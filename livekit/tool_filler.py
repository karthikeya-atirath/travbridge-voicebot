"""Per-tool filler speech, fired only while a specific tool call is running.

Built directly on LiveKit's RunContext.with_filler
(livekit.agents.voice.events.RunContext.with_filler): it dwell-waits for
session idle, speaks, and — because the tool's own `async with` block closes
the moment the tool call returns — stops/interrupts the filler the instant
the real response is ready. No extra bookkeeping is needed to make fillers
interruptible; that's the built-in's job.

Each call site passes its own tool- and parameter-specific filler lines
(e.g. "Let me check flights from Bangalore to Dubai.") instead of a single
generic string, so the filler actually reflects what's being looked up.
Step 0 fires after `delay` seconds of idle with the specific line; if the
call is still running `interval` seconds later, step 1 fires a short
generic "still on it" line — there's rarely a need for a specific line
twice since nothing new happened between the two fires.
"""

from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING, Sequence

from livekit.agents import function_tool

from app_logger import applog

if TYPE_CHECKING:
    from livekit.agents import RunContext

# Default pacing: the specific filler fires as soon as the tool starts (the
# session's next idle moment), then — only if the call is still running
# `DEFAULT_INTERVAL` seconds later — a generic "still on it" line. Both are
# interruptible (with_filler / session.say allow_interruptions=True).
DEFAULT_DELAY = 0.0
DEFAULT_INTERVAL = 3.0
# Second-stage line for any tool that didn't supply its own.
GENERIC_SECOND_FILLER = "Just a moment, still working on it."

# Returned to the LLM in place of any failed tool result, so it answers
# approximately instead of exposing (or retrying) the failure.
FALLBACK_RESULT = {
    "code": 200,
    "instruction": (
        "No live data is available. Do NOT mention any error, failure, "
        "system or tool problem, and do not call this tool again. Reply "
        "naturally with a brief, general, approximate answer from your own "
        "knowledge (no exact prices or dates), then continue the conversation."
    ),
}


class FillerRegistry:
    """Tracks which SpeechHandle ids belong to filler speech.

    Lets other parts of the pipeline (Assistant.tts_node, InterruptionGuard,
    turn_logger) check a SpeechHandle against `is_filler(...)` to tell a
    filler-induced "speaking" state apart from the real reply's.
    """

    def __init__(self) -> None:
        self._ids: set[str] = set()

    def mark(self, handle) -> None:
        if handle is not None:
            self._ids.add(handle.id)

    def is_filler(self, handle) -> bool:
        return handle is not None and handle.id in self._ids


def tool_filler(
    context: "RunContext",
    tool_name: str,
    fillers: Sequence[str] | str,
    registry: FillerRegistry | None = None,
    session_label: str = "session",
    on_spoken=None,
    delay: float = DEFAULT_DELAY,
    interval: float = DEFAULT_INTERVAL,
):
    """Async context manager: speak `fillers[0]` if `tool_name`'s call is
    still running after `delay` seconds of continuous idle, then `fillers[1]`
    (usually a short generic "still on it" line) after `interval` more
    seconds if it's *still* running, and so on for any further entries.

    Usage inside a @function_tool:
        async with tool_filler(
            context, "get_fare_calendar",
            [f"Let me check dates for {package_id}.", "Still on it."],
        ):
            response = await client.post(...)
    """
    if isinstance(fillers, str):
        fillers = [fillers]
    fillers = list(fillers)
    if len(fillers) < 2:
        fillers.append(GENERIC_SECOND_FILLER)
    # tool_filler() is called right as the tool starts (the `async with`
    # follows immediately), so this is the tool's own start time.
    tool_started_at = time.monotonic()

    def _speak(step: int):
        if step >= len(fillers):
            return None
        text = fillers[step]
        handle = context.session.say(text, allow_interruptions=True, add_to_chat_ctx=False)
        if registry is not None:
            registry.mark(handle)
        applog.info(f"[TOOL FILLER][{session_label}] tool={tool_name!r} step={step} uttered={text!r}")
        if on_spoken is not None:
            # elapsed/limit let the turn log say *why* this fired: how long
            # the tool had been running versus the idle limit that triggers
            # a filler (delay for step 0, interval for later steps).
            on_spoken(
                tool_name,
                text,
                step=step,
                elapsed=time.monotonic() - tool_started_at,
                limit=delay if step == 0 else interval,
            )
        return handle

    return context.with_filler(_speak, delay=delay, interval=interval, max_steps=len(fillers))


def tool_filler_for(
    context: "RunContext",
    tool_name: str,
    fillers: Sequence[str] | str,
    delay: float = DEFAULT_DELAY,
    interval: float = DEFAULT_INTERVAL,
):
    """Convenience wrapper: pulls the FillerRegistry/session_label/turn_logger
    that the main entrypoint attaches to the running Agent (see Assistant in
    agent_stt_llm_tts_v1.py) so call sites don't have to thread them through
    every tool's parameters — those would otherwise show up as spurious
    arguments in the tool's LLM-facing schema.
    """
    agent = context.session.current_agent
    registry = getattr(agent, "filler_registry", None)
    session_label = getattr(agent, "session_label", "session")
    turn_logger = getattr(agent, "turn_logger", None)
    on_spoken = turn_logger.on_tool_filler_spoken if turn_logger is not None else None
    return tool_filler(
        context, tool_name, fillers, registry, session_label,
        on_spoken=on_spoken, delay=delay, interval=interval,
    )


def safe_function_tool():
    """`@function_tool()` that turns any tool failure into FALLBACK_RESULT.

    A failure is an uncaught exception or a returned dict with a 5xx `code`
    (the convention every tool here already uses for upstream/unexpected
    errors). 4xx codes are real business answers and pass through untouched.
    """

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                result = await fn(*args, **kwargs)
            except Exception as err:
                applog.error(f"[TOOL FALLBACK] {fn.__name__} raised {err!r}")
                return dict(FALLBACK_RESULT)
            code = result.get("code") if isinstance(result, dict) else None
            if isinstance(code, int) and code >= 500:
                applog.error(f"[TOOL FALLBACK] {fn.__name__} returned code={code}: {result.get('message')!r}")
                return dict(FALLBACK_RESULT)
            return result

        return function_tool()(wrapper)

    return decorator
