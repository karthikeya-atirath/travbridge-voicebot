"""Two broad caller policies plus per-caller dynamic endpointing.

The profile classifier selects a responsive or patient policy. The policy
sets the VAD silence threshold, TTS pace, STT settings, and DynamicEndpointing
ceiling. DynamicEndpointing keeps its learned minimum delay inside that
ceiling. Updating only max_delay on the live endpointing object preserves its
EMA; AgentSession.update_options(endpointing_opts=...) would replace that
object and discard the learned floor.
"""

from __future__ import annotations

import re
import time

from livekit.agents import AgentSession
from livekit.agents.metrics import EOUMetrics

from speech_classifier import SpeechClassifier, TurnSignal
from interruption_guard import InterruptionGuard
from app_logger import applog

_STT_ENDPOINTING_MS = 200

CATEGORY_CONFIGS = {
    "responsive": {
        "endpointing": {"min_delay": 0.175, "max_delay": 0.60, "alpha": 0.7},
        "vad": {"min_silence_duration": 0.40},
        "tts": {"pace": 1.025},
        # Speculative LLM start only for callers who rarely pause mid-thought;
        # for patient callers it drafts replies from half-finished sentences.
        "preemptive_generation": {"enabled": True, "preemptive_tts": False},
        "stt": {"endpointing_ms": _STT_ENDPOINTING_MS, "interim_results": True, "no_delay": True},
    },
    "patient": {
        "endpointing": {"min_delay": 0.35, "max_delay": 1.00, "alpha": 0.8},
        "vad": {"min_silence_duration": 0.60},
        "tts": {"pace": 0.90},
        "preemptive_generation": {"enabled": False, "preemptive_tts": False},
        "stt": {"endpointing_ms": _STT_ENDPOINTING_MS, "interim_results": True, "no_delay": True},
    },
}

# Accept previous deployment values while collapsing them onto the two
# policies. This avoids breaking existing SPEECH_PROFILE environment values.
_PROFILE_ALIASES = {
    "responsive": "responsive",
    "fast_aggressive": "responsive",
    "fast_fluent": "responsive",
    "interruption_heavy": "responsive",
    "interrupter": "responsive",
    "patient": "patient",
    "slow_steady": "patient",
    "micro_pauses": "patient",
    "micro_pause": "patient",
}


def resolve_profile(profile: str) -> str:
    return _PROFILE_ALIASES.get(profile.strip().lower(), "patient")


def _live_endpointing(session: AgentSession):
    """Best-effort access to the active endpointing controller."""
    activity = getattr(session, "_activity", None)
    recognition = getattr(activity, "_audio_recognition", None)
    return getattr(recognition, "_endpointing", None)


def _dynamic_endpointing_floor(session: AgentSession) -> float | None:
    endpointing = _live_endpointing(session)
    return getattr(endpointing, "min_delay", None)



def current_floor_for_resume(session: AgentSession) -> float | None:
    return _dynamic_endpointing_floor(session)

def _dynamic_endpointing_ceiling(session: AgentSession) -> float | None:
    endpointing = _live_endpointing(session)
    return getattr(endpointing, "max_delay", None)


def _profile_params(profile: str) -> dict:
    config = CATEGORY_CONFIGS.get(resolve_profile(profile))
    if config is None:
        return {}
    return {
        "tts_pace": config["tts"].get("pace"),
        "stt_endpointing_ms": config["stt"].get("endpointing_ms"),
        "endpointing_max_delay": config["endpointing"].get("max_delay"),
        "vad_min_silence": config["vad"].get("min_silence_duration"),
    }


def apply_category(session: AgentSession, category: str) -> bool:
    """Apply one policy while preserving DynamicEndpointing's learned floor.

    Returns whether the live endpointing ceiling was updated. VAD, STT, and
    TTS options are applied independently of that best-effort private API.
    """
    profile = resolve_profile(category)
    config = CATEGORY_CONFIGS[profile]
    endpointing = _live_endpointing(session)
    endpoint_updated = False
    update_endpointing = getattr(endpointing, "update_options", None)
    if callable(update_endpointing):
        # min_delay is deliberately not passed: update_options(min_delay=...)
        # resets the learned EMA. max_delay/alpha leave the learned value intact.
        update_endpointing(
            max_delay=config["endpointing"]["max_delay"],
            alpha=config["endpointing"]["alpha"],
        )
        endpoint_updated = True

    # AgentActivity re-reads session.options.preemptive_generation on every
    # preemptive attempt, so updating it in place takes effect on the next turn.
    session.options.preemptive_generation.update(config["preemptive_generation"])

    if session.vad is not None:
        session.vad.update_options(**config["vad"])
    if session.stt is not None:
        session.stt.update_options(**config["stt"])
    if session.tts is not None:
        session.tts.update_options(**config["tts"])
    return endpoint_updated


def attach_speech_tuner(
    session: AgentSession,
    window: int = 5,
    session_label: str | None = None,
    initial_profile: str = "patient",
    interruption_guard: InterruptionGuard | None = None,
    turn_logger=None,
) -> None:
    """Tune broad caller policy every window, with hysteresis for stability.

    Patient mode is promoted immediately after two turns in a window reach
    the current endpointing ceiling. Otherwise, two consecutive classifier
    windows are required before changing policy, preventing noisy per-window
    flips. A strong ceiling-hit pattern locks the call into patient mode.
    """
    initial_profile = resolve_profile(initial_profile)
    classifier = SpeechClassifier(
        window=window,
        max_endpointing_delay=_dynamic_endpointing_ceiling(session),
    )
    pending_signal = TurnSignal()
    pending_word_count = 0
    pending_transcript = ""
    turn_index = 0
    speech_segment_started_at: float | None = None
    accumulated_speech_duration = 0.0
    genuine_interrupt_in_turn = {"value": False}
    agent_speaking = {"value": False}
    last_eou_delay = {"value": None}
    last_eou_at = {"value": None}
    label_tag = session_label or "session"
    active_profile = {"value": initial_profile}
    candidate_profile: str | None = None
    candidate_windows = 0
    patient_mode_locked = False
    suppressed_filler_texts: set[str] = set()

    if interruption_guard is not None:
        def _on_guard_event(kind: str, data: dict) -> None:
            if kind == "decision" and data.get("decision") == "interrupt":
                genuine_interrupt_in_turn["value"] = True
            elif kind == "filler_check" and data.get("verdict") == "non_answer_suppressed":
                suppressed_filler_texts.add(_norm(data.get("text", "")))
            elif kind == "recovery" and data.get("suppress_reason") == "non_answer_filler":
                suppressed_filler_texts.add(_norm(data.get("text", "")))

        interruption_guard.add_observer(_on_guard_event)

    @session.on("agent_state_changed")
    def _on_agent_state(event):
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking":
            agent_speaking["value"] = True
        elif new_state in ("idle", "listening", "thinking"):
            agent_speaking["value"] = False

    @session.on("user_input_transcribed")
    def _on_transcript(event):
        nonlocal pending_word_count, pending_transcript
        if not event.is_final:
            return
        pending_signal.mix_ratio = SpeechClassifier.mix_ratio(event.transcript)
        pending_word_count = len(event.transcript.split())
        pending_transcript = event.transcript

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal speech_segment_started_at, accumulated_speech_duration
        now = time.monotonic()
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking" and speech_segment_started_at is None:
            speech_segment_started_at = now
            previous_eou_at = last_eou_at["value"]
            previous_eou_delay = last_eou_delay["value"]
            near_previous_turn_end = (
                previous_eou_at is not None
                and now - previous_eou_at <= 2.0
                and previous_eou_delay is not None
                and current_floor_for_resume(session) is not None
                and previous_eou_delay >= 0.8 * current_floor_for_resume(session)
            )
            if agent_speaking["value"] and near_previous_turn_end:
                pending_signal.continued_after_bot = True
        elif new_state in ("listening", "thinking", "away") and speech_segment_started_at is not None:
            accumulated_speech_duration += max(0.0, now - speech_segment_started_at)
            speech_segment_started_at = None

    @session.on("metrics_collected")
    def _on_metrics(event):
        nonlocal pending_signal, pending_word_count, pending_transcript, turn_index
        nonlocal speech_segment_started_at, accumulated_speech_duration
        nonlocal candidate_profile, candidate_windows, patient_mode_locked
        m = event.metrics
        if not isinstance(m, EOUMetrics):
            return
        if not pending_transcript.strip():
            speech_segment_started_at = None
            accumulated_speech_duration = 0.0
            genuine_interrupt_in_turn["value"] = False
            pending_signal = TurnSignal()
            return

        filler_key = _norm(pending_transcript)
        if filler_key in suppressed_filler_texts:
            suppressed_filler_texts.discard(filler_key)
            applog.info(
                f"[SPEECH TUNER][{label_tag}] skipped non-answer filler turn "
                f"{pending_transcript!r} (not counted toward classification)"
            )
            speech_segment_started_at = None
            accumulated_speech_duration = 0.0
            genuine_interrupt_in_turn["value"] = False
            pending_signal = TurnSignal()
            pending_word_count = 0
            pending_transcript = ""
            return
        suppressed_filler_texts.clear()

        pending_signal.interrupted = genuine_interrupt_in_turn["value"]
        if speech_segment_started_at is not None:
            accumulated_speech_duration += max(0.0, time.monotonic() - speech_segment_started_at)
            speech_segment_started_at = None
        if pending_word_count > 0 and accumulated_speech_duration >= 0.10:
            pending_signal.pace = pending_word_count / accumulated_speech_duration
        pending_signal.pause = max(0.0, m.end_of_utterance_delay)
        last_eou_delay["value"] = pending_signal.pause
        last_eou_at["value"] = time.monotonic()
        turn_index += 1

        current_floor = _dynamic_endpointing_floor(session)
        current_ceiling = _dynamic_endpointing_ceiling(session)
        classifier.set_max_endpointing_delay(current_ceiling)
        _calc_start = time.perf_counter()
        label = classifier.add_turn(pending_signal)
        calculation_time = time.perf_counter() - _calc_start
        current_profile = active_profile["value"]
        apply_profile: str | None = None
        strong_patient_evidence = bool(
            label == "patient"
            and (
                classifier.last_vector.get("ceiling_rate", 0.0) >= 0.4
                or classifier.last_vector.get("continuation_rate", 0.0) >= 0.4
            )
        )

        if label:
            if label == candidate_profile:
                candidate_windows += 1
            else:
                candidate_profile = label
                candidate_windows = 1
            if strong_patient_evidence:
                apply_profile = "patient"
            elif candidate_windows >= 2 and not patient_mode_locked:
                apply_profile = label

        next_profile = apply_profile or current_profile
        floor_str = f"{current_floor:.3f}s" if current_floor is not None else "n/a"
        applog.info(
            f"[SPEECH TUNER][{label_tag}][turn {turn_index}] "
            f"pace={pending_signal.pace:.2f}wps pause={pending_signal.pause:.3f}s "
            f"interrupted={pending_signal.interrupted} endpointing_floor={floor_str} "
            f"classifier={label or 'pending'} policy_candidate="
            f"{candidate_profile or 'n/a'} candidate_windows={candidate_windows} "
            f"active_policy={current_profile} ceiling_rate="
            f"{classifier.last_vector.get('ceiling_rate', 0.0):.2f} "
            f"continuation_rate={classifier.last_vector.get('continuation_rate', 0.0):.2f}"
        )
        if turn_logger is not None:
            turn_logger.record_speech_tuning(
                pace=pending_signal.pace,
                pause=pending_signal.pause,
                mix_ratio=pending_signal.mix_ratio,
                endpointing_floor=current_floor,
                endpointing_ceiling=current_ceiling,
                profile_used=current_profile,
                next_profile=next_profile,
                params_used=_profile_params(current_profile),
                params_next=_profile_params(next_profile),
                calculation_time=calculation_time,
            )

        pending_signal = TurnSignal()
        pending_word_count = 0
        pending_transcript = ""
        genuine_interrupt_in_turn["value"] = False
        accumulated_speech_duration = 0.0

        if apply_profile is None:
            return
        if apply_profile == current_profile:
            if apply_profile == "patient" and (
                strong_patient_evidence or candidate_windows >= 2
            ):
                patient_mode_locked = True
            return
        try:
            endpoint_updated = apply_category(session, apply_profile)
            active_profile["value"] = apply_profile
            if apply_profile == "patient":
                patient_mode_locked = True
            config = CATEGORY_CONFIGS[apply_profile]
            endpoint_status = (
                f"endpointing_max={config['endpointing']['max_delay']:.2f}s"
                if endpoint_updated
                else "endpointing_max=unchanged (live controller unavailable)"
            )
            applog.info(
                f"[SPEECH TUNER][{label_tag}][turn {turn_index}] "
                f"policy {current_profile} -> {apply_profile} | "
                f"vad={config['vad']} stt={config['stt']} tts={config['tts']} "
                f"{endpoint_status} learned_floor_preserved=True"
            )
        except Exception as error:
            if turn_logger is not None:
                turn_logger.record_speech_tuning_error(str(error))
            applog.exception(f"[SPEECH TUNER][{label_tag}] apply_category failed")

def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text or "").strip().lower()


