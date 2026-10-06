"""Single place to switch the voice agent's turn-taking features on or off.

Edit the values below, restart the worker, done. Nothing here touches STT,
LLM or TTS settings. This file is read directly (not from .env), so a stray
shell variable or dotenv override can never silently change the behaviour.

Every call logs the values it started with ("[CONFIG] ...") so app_voice.log
always shows which switches were on.
"""

# ── Which end-of-turn detector decides that the caller has finished ──────────
#   "smart_turn"   Smart Turn v3.2 audio model. Endpointing is FIXED:
#                  min_delay = wait after a "complete" verdict,
#                  max_delay = ceiling after an "incomplete" verdict.
#   "livekit_mini" LiveKit turn-detector v1-mini with DYNAMIC endpointing
#                  (learns a per-caller minimum delay inside max_delay).
#   "vad"          No model: VAD silence + dynamic endpointing only.
TURN_DETECTOR = "livekit_mini"

# Smart Turn only: P(turn complete) needed to commit on the short path.
# "0.5" for one value, or per language: "hi=0.45,en=0.5".
SMART_TURN_THRESHOLD = "0.5"

# ── Interruption guard ────────────────────────────────────────────────────────
# ON : the guard is the only thing allowed to interrupt the bot (it classifies
#      overlapping speech: backchannel vs. real interruption vs. command).
# OFF: LiveKit's built-in interruption handling takes over (any caller speech
#      past ~0.5s stops the bot); no guard, no filler/redundancy vetoes.
INTERRUPTION_GUARD = True

# ── Semantic check (sentence-embedding "is this just a repeat?" check) ───────
# Used by the guard on overlapping speech and on finalized turns. Needs
# INTERRUPTION_GUARD = True. OFF skips the embedding model entirely (it is
# not even loaded at worker start-up) and treats all overlapping speech as new.
SEMANTIC_CHECK = True

# ── Caller classification + tuning ───────────────────────────────────────────
# ON : every turn is measured (pace, pause, language mix); every 5 turns the
#      caller is classified and the live profile (VAD silence, TTS pace,
#      endpointing ceiling, ...) may switch between responsive/patient.
# OFF: the SPEECH_PROFILE below stays fixed for the whole call.
SPEECH_TUNING = True
# Starting profile: "patient" or "responsive".
SPEECH_PROFILE = "responsive"

# ── Tool filler lines ("Let me check the pricing...") ───────────────────────
TOOL_FILLER = True

# ── Per-turn log blocks (turn_logger.py) ─────────────────────────────────────
# One readable block per turn plus a live "[STAGE]" line as each stage ends.
TURN_LOGGER = True


_VALID_DETECTORS = ("smart_turn", "livekit_mini", "vad")
if TURN_DETECTOR not in _VALID_DETECTORS:
    raise ValueError(f"agent_config.TURN_DETECTOR must be one of {_VALID_DETECTORS}, got {TURN_DETECTOR!r}")
if SPEECH_PROFILE not in ("patient", "responsive"):
    raise ValueError(f"agent_config.SPEECH_PROFILE must be 'patient' or 'responsive', got {SPEECH_PROFILE!r}")
# The semantic check lives inside the guard; without the guard it cannot run.
SEMANTIC_CHECK = SEMANTIC_CHECK and INTERRUPTION_GUARD

USE_SMART_TURN = TURN_DETECTOR == "smart_turn"


def summary() -> str:
    return (
        f"turn_detector={TURN_DETECTOR} interruption_guard={INTERRUPTION_GUARD} "
        f"semantic_check={SEMANTIC_CHECK} speech_tuning={SPEECH_TUNING} "
        f"speech_profile={SPEECH_PROFILE} tool_filler={TOOL_FILLER} turn_logger={TURN_LOGGER}"
    )
