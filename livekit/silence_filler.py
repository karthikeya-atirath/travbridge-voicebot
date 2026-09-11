"""Session-wide filler speech for any silent gap on the bot's turn.

Covers every source of dead air between the user finishing their turn and
the agent's reply actually starting to play — slow LLM generation, a tool
call, several tool calls back to back, a slow network hop, TTS taking a
moment to produce its first frame, anything. All of those, no matter the
cause, show up as the same agent_state: "thinking" (LiveKit only flips to
"speaking" once real audio is actually forwarded — see
livekit.agents.voice.agent_activity, _update_agent_state("speaking", ...)).
This deliberately doesn't ask *why* the agent is thinking, only how long,
so it needs no per-tool wiring anywhere.

Never fires during the user's turn: "thinking" only exists between the user
finishing and the agent starting to speak, and the dwell timer is cancelled
the instant user_state_changed reports "speaking" again, so a filler can
never land on top of the user's own speech.

Fillers are spoken with add_to_chat_ctx=False — they're stalling audio, not
conversational content, so they never enter the LLM's chat context or
chat_history/CRM transcript. Their SpeechHandle ids are recorded in
FillerRegistry so callers elsewhere in the pipeline (turn_logger's per-turn
latency tracking, in particular) can tell "the agent started making noise"
apart from "the real reply actually started" — a filler flips agent_state
to "speaking" too, and must not be mistaken for the real response.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Callable

from app_logger import applog

# Kept very short and generic (no reference to what's being looked up) so
# the same pool reads naturally no matter what caused the silence.
FILLER_LINES: list[str] = [
    "Just a moment.",
    "One moment.",
    "Checking now.",
    "Hang tight.",
    "Almost there.",
    "Still on it.",
    "One second.",
    "Working on it.",
]
# No filler until the agent has been silently "thinking" for this long —
# ordinary, fast turns never trigger one. If it's still thinking after that,
# a fresh (randomly picked) line repeats every FILLER_INTERVAL seconds,
# capped at FILLER_MAX_STEPS so a genuinely stuck turn doesn't loop forever.
FILLER_DELAY = 2.0
FILLER_INTERVAL = 5.0
FILLER_MAX_STEPS = 4


def random_filler() -> str:
    return random.choice(FILLER_LINES)


class FillerRegistry:
    """Tracks which SpeechHandle ids belong to filler speech.

    Lets other parts of the pipeline check `session.current_speech` against
    `is_filler(...)` to tell a filler-induced "speaking" state apart from
    the real reply's, instead of treating every "speaking" transition as
    proof the real response has started.
    """

    def __init__(self) -> None:
        self._ids: set[str] = set()
        self._on_real_tts_start: Callable[[], None] | None = None

    def mark(self, handle) -> None:
        if handle is not None:
            self._ids.add(handle.id)

    def is_filler(self, handle) -> bool:
        return handle is not None and handle.id in self._ids

    def set_on_real_tts_start(self, callback: Callable[[], None]) -> None:
        """Register the callback attach_silence_filler wires up to end the
        "thinking" dwell window early — see notify_real_tts_start."""
        self._on_real_tts_start = callback

    def notify_real_tts_start(self) -> None:
        """Called by tts_node the instant a non-filler reply's synthesis
        actually starts (Assistant.tts_node's "[TTS] synthesis starting"
        point) — well before agent_state_changed flips to "speaking", which
        LiveKit only does once real audio is actually forwarded (see the
        module docstring). Without this, the dwell/fire loop only learns a
        real reply is imminent once its audio starts playing, so a filler
        can fire in the gap between synthesis starting (TTS TTFB) and audio
        actually reaching the room — talking over, or needlessly right
        before, a reply that was already on its way.
        """
        if self._on_real_tts_start is not None:
            self._on_real_tts_start()


def attach_silence_filler(session, session_label: str = "session") -> FillerRegistry:
    """Wire the dwell/fire loop to agent_state_changed/user_state_changed."""
    registry = FillerRegistry()
    state: dict = {"thinking": False, "task": None, "think_start": None, "fillers_spoken": 0}

    def _cancel_pending() -> None:
        task = state["task"]
        if task is not None and not task.done():
            task.cancel()
        state["task"] = None

    def _end_thinking(reason: str) -> None:
        # Logs the silent window's full duration and how many fillers (if
        # any) were spoken during it, whatever ended it: the real reply
        # starting to play, the user interrupting, or the session closing.
        if state["thinking"] and state["think_start"] is not None:
            waited = round(time.monotonic() - state["think_start"], 3)
            applog.info(
                f"[SILENCE FILLER][{session_label}] state=thinking->{reason} "
                f"waited={waited:.3f}s fillers_spoken={state['fillers_spoken']}"
            )
        state["thinking"] = False
        state["think_start"] = None
        state["fillers_spoken"] = 0
        _cancel_pending()

    async def _dwell_and_fire() -> None:
        try:
            for step in range(FILLER_MAX_STEPS):
                dwell = FILLER_DELAY if step == 0 else FILLER_INTERVAL
                await asyncio.sleep(dwell)
                if not state["thinking"]:
                    return
                waited = round(time.monotonic() - state["think_start"], 3)
                line = random_filler()
                applog.info(
                    f"[SILENCE FILLER][{session_label}] step={step + 1} state=thinking "
                    f"waited={waited:.3f}s dwell={dwell:.1f}s selected={line!r}"
                )
                handle = session.say(line, allow_interruptions=True, add_to_chat_ctx=False)
                registry.mark(handle)
                state["fillers_spoken"] += 1
                applog.info(
                    f"[SILENCE FILLER][{session_label}] step={step + 1} state=speaking(filler) "
                    f"uttered={line!r}"
                )
        except asyncio.CancelledError:
            pass

    def _on_agent_state_changed(event) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "thinking":
            if not state["thinking"]:
                state["thinking"] = True
                state["think_start"] = time.monotonic()
                state["fillers_spoken"] = 0
                _cancel_pending()
                applog.info(f"[SILENCE FILLER][{session_label}] state=idle->thinking waiting_start")
                state["task"] = asyncio.create_task(_dwell_and_fire())
        else:
            _end_thinking(new_state or "unknown")

    def _on_user_state_changed(event) -> None:
        # The user starting to speak ends the bot's silent window outright,
        # even if agent_state hasn't flipped away from "thinking" yet.
        if getattr(event, "new_state", None) == "speaking":
            _end_thinking("user_speaking")

    session.on("agent_state_changed")(_on_agent_state_changed)
    session.on("user_state_changed")(_on_user_state_changed)
    registry.set_on_real_tts_start(lambda: _end_thinking("tts_starting"))
    return registry
