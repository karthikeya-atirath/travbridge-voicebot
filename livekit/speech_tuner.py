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

from livekit.agents import AgentSession

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
