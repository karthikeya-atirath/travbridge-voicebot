"""Per-speech-type STT/TTS parameter table + single apply dispatcher.

This project's voice agent keeps Deepgram as its STT provider, so the STT
knobs below are Deepgram's actual `update_options()` params (endpointing_ms,
no_delay) rather than Sarvam's VAD-frame params — same intent (how
aggressively to decide the customer stopped talking), mapped to the
provider actually in use. TTS knobs are Sarvam's, unchanged.

NOTE: calling STT.update_options() forces Deepgram to reconnect its
websocket, which cuts off any in-flight recognition. Only call apply_category
between turns (never while the customer is mid-utterance).
"""

from __future__ import annotations

import time

from livekit.agents import AgentSession
from livekit.agents.metrics import EOUMetrics

from speech_classifier import SpeechClassifier, TurnSignal
from app_logger import applog

CATEGORY_CONFIGS = {
    "fast_fluent": {
        "stt": {"endpointing_ms": 150, "no_delay": True},
        "tts": {"pace": 1.15, "temperature": 0.55},
    },
    "slow_steady": {
        "stt": {"endpointing_ms": 700, "no_delay": False},
        "tts": {"pace": 0.90, "temperature": 0.35},
    },
    "micro_pause": {
        "stt": {"endpointing_ms": 450, "no_delay": False},
        "tts": {"pace": 0.97, "temperature": 0.45},
    },
    "hesitant": {
        "stt": {"endpointing_ms": 900, "no_delay": False},
        "tts": {"pace": 0.87, "temperature": 0.30},
    },
    "interrupter": {
        "stt": {"endpointing_ms": 150, "no_delay": True},
        "tts": {"pace": 1.05, "temperature": 0.50},
    },
}


def apply_category(session: AgentSession, category: str) -> None:
    """Push a category's STT + TTS params into the live session."""
    config = CATEGORY_CONFIGS[category]
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
        pending_word_count = len(event.transcript.split())
        pending_transcript = event.transcript

    @session.on("user_state_changed")
    def _on_user_state(event):
        nonlocal turn_start_time
        if getattr(event, "new_state", None) == "speaking":
            if agent_speaking["value"]:
                pending_signal.interrupted = True
            # Only stamp the *first* speaking-onset of a turn — VAD can flicker
            # speaking/listening on brief mid-sentence pauses, and resetting the
            # clock on those would undercount the turn's real duration.
            if turn_start_time is None:
                turn_start_time = time.time()

    @session.on("metrics_collected")
    def _on_metrics(event):
        nonlocal pending_signal, pending_word_count, pending_transcript, turn_index, turn_start_time
        m = event.metrics
        if isinstance(m, EOUMetrics):
            if turn_start_time is not None:
                duration = time.time() - turn_start_time
                if duration > 0 and pending_word_count > 0:
                    pending_signal.pace = pending_word_count / duration
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

                config = CATEGORY_CONFIGS.get(label)
                if applied and config:
                    outcome = f"APPLIED -> stt={config['stt']}, tts={config['tts']}"
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
