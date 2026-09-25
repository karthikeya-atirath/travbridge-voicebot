"""Force Deepgram to finalize the moment the caller stops speaking.

Why this exists: LiveKit only starts end-of-turn detection once a *final*
transcript exists (audio_recognition._run_eou_detection returns early while
only interim text has arrived), and the endpointing delay is then counted from
the end of speech, not from the final. So when Deepgram is slow to finalize on
its own, the turn commits late by exactly that lag (a 1.0s endpointing ceiling
was observed committing at 2.3s), and short replies such as "hello" that the
guard only trusts once final wait the same time.

Sending Deepgram a Finalize the instant VAD reports end of speech makes the
final arrive on schedule. It does not commit the turn earlier than configured:
the commit still waits out the endpointing delay from the last speech, and if
the caller resumes inside that window the pending end-of-turn is cancelled and
the next final is appended to the same transcript (audio_recognition.py,
START_OF_SPEECH handling). Preemptive generation, when on, simply gets its
final sooner.

Three cases have to be covered, which is why this is event-driven rather than
a single call on end of speech:
  1. An interim is already pending when VAD ends  -> flush at the transition.
  2. STT lags: the last interim arrives *after* VAD ended -> flush on arrival.
  3. The flush is lost/ignored and no final follows  -> one retry.
Nothing is sent when no interim text exists (noise VAD picked up), and at most
one flush per end-of-speech is issued, plus the single retry.
"""

from __future__ import annotations

import asyncio
import time
import weakref
from typing import TYPE_CHECKING

from livekit.plugins import deepgram

from app_logger import applog

if TYPE_CHECKING:
    from livekit.agents import AgentSession

# How long to wait for the final after a flush before trying once more.
FLUSH_RETRY_AFTER: float = 1.0


class FlushableDeepgramSTT(deepgram.STT):
    """deepgram.STT that remembers its newest stream so it can be flushed.

    AgentSession creates the stream internally (Agent.stt_node), so there is
    otherwise no handle to it.
    """

    _latest_stream_ref: "weakref.ReferenceType | None" = None

    def stream(self, *args, **kwargs):
        stream = super().stream(*args, **kwargs)
        self._latest_stream_ref = weakref.ref(stream)
        return stream

    def flush_active_stream(self) -> bool:
        ref = self._latest_stream_ref
        stream = ref() if ref is not None else None
        if stream is None:
            return False
        try:
            stream.flush()
        except RuntimeError:
            # Stream closed or input already ended (reconnect/teardown).
            return False
        return True


class SttFlusher:
    def __init__(self, session: "AgentSession", stt: FlushableDeepgramSTT, label: str) -> None:
        self._session = session
        self._stt = stt
        self._label = label
        self._user_speaking = False
        # An interim exists that no final has replaced yet.
        self._pending_interim = False
        # A flush was sent for the current end-of-speech and is awaiting its final.
        self._flushed_at: float | None = None
        self._retried = False
        self._retry_handle: asyncio.TimerHandle | None = None

    def on_user_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking":
            self._user_speaking = True
            self._reset_flush_state()
            return
        if new_state in {"listening", "away"} and self._user_speaking:
            self._user_speaking = False
            if self._pending_interim:
                self._flush("vad_end_with_pending_interim")

    def on_user_input_transcribed(self, event: object) -> None:
        if not (getattr(event, "transcript", "") or "").strip():
            return
        if getattr(event, "is_final", False):
            if self._flushed_at is not None:
                applog.info(
                    f"[STT FLUSH][{self._label}] final arrived "
                    f"{(time.monotonic() - self._flushed_at) * 1000:.0f}ms after flush"
                )
            self._pending_interim = False
            self._reset_flush_state()
            return
        self._pending_interim = True
        # STT lag: the interim for the tail of the utterance can land after VAD
        # already reported end of speech, when no transition is left to hook.
        if not self._user_speaking and self._flushed_at is None:
            self._flush("interim_after_vad_end")

    def _reset_flush_state(self) -> None:
        self._flushed_at = None
        self._retried = False
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None

    def _flush(self, reason: str) -> None:
        if not self._stt.flush_active_stream():
            applog.warning(f"[STT FLUSH][{self._label}] no active stream to flush ({reason})")
            return
        self._flushed_at = time.monotonic()
        applog.info(f"[STT FLUSH][{self._label}] flushed: {reason}")
        if not self._retried:
            self._retry_handle = asyncio.get_running_loop().call_later(
                FLUSH_RETRY_AFTER, self._retry_if_still_waiting
            )

    def _retry_if_still_waiting(self) -> None:
        self._retry_handle = None
        if self._user_speaking or not self._pending_interim or self._retried:
            return
        self._retried = True
        applog.warning(
            f"[STT FLUSH][{self._label}] no final {FLUSH_RETRY_AFTER:.1f}s after flush, retrying once"
        )
        self._flush("retry_no_final")


def attach_stt_flush(
    session: "AgentSession", stt: FlushableDeepgramSTT, session_label: str | None = None
) -> SttFlusher:
    flusher = SttFlusher(session, stt, session_label or "session")
    session.on("user_state_changed")(flusher.on_user_state_changed)
    session.on("user_input_transcribed")(flusher.on_user_input_transcribed)
    return flusher
