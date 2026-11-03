"""Bridge a voice call started from the dynapack chat UI to dynapack's voice agent.

A call started from the chat UI is dispatched with metadata
{"source": "chat_ui", "chat_id": ..., "provider": ...} (by the dynapack token service or
its Vite dev endpoint). For those calls this worker keeps speech to text, turn detection,
interruptions and text to speech, but the reply comes from the dynapack backend: each
final transcript goes over dynapack's chat WebSocket as a channel "voice" turn on the
chat's own chat_id, and the streamed reply text is handed to TTS. The call and the chat
are one conversation there, so the voice agent knows what the chat did. The voice tools
run in dynapack (domain/voice/ there), so the session here has none.

The hello_ack carries the call's first line (``greeting``): it already knows where the
chat stands, e.g. that a holiday being built in the chat can only be changed there.
When the caller interrupts a reply, the turn is cancelled in dynapack as well
(a "cancel" frame), so the unheard reply is not kept and its tools stop.

Calls dispatched any other way (C-Zen phone calls, the auto dialler) carry no such
metadata and keep this worker's own Gemini LLM and tools.

Env: DYNAPACK_WS_URL (e.g. wss://host/dynamic/v1/api/ws/invoke), DYNAPACK_PROVIDER
(the brand when the dispatch names none; default tcil).
"""

import asyncio
import json
import os
import uuid
from typing import AsyncIterator, Optional

import websockets

from app_logger import applog

CHAT_UI_SOURCE = "chat_ui"
# Covers a full package search plus pricing; dynapack's own turn lock waits up to 240 s.
TURN_TIMEOUT_SECONDS = 120.0
HELLO_TIMEOUT_SECONDS = 15.0
# A result frame carries the turn's logs as well as the reply.
MAX_FRAME_BYTES = 8 * 1024 * 1024
# Spoken when dynapack cannot answer, so the caller never hears silence.
FAILURE_LINE = "Sorry, I couldn't get that just now. Could you please say that again?"


def chat_ui_dispatch(metadata: Optional[str]) -> Optional[dict]:
    """The dispatch metadata of a call started from the chat UI, or None for any other call."""
    try:
        data = json.loads(metadata or "")
    except ValueError:
        return None
    if isinstance(data, dict) and data.get("source") == CHAT_UI_SOURCE:
        return data
    return None


class DynapackTurnError(RuntimeError):
    """dynapack refused a frame, closed the socket or did not answer in time."""


class DynapackSession:
    """One WebSocket to dynapack per call: a hello on channel "voice", then one text frame per turn.

    Frames are routed to the turn that sent them by message_id, so a reply cancelled by
    the caller interrupting never leaks into the next one.
    """

    def __init__(self, url: str, provider: str, chat_id: Optional[str] = None) -> None:
        self._url = url
        self._provider = provider
        # The chat's id, so the call joins its session; a call with none starts one.
        self.chat_id = chat_id or str(uuid.uuid4())
        # dynapack's opening line for this call (hello_ack), spoken instead of the fixed one.
        self.greeting: str = ""
        self._ws = None
        self._reader: Optional[asyncio.Task] = None
        self._turns: dict[str, asyncio.Queue] = {}
        self._turn_count = 0

    @classmethod
    def from_env(cls, dispatch: dict) -> Optional["DynapackSession"]:
        url = os.environ.get("DYNAPACK_WS_URL", "").strip()
        if not url:
            applog.error("[DYNAPACK] DYNAPACK_WS_URL is not set; using this worker's own LLM")
            return None
        brand = (
            dispatch.get("provider") or os.environ.get("DYNAPACK_PROVIDER", "") or "tcil"
        ).strip().lower()
        chat_id = dispatch.get("chat_id")
        return cls(url, brand, chat_id if isinstance(chat_id, str) and chat_id else None)

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._url, max_size=MAX_FRAME_BYTES)
        await self._ws.send(json.dumps({
            "type": "hello",
            "chat_id": self.chat_id,
            "provider": self._provider,
            "channel": "voice",
            "stream": True,
        }))
        ack = json.loads(await asyncio.wait_for(self._ws.recv(), HELLO_TIMEOUT_SECONDS))
        if ack.get("type") != "hello_ack":
            await self._ws.close()
            raise DynapackTurnError(f"dynapack refused the hello: {ack}")
        self.greeting = str(ack.get("greeting") or "").strip()
        self._reader = asyncio.create_task(self._read())
        applog.info(f"[DYNAPACK] connected chat_id={self.chat_id} provider={self._provider}")

    async def _read(self) -> None:
        try:
            async for raw in self._ws:
                frame = json.loads(raw)
                turn = self._turns.get(str(frame.get("message_id")))
                if turn is not None:
                    turn.put_nowait(frame)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            applog.exception("[DYNAPACK] reader failed")
        finally:
            for turn in self._turns.values():
                turn.put_nowait({"type": "closed"})

    async def reply(self, text: str) -> AsyncIterator[str]:
        """Send one transcript; yield the reply as new text arrives, ready for TTS."""
        self._turn_count += 1
        message_id = f"{self.chat_id}:{self._turn_count}"
        turn: asyncio.Queue = asyncio.Queue()
        self._turns[message_id] = turn
        spoken = ""
        answered = False
        try:
            await self._ws.send(json.dumps({
                "type": "text", "chat_id": self.chat_id, "message_id": message_id, "payload": text,
            }))
            while True:
                frame = await asyncio.wait_for(turn.get(), TURN_TIMEOUT_SECONDS)
                kind = frame.get("type")
                if kind == "stream":
                    # Each stream frame holds the whole reply so far, not a delta.
                    full = frame.get("text") or ""
                elif kind == "result":
                    answered = True
                    result = frame.get("result") or {}
                    # A failed turn carries a brand-worded apology in "error"; speak it.
                    full = (
                        (result.get("final_output") or {}).get("response") or ""
                        if result.get("success") else result.get("error") or ""
                    )
                elif kind in ("error", "closed"):
                    raise DynapackTurnError(frame.get("message") or kind)
                else:
                    continue
                # A rewrite of text already spoken cannot be unsaid; only new text goes on.
                if full.startswith(spoken) and len(full) > len(spoken):
                    yield full[len(spoken):]
                    spoken = full
                if kind == "result":
                    return
        except (asyncio.CancelledError, GeneratorExit):
            # The caller interrupted: stop the turn in dynapack too, unless it already
            # answered. Cancelled mid-wait raises CancelledError here; closed between two
            # chunks (aclose) raises GeneratorExit. Fire and forget: neither may await.
            if not answered:
                asyncio.create_task(self._send_cancel(message_id))
            raise
        except Exception as err:
            applog.error(f"[DYNAPACK] turn {message_id} failed: {err!r}")
            if not spoken:
                yield FAILURE_LINE
        finally:
            self._turns.pop(message_id, None)

    async def _send_cancel(self, message_id: str) -> None:
        try:
            await self._ws.send(json.dumps({
                "type": "cancel", "chat_id": self.chat_id, "message_id": message_id,
            }))
        except Exception as err:
            applog.warning(f"[DYNAPACK] cancel for {message_id} not sent: {err!r}")

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()
