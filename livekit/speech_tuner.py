"""Per-speech-type STT/TTS parameter table + single apply dispatcher.

Values below are the exact fast_aggressive / slow_steady / micro_pauses /
hesitant / interruption_heavy figures supplied for this project (midpoint
picked wherever the source gave a range), split across groups by what each
one actually controls in this stack:

- endpointing: LiveKit's own turn-taking wait time. Present in
  CATEGORY_CONFIGS only to seed AgentSession's initial min_delay at
  session construction (agent_stt_llm_tts_v1.py) — apply_category() does
  NOT re-push it. The session is built with endpointing mode="dynamic"
  (livekit.agents.voice.endpointing.DynamicEndpointing), which continuously
  learns a per-caller pause floor from real pause behavior via an
  exponential moving average, and specifically raises that floor when it
  observes a pause immediately followed by an interruption — exactly the
  micro-pause/false-EOT pattern these categories are named for.
  DynamicEndpointing.update_options() re-seeds that EMA from scratch, so
  calling AgentSession.update_options(endpointing_opts=...) on every 5-turn
  reclassification would throw away real, continuously-learned per-caller
  signal in favor of a coarser static guess every time the tuner rechecks —
  fighting the exact mechanism meant to solve the micro-pause case. STT/TTS
  have no equivalent self-adjusting mechanism, so those stay under this
  tuner's live control.
- interruption settings are intentionally not tuned here. The interruption
  guard is their single owner, so native VAD cannot race semantic filler handling.
- tts: Sarvam's speaking pace, applied via ``TTS.update_options()``.
- stt: Deepgram nova-2 knobs, pinned to nova-2 rather than nova-3 to stay on
  the account's free tier (see agent_stt_llm_tts_v1.py — this means no
  real-time Hindi/English code-switching support, a nova-3-only feature).
  NOTE the source table's ``utterance_end_ms`` has no equivalent on this
  LiveKit plugin wrapper (livekit-plugins-deepgram 1.5.13) for any Deepgram
  model — it only exists on Deepgram's newer Flux model (``deepgram.STTv2``),
  which this project isn't using, so it's intentionally left out rather than
  guessed at.

NOTE: calling STT.update_options() only takes effect on Deepgram's next
websocket reconnect, and forcing a reconnect mid-utterance would cut off
in-flight recognition. Only call apply_category between turns (never while
the customer is mid-utterance).
"""

from __future__ import annotations

import re
import time

from livekit.agents import AgentSession
from livekit.agents.metrics import EOUMetrics

from speech_classifier import SpeechClassifier, TurnSignal
from app_logger import applog

_TUNER_BACKCHANNELS = frozenset({
    "hm", "hmm", "hmmm", "mm", "mmm", "uh", "um", "uh huh", "uh-huh",
    "mm hmm", "mm-hmm", "ok", "okay", "yeah", "yes", "right", "alright",
    "understood", "i see", "got it", "haan", "han", "हम्म", "हां", "हाँ", "ठीक है",
})


def _is_tuner_backchannel(text: str) -> bool:
    normalized = " ".join(re.sub(r"[^\w\u0900-\u097f-]+", " ", text.casefold()).split())
    return normalized in _TUNER_BACKCHANNELS

CATEGORY_CONFIGS = {
    "fast_aggressive": {
        "endpointing": {"min_delay": 0.175},
        "tts": {"pace": 1.025},
        "stt": {"endpointing_ms": 125, "interim_results": True, "no_delay": True},
    },
    "slow_steady": {
        "endpointing": {"min_delay": 0.25},
        "tts": {"pace": 0.90},
        "stt": {"endpointing_ms": 200, "interim_results": True, "no_delay": True},
    },
    "micro_pauses": {
        "endpointing": {"min_delay": 0.30},
        "tts": {"pace": 0.925},
        "stt": {"endpointing_ms": 200, "interim_results": True, "no_delay": True},
    },
    "hesitant": {
        "endpointing": {"min_delay": 0.40},
        "tts": {"pace": 0.875},
        "stt": {"endpointing_ms": 250, "interim_results": True, "no_delay": True},
    },
    "interruption_heavy": {
        "endpointing": {"min_delay": 0.175},
        "tts": {"pace": 1.025},
        "stt": {"endpointing_ms": 75, "interim_results": True, "no_delay": True},
    },
}


CLASSIFIER_PROFILE_MAP: dict[str, str] = {
    "fast_fluent": "fast_aggressive",
    "micro_pause": "micro_pauses",
    "interrupter": "interruption_heavy",
}


def apply_category(session: AgentSession, category: str) -> None:
    """Push a category's STT/TTS params into the live session.

    Deliberately does not touch endpointing_opts — see the module docstring:
    that would reset AgentSession's DynamicEndpointing EMA on every
    reclassification, discarding the per-caller pause floor it has been
    continuously learning since session start.
    """
    profile = CLASSIFIER_PROFILE_MAP.get(category, category)
    config = CATEGORY_CONFIGS[profile]
    if session.stt is not None:
        session.stt.update_options(**config["stt"])
    if session.tts is not None:
        session.tts.update_options(**config["tts"])


def attach_speech_tuner(
    session: AgentSession,
    window: int = 5,
    session_label: str | None = None,
) -> None:
    """Wire up rolling speech-type classification and live STT/TTS retuning
    on an AgentSession. Every `window` completed user turns, classifies the
    caller's recent speech pattern (pace, pause, interrupts) and pushes the
    matching profile from CATEGORY_CONFIGS via apply_category.

    Only meaningful for sessions using Deepgram STT + Sarvam TTS, since
    those are the providers CATEGORY_CONFIGS targets.

    Every turn's signal and every reclassification's math + outcome is
    logged via applog (tagged [SPEECH TUNER]) for auditing the auto-tuning
    behavior — this never affects tuning behavior itself.
    """
    classifier = SpeechClassifier(window=window)
    pending_signal = TurnSignal()
    pending_word_count = 0
    pending_transcript = ""
    turn_index = 0
    turn_start_time: float | None = None
    pending_overlap = False
    agent_speaking = {"value": False}
    label_tag = session_label or "session"

    @session.on("agent_state_changed")
    def _on_agent_state(event):
        new = getattr(event, "new_state", None)
        if new == "speaking":
            agent_speaking["value"] = True
        elif new in ("idle", "listening", "thinking"):
            agent_speaking["value"] = False

    @session.on("user_input_transcribed")
    def _on_transcript(event):
        nonlocal pending_word_count, pending_transcript
        if not event.is_final:
            return
        pending_signal.mix_ratio = SpeechClassifier.mix_ratio(event.transcript)
        pending_signal.interrupted = pending_overlap and not _is_tuner_backchannel(event.transcript)
        pending_word_count = len(event.transcript.split())
        pending_transcript = event.transcript

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal turn_start_time, pending_overlap
        if getattr(event, "new_state", None) == "speaking":
            if agent_speaking["value"]:
                pending_overlap = True
            # Only stamp the *first* speaking-onset of a turn — VAD can flicker
            # speaking/listening on brief mid-sentence pauses, and resetting the
            # clock on those would undercount the turn's real duration.
            if turn_start_time is None:
                turn_start_time = time.time()

    @session.on("metrics_collected")
    def _on_metrics(event):
        nonlocal pending_signal, pending_word_count, pending_transcript, turn_index, turn_start_time, pending_overlap
        m = event.metrics
        if isinstance(m, EOUMetrics):
            # Some sessions emit duplicate/empty EOU metrics. They are not user
            # turns and must not advance the five-turn classification window.
            if not pending_transcript.strip():
                turn_start_time = None
                pending_overlap = False
                pending_signal = TurnSignal()
                return

            if turn_start_time is not None:
                elapsed = time.time() - turn_start_time
                speech_duration = max(0.05, elapsed - m.end_of_utterance_delay)
                if pending_word_count > 0:
                    pending_signal.pace = pending_word_count / speech_duration
                turn_start_time = None
            pending_signal.pause = m.end_of_utterance_delay
            turn_index += 1
            applog.info(
                f"[SPEECH TUNER][{label_tag}][turn {turn_index}] "
                f"pace={pending_signal.pace:.2f} pause={pending_signal.pause:.2f} "
                f"interrupted={pending_signal.interrupted} "
                f"mix_ratio={pending_signal.mix_ratio:.2f} "
                f"transcript={pending_transcript!r}"
            )

            label = classifier.add_turn(pending_signal)
            pending_signal = TurnSignal()
            pending_word_count = 0
            pending_transcript = ""
            pending_overlap = False

            if label:
                applied = False
                error = None
                skip_reason = None
                if agent_speaking["value"]:
                    skip_reason = "agent was speaking"
                else:
                    applog.info(f"[SPEECH TUNER][{label_tag}] switching profile -> {label}")
                    try:
                        apply_category(session, label)
                        applied = True
                    except Exception as e:
                        applog.error(f"[SPEECH TUNER][{label_tag}] apply_category failed: {e}")
                        error = e

                profile = CLASSIFIER_PROFILE_MAP.get(label, label)
                config = CATEGORY_CONFIGS.get(profile)
                if applied and config:
                    outcome = (
                        f"APPLIED -> stt={config['stt']}, tts={config['tts']} "
                        f"(endpointing left to DynamicEndpointing's own learning)"
                    )
                elif error is not None:
                    outcome = f"FAILED TO APPLY -> {error}"
                elif skip_reason:
                    outcome = f"NOT APPLIED ({skip_reason})"
                else:
                    outcome = "NOT APPLIED"
                avg_str = ", ".join(f"{k}={v:.3f}" for k, v in classifier.last_vector.items())
                dist_str = ", ".join(
                    f"{k}={v:.3f}"
                    for k, v in sorted(classifier.last_distances.items(), key=lambda kv: kv[1])
                )
                applog.info(
                    f"[SPEECH TUNER][{label_tag}][classify @ turn {turn_index}] "
                    f"avg=({avg_str}) distances=({dist_str}) -> label={label} | {outcome}"
                )
