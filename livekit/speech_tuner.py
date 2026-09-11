"""Per-speech-type STT/TTS parameter table + single apply dispatcher.

Values below are the exact fast_aggressive / slow_steady / micro_pauses /
interruption_heavy figures supplied for this project (midpoint picked
wherever the source gave a range; micro_pauses additionally absorbed the
former "hesitant" category's figures — see speech_classifier.py), split
across groups by what each one actually controls in this stack:

- endpointing: LiveKit's own turn-taking wait time. Present in
  CATEGORY_CONFIGS only to seed AgentSession's initial min_delay/max_delay
  at session construction (agent_stt_llm_tts_v1.py, from SPEECH_PROFILE's
  starting category) — apply_category() does NOT re-push either value on
  later reclassifications. The session is built with endpointing
  mode="dynamic" (livekit.agents.voice.endpointing.DynamicEndpointing),
  which continuously learns a per-caller pause floor from real pause
  behavior via an exponential moving average, and specifically raises that
  floor when it observes a pause immediately followed by an interruption —
  exactly the micro-pause/false-EOT pattern these categories are named for.
  DynamicEndpointing.update_options() re-seeds that EMA from scratch, so
  calling AgentSession.update_options(endpointing_opts=...) on every 5-turn
  reclassification would throw away real, continuously-learned per-caller
  signal in favor of a coarser static guess every time the tuner rechecks —
  fighting the exact mechanism meant to solve the micro-pause case.
  max_delay itself is a fixed ceiling DynamicEndpointing never learns/
  adjusts (it only clamps min_delay from above), so unlike min_delay there
  would be no EMA lost by re-pushing it alone later — it's just left alone
  here too, for the same reason apply_category doesn't split "reapply half
  of endpointing" into its own special case. STT/TTS have no equivalent
  self-adjusting mechanism, so those stay under this tuner's live control.
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

import time

from livekit.agents import AgentSession
from livekit.agents.metrics import EOUMetrics

from speech_classifier import SpeechClassifier, TurnSignal
from interruption_guard import InterruptionGuard
from app_logger import applog

# STT endpointing floor: Deepgram treats a silence gap of this length as the
# end of a speech chunk (not necessarily the end of the user's utterance —
# see interruption_guard.py's note above EXPLANATION_INTERRUPT_THRESHOLD).
# Below ~200ms this fires on ordinary mid-sentence breathing pauses, handing
# the pipeline a stream of spurious is_final=True chunks that both the
# interruption guard and the LLM turn boundary then have to second-guess.
# fast_aggressive/interruption_heavy used to go as low as 125ms/75ms to
# minimize latency for fast talkers and frequent interrupters, but that
# aggressiveness was a direct contributor to false interruptions — held at
# 200ms across every profile until STT/interruption stability improves.
_MIN_STT_ENDPOINTING_MS = 200

CATEGORY_CONFIGS = {
    "fast_aggressive": {
        # max_delay=0.75: a confident/fast talker rarely produces a turn the
        # semantic turn detector is genuinely unsure about, so there's
        # little reason to make even an *uncertain* turn wait as long as a
        # hesitant caller's. Lower than slow_steady/micro_pauses' ceilings,
        # matching this profile's low min_delay.
        "endpointing": {"min_delay": 0.175, "max_delay": 0.75},
        "tts": {"pace": 1.025},
        "stt": {"endpointing_ms": _MIN_STT_ENDPOINTING_MS, "interim_results": True, "no_delay": True},
    },
    "slow_steady": {
        "endpointing": {"min_delay": 0.25, "max_delay": 1.05},
        "tts": {"pace": 0.90},
        "stt": {"endpointing_ms": 200, "interim_results": True, "no_delay": True},
    },
    # Merged with the former "hesitant" category (see speech_classifier.py's
    # _SIGNATURES comment) — values below are the midpoint of the old
    # micro_pauses/hesitant configs, so both "several short frequent
    # pauses" and "one long halting pause" callers get a patience level
    # between the two rather than either extreme.
    "micro_pauses": {
        # Highest ceiling of the four: this is exactly the caller
        # DynamicEndpointing's max_delay exists to protect — one who needs
        # the endpointer to wait out a genuine mid-utterance pause instead
        # of releasing a truncated turn.
        "endpointing": {"min_delay": 0.35, "max_delay": 1.25},
        "tts": {"pace": 0.90},
        "stt": {"endpointing_ms": 225, "interim_results": True, "no_delay": True},
    },
    "interruption_heavy": {
        # Shortest ceiling of the four: this caller almost never produces a
        # pause worth waiting out, so an "uncertain" turn should still be
        # released quickly rather than held for as long as everyone else's.
        "endpointing": {"min_delay": 0.175, "max_delay": 0.65},
        "tts": {"pace": 1.025},
        "stt": {"endpointing_ms": _MIN_STT_ENDPOINTING_MS, "interim_results": True, "no_delay": True},
    },
}


CLASSIFIER_PROFILE_MAP: dict[str, str] = {
    "fast_fluent": "fast_aggressive",
    "micro_pause": "micro_pauses",
    "interrupter": "interruption_heavy",
}


def _dynamic_endpointing_floor(session: AgentSession) -> float | None:
    """Best-effort peek at DynamicEndpointing's live-learned min_delay floor.

    There's no public API for this — AgentSession only exposes the static
    endpointing config it was built with, not the EMA-adjusted value the
    module docstring above describes DynamicEndpointing continuously
    learning per-caller. The learned value only lives on the private
    session._activity._audio_recognition._endpointing (see
    livekit.agents.voice.endpointing.DynamicEndpointing.min_delay). Guarded
    with getattr at every hop so a livekit-agents upgrade that moves this
    just turns the tuner log's floor field off instead of raising.
    """
    activity = getattr(session, "_activity", None)
    audio_recognition = getattr(activity, "_audio_recognition", None)
    endpointing = getattr(audio_recognition, "_endpointing", None)
    return getattr(endpointing, "min_delay", None)


def _dynamic_endpointing_ceiling(session: AgentSession) -> float | None:
    """Best-effort peek at the live session's actual max_delay ceiling.

    Unlike min_delay, max_delay is never learned/adjusted by
    DynamicEndpointing (see the module docstring) — it's a fixed value set
    once at session construction from whichever profile was active then
    (CATEGORY_CONFIGS[profile]["endpointing"]["max_delay"]). speech_
    classifier.py's ceiling_rate needs exactly that value to decide whether
    a turn's pause "hit the ceiling" — hardcoding/duplicating a separate
    constant for this would silently go wrong the moment the initial
    profile isn't the one that constant assumed (now that max_delay varies
    per profile instead of being one global figure). Reading it directly
    off the live session, the same way _dynamic_endpointing_floor already
    does for min_delay, keeps it correct by construction instead of by
    convention. Guarded with getattr at every hop for the same reason as
    that function: a livekit-agents upgrade that moves this attribute
    should degrade the tuner's ceiling_rate signal, not crash it.
    """
    activity = getattr(session, "_activity", None)
    audio_recognition = getattr(activity, "_audio_recognition", None)
    endpointing = getattr(audio_recognition, "_endpointing", None)
    return getattr(endpointing, "max_delay", None)


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
    initial_profile: str = "slow_steady",
    interruption_guard: InterruptionGuard | None = None,
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

    `interruption_guard`, when given, is the single source of truth for
    whether a turn was a genuine interruption: any raw overlap (the user's
    VAD state flips to "speaking" while the agent is talking) used to be
    counted as one outright, which fed the "interrupter" speech type off of
    backchannel noise and ordinary crosstalk the guard itself would have
    IGNOREd/WAITed on rather than actually cutting the agent off for. A
    turn is only marked interrupted here if the guard's own decision logic
    (classify_interruption, downstream of the TTS-echo filter and turn
    evolution's redundancy check) actually fired a real interrupt.
    """
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
    label_tag = session_label or "session"
    active_profile = {"value": initial_profile}
    last_endpointing_floor = {"value": None}

    if interruption_guard is not None:
        def _on_guard_event(kind: str, data: dict) -> None:
            if kind == "decision" and data.get("decision") == "interrupt":
                genuine_interrupt_in_turn["value"] = True

        interruption_guard.add_observer(_on_guard_event)

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
        pending_word_count = len(event.transcript.split())
        pending_transcript = event.transcript

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal speech_segment_started_at, accumulated_speech_duration
        now = time.monotonic()
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking" and speech_segment_started_at is None:
            speech_segment_started_at = now
        elif new_state in ("listening", "thinking", "away") and speech_segment_started_at is not None:
            accumulated_speech_duration += max(0.0, now - speech_segment_started_at)
            speech_segment_started_at = None

    @session.on("metrics_collected")
    def _on_metrics(event):
        nonlocal pending_signal, pending_word_count, pending_transcript, turn_index
        nonlocal speech_segment_started_at, accumulated_speech_duration
        m = event.metrics
        if not isinstance(m, EOUMetrics):
            return
        if not pending_transcript.strip():
            speech_segment_started_at = None
            accumulated_speech_duration = 0.0
            genuine_interrupt_in_turn["value"] = False
            pending_signal = TurnSignal()
            applog.info(f"[SPEECH TUNER][{label_tag}] ignored empty/duplicate EOU metric")
            return

        pending_signal.interrupted = genuine_interrupt_in_turn["value"]
        if speech_segment_started_at is not None:
            accumulated_speech_duration += max(0.0, time.monotonic() - speech_segment_started_at)
            speech_segment_started_at = None
        pace_valid = pending_word_count > 0 and accumulated_speech_duration >= 0.10
        if pace_valid:
            pending_signal.pace = pending_word_count / accumulated_speech_duration
        pending_signal.pause = max(0.0, m.end_of_utterance_delay)
        turn_index += 1

        current_floor = _dynamic_endpointing_floor(session)
        prev_floor = last_endpointing_floor["value"]
        if current_floor is None:
            floor_str = "n/a"
        elif prev_floor is None:
            floor_str = f"{current_floor:.3f}s (initial)"
        elif abs(current_floor - prev_floor) < 0.0005:
            floor_str = f"{current_floor:.3f}s (unchanged)"
        else:
            sign = "+" if current_floor > prev_floor else ""
            floor_str = f"{current_floor:.3f}s ({prev_floor:.3f}s {sign}{current_floor - prev_floor:.3f}s)"
        last_endpointing_floor["value"] = current_floor

        applog.info(
            f"[SPEECH TUNER][{label_tag}][turn {turn_index}] "
            f"speech_duration={accumulated_speech_duration:.3f}s pace={pending_signal.pace:.2f} "
            f"pace_valid={pace_valid} eou_delay={pending_signal.pause:.3f}s "
            f"transcription_delay={m.transcription_delay:.3f}s "
            f"callback_delay={m.on_user_turn_completed_delay:.3f}s "
            f"interrupted={pending_signal.interrupted} mix_ratio={pending_signal.mix_ratio:.2f} "
            f"dynamic_endpointing_floor={floor_str} "
            f"transcript={pending_transcript!r}"
        )

        label = classifier.add_turn(pending_signal)
        current_profile = active_profile["value"]

        pending_signal = TurnSignal()
        pending_word_count = 0
        pending_transcript = ""
        genuine_interrupt_in_turn["value"] = False
        accumulated_speech_duration = 0.0

        if not label:
            return
        applog.info(f"[SPEECH TUNER][{label_tag}] switching profile {current_profile} -> {label}")
        try:
            apply_category(session, label)
            active_profile["value"] = label
            profile = CLASSIFIER_PROFILE_MAP.get(label, label)
            config = CATEGORY_CONFIGS[profile]
            outcome = (
                f"APPLIED -> tts_effect=immediate tts={config['tts']}, "
                f"stt_effect=next_websocket stt={config['stt']}, "
                f"endpointing_effect=dynamic_unchanged"
            )
        except Exception as error:
            outcome = f"FAILED TO APPLY -> {error}"
            applog.exception(f"[SPEECH TUNER][{label_tag}] apply_category failed")
        avg_str = ", ".join(f"{key}={value:.3f}" for key, value in classifier.last_vector.items())
        dist_str = ", ".join(
            f"{key}={value:.3f}"
            for key, value in sorted(classifier.last_distances.items(), key=lambda item: item[1])
        )
        applog.info(
            f"[SPEECH TUNER][{label_tag}][classify @ turn {turn_index}] "
            f"robust=({avg_str}) distances=({dist_str}) -> label={label} | {outcome}"
        )
