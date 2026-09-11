"""Always-on per-turn latency logging, written straight into app_voice.log.

There is no separate structured file (jsonl/log) here on purpose —
evaluation is done by reading app_voice.log itself, so this only needs to
emit a handful of clearly tagged lines per turn: a timestamp as the turn
enters each pipeline stage, and one consolidated latency line once the
turn's reply has actually finished playing.

Timestamps come from real signals already emitted elsewhere in this stack:
  - user_state_changed -> "speaking": first audio reaching STT for this turn.
  - user_input_transcribed: STT's first generated text for this turn.
  - EOUMetrics (metrics_collected): LiveKit's own authoritative
    transcription / end-of-utterance / guard timings.
  - Assistant.llm_node / tts_node (agent_stt_llm_tts_v1.py): LLM's first
    chunk of any kind (a tool-call decision or real text), first real text
    token, and TTS start/first-audio/complete.
  - tool_execution_updated (ToolCallStarted/ToolCallEnded): wall-clock start
    and end of each function-tool call the LLM makes mid-turn.
  - agent_state_changed -> "speaking"/other: real room playback start/end.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app_logger import applog


def _now() -> float:
    return time.monotonic()


def _dt(a: float | None, b: float | None) -> float | None:
    return round(b - a, 3) if (a is not None and b is not None) else None


def _fmt(value: float | None) -> str:
    return f"{value:.3f}s" if isinstance(value, (int, float)) else "n/a"


@dataclass
class _Turn:
    index: int
    event_type: str = "user_turn"
    stt_text: str = ""
    t_user_speech_start: float | None = None
    t_stt_first_text: float | None = None
    t_stt_final: float = 0.0
    transcription_delay: float | None = None  # EOUMetrics.transcription_delay
    eou_delay: float | None = None  # EOUMetrics.end_of_utterance_delay
    guard_decision_latency: float | None = None  # EOUMetrics.on_user_turn_completed_delay
    t_user_speech_end: float | None = None
    t_llm_start: float | None = None  # llm_node actually invoked (request handed to the LLM)
    t_llm_decision: float | None = None  # first chunk of any kind (text or tool call)
    t_llm_first_token: float | None = None  # first real text token
    t_llm_complete: float | None = None
    tool_calls: dict[str, dict] = field(default_factory=dict)  # call_id -> {name, t_start, t_end, status}
    t_tts_start: float | None = None
    t_tts_first_audio: float | None = None
    t_tts_complete: float | None = None
    t_playback_start: float | None = None
    t_playback_end: float | None = None
    interrupted: bool = False
    suppressed: str | None = None


class TurnLatencyTracker:
    """Feed it lifecycle calls from the STT/guard/LLM/TTS/playback stages;
    logs one line per stage plus a final consolidated latency line."""

    def __init__(self, session_label: str = "session") -> None:
        self.session_label = session_label
        self._index = 0
        self._current: _Turn | None = None
        self._closed = False
        # First audio / first STT text arrive before the turn's transcript
        # is finalized, so they're captured ahead of start_turn and attached
        # once the turn actually opens.
        self._pending_speech_start: float | None = None
        self._pending_first_text: float | None = None

    # ---- pre-turn: audio and interim text arriving before STT finalizes ----
    def on_user_speech_start(self) -> None:
        if self._pending_speech_start is None:
            self._pending_speech_start = _now()
            applog.info(f"[TURN][{self.session_label}] first audio received")

    def on_stt_text(self, text: str, is_final: bool) -> None:
        if not text.strip() or self._pending_first_text is not None:
            return
        self._pending_first_text = _now()
        applog.info(
            f"[TURN][{self.session_label}] first STT text text={text!r} is_final={is_final}"
        )

    # ---- turn intake (transcript finalized, handed to the interruption guard) ----
    def start_turn(self, stt_text: str, event_type: str = "user_turn") -> None:
        self._index += 1
        self._current = _Turn(
            index=self._index,
            event_type=event_type,
            stt_text=stt_text,
            t_user_speech_start=self._pending_speech_start,
            t_stt_first_text=self._pending_first_text,
            t_stt_final=_now(),
        )
        self._pending_speech_start = None
        self._pending_first_text = None
        applog.info(
            f"[TURN][{self.session_label}][turn {self._index}] reached interruption guard "
            f"type={event_type} text={stt_text!r}"
        )

    def on_eou_metrics(
        self,
        end_of_utterance_delay: float,
        transcription_delay: float,
        on_user_turn_completed_delay: float,
    ) -> None:
        if self._current is None:
            return
        now = _now()
        self._current.t_user_speech_end = now - on_user_turn_completed_delay - end_of_utterance_delay
        self._current.transcription_delay = round(transcription_delay, 3)
        self._current.eou_delay = round(end_of_utterance_delay, 3)
        self._current.guard_decision_latency = round(on_user_turn_completed_delay, 3)

    def suppress_turn(self, reason: str) -> None:
        if self._current is None:
            return
        self._current.suppressed = reason
        self._flush()

    # ---- LLM ----
    def on_llm_start(self) -> None:
        """llm_node has just been entered for this turn — the moment the
        request is actually handed to the LLM, as opposed to when the user
        stopped speaking (which also includes STT/EOU/guard time upstream)."""
        if self._current is not None and self._current.t_llm_start is None:
            self._current.t_llm_start = _now()

    def on_llm_first_signal(self) -> None:
        """First chunk of any kind from this turn's llm_node — a tool-call
        decision or real text, whichever comes first. Marks how quickly the
        LLM engages at all, before any tool execution can push that back."""
        if self._current is not None and self._current.t_llm_decision is None:
            self._current.t_llm_decision = _now()

    def on_llm_first_token(self) -> None:
        if self._current is not None and self._current.t_llm_first_token is None:
            self._current.t_llm_first_token = _now()
            applog.info(f"[TURN][{self.session_label}][turn {self._current.index}] llm first token")

    def on_llm_complete(self, text: str) -> None:
        if self._current is not None:
            self._current.t_llm_complete = _now()

    def on_llm_cancelled(self) -> None:
        if self._current is None:
            return
        self._current.interrupted = True
        self._flush()

    # ---- TTS ----
    def on_tts_start(self) -> None:
        if self._current is not None and self._current.t_tts_start is None:
            self._current.t_tts_start = _now()

    def on_tts_first_audio(self) -> None:
        if self._current is not None and self._current.t_tts_first_audio is None:
            self._current.t_tts_first_audio = _now()
            applog.info(f"[TURN][{self.session_label}][turn {self._current.index}] tts first audio")

    def on_tts_complete(self, chars: int) -> None:
        if self._current is None:
            return
        self._current.t_tts_complete = _now()
        self._maybe_flush()

    def on_tts_no_output(self) -> None:
        if self._current is not None:
            self._flush()

    def on_tts_cancelled(self, chars: int) -> None:
        if self._current is None:
            return
        if self._current.t_playback_start is not None and self._current.t_playback_end is None:
            self._current.t_playback_end = _now()
        self._current.interrupted = True
        self._flush()

    # ---- function tool calls (mid-turn, between LLM rounds) ----
    def on_tool_call_start(self, call_id: str, name: str) -> None:
        if self._current is None:
            return
        self._current.tool_calls[call_id] = {"name": name, "t_start": _now(), "t_end": None, "status": None}
        applog.info(
            f"[TURN][{self.session_label}][turn {self._current.index}] tool call start "
            f"name={name!r} call_id={call_id}"
        )

    def on_tool_call_end(self, call_id: str, status: str) -> None:
        if self._current is None:
            return
        entry = self._current.tool_calls.get(call_id)
        if entry is None:
            return
        entry["t_end"] = _now()
        entry["status"] = status
        applog.info(
            f"[TURN][{self.session_label}][turn {self._current.index}] tool call end "
            f"name={entry['name']!r} call_id={call_id} status={status} "
            f"duration={_fmt(_dt(entry['t_start'], entry['t_end']))}"
        )

    # ---- real room playback ----
    def on_playback_start(self) -> None:
        if self._current is not None and self._current.t_playback_start is None:
            self._current.t_playback_start = _now()

    def on_playback_end(self) -> None:
        if self._current is None or self._current.t_playback_start is None:
            return
        if self._current.t_playback_end is None:
            self._current.t_playback_end = _now()
        self._maybe_flush()

    def _maybe_flush(self) -> None:
        rec = self._current
        if rec is not None and rec.t_tts_complete is not None and rec.t_playback_end is not None:
            self._flush()

    # ---- interruption-guard observer: only cares whether THIS turn's
    #      playback ends up genuinely interrupted ----
    def record_guard_event(self, kind: str, data: dict) -> None:
        if self._current is not None and kind == "decision" and data.get("decision") == "interrupt":
            self._current.interrupted = True

    # ---- shutdown ----
    def flush_dangling(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._current is not None:
            self._current.suppressed = self._current.suppressed or "session_closed_mid_turn"
            self._flush()

    def _flush(self) -> None:
        rec = self._current
        if rec is None:
            return
        self._current = None

        user_speech_end = rec.t_user_speech_end or rec.t_stt_final
        audio_start = rec.t_user_speech_start or user_speech_end
        playback_start = rec.t_playback_start or rec.t_tts_first_audio or rec.t_tts_start
        playback_end = rec.t_playback_end or rec.t_tts_complete

        tool_starts = [e["t_start"] for e in rec.tool_calls.values()]
        tool_ends = [e["t_end"] for e in rec.tool_calls.values() if e["t_end"] is not None]
        # Wall-clock span across all tool calls in this turn (start of the
        # first call to end of the last), not a sum of durations — parallel
        # tool calls don't stack the turn's actual latency.
        tool_start = min(tool_starts) if tool_starts else None
        tool_end = max(tool_ends) if tool_ends else None

        latencies = {
            "stt_first_text": _dt(audio_start, rec.t_stt_first_text),
            "transcription": rec.transcription_delay,
            "eou": rec.eou_delay,
            "guard": rec.guard_decision_latency,
            "dispatch_to_llm": _dt(user_speech_end, rec.t_llm_start),
            "llm_decision": _dt(rec.t_llm_start, rec.t_llm_decision),
            "llm_ttft": _dt(rec.t_llm_start, rec.t_llm_first_token),
            "llm_generation": _dt(rec.t_llm_first_token, rec.t_llm_complete),
            "tool_execution": _dt(tool_start, tool_end),
            "post_tool_llm": _dt(tool_end, rec.t_llm_first_token) if tool_end is not None else None,
            "tts_ttfb": _dt(rec.t_tts_start, rec.t_tts_first_audio),
            "tts_synthesis": _dt(rec.t_tts_start, rec.t_tts_complete),
            "playback": _dt(rec.t_playback_start, rec.t_playback_end),
            "total_time_to_first_audio": _dt(user_speech_end, playback_start),
            "total_turn_latency": _dt(user_speech_end, playback_end),
        }
        applog.info(
            f"[TURN LATENCY][{self.session_label}][turn {rec.index}] type={rec.event_type} "
            f"suppressed={rec.suppressed or 'no'} interrupted={rec.interrupted} | "
            f"stt_first_text={_fmt(latencies['stt_first_text'])} "
            f"transcription={_fmt(latencies['transcription'])} "
            f"eou={_fmt(latencies['eou'])} "
            f"guard={_fmt(latencies['guard'])} "
            f"dispatch_to_llm={_fmt(latencies['dispatch_to_llm'])} "
            f"llm_decision={_fmt(latencies['llm_decision'])} "
            f"llm_ttft={_fmt(latencies['llm_ttft'])} "
            f"llm_generation={_fmt(latencies['llm_generation'])} "
            f"tool_calls={len(rec.tool_calls)} "
            f"tool_execution={_fmt(latencies['tool_execution'])} "
            f"post_tool_llm={_fmt(latencies['post_tool_llm'])} "
            f"tts_ttfb={_fmt(latencies['tts_ttfb'])} "
            f"tts_synthesis={_fmt(latencies['tts_synthesis'])} "
            f"playback={_fmt(latencies['playback'])} "
            f"total_time_to_first_audio={_fmt(latencies['total_time_to_first_audio'])} "
            f"total_turn_latency={_fmt(latencies['total_turn_latency'])}"
        )
        self._log_breakdown(rec, latencies)

    def _log_breakdown(self, rec: "_Turn", latencies: dict) -> None:
        """A compact, at-a-glance version of the same numbers: the five
        pipeline stages that make up user-perceived wait time, in order,
        with the biggest one flagged so a slow turn's cause jumps out
        without having to read the full TURN LATENCY line."""
        llm_stage = None
        if latencies["llm_decision"] is not None or latencies["post_tool_llm"] is not None:
            llm_stage = round((latencies["llm_decision"] or 0.0) + (latencies["post_tool_llm"] or 0.0), 3)

        stages = [
            ("STT", latencies["transcription"]),
            ("EOU", latencies["eou"]),
            ("Guard", latencies["guard"]),
            ("LLM", llm_stage),
            ("Tool", latencies["tool_execution"]),
            ("TTS", latencies["tts_ttfb"]),
        ]
        present = [(name, value) for name, value in stages if value is not None]
        bottleneck = max((v for _, v in present), default=None)

        lines = [
            f"{name}?{' ' * (8 - len(name))}{_fmt(value)}"
            + ("   <- problem" if bottleneck is not None and value == bottleneck else "")
            for name, value in stages
            if value is not None
        ]
        applog.info(
            f"[TURN BREAKDOWN][{self.session_label}][turn {rec.index}]\n" + "\n".join(lines)
        )


def attach_turn_logger(
    session, session_label: str, interruption_guard, filler_registry=None
) -> TurnLatencyTracker:
    """`filler_registry` (silence_filler.FillerRegistry), when given, is
    checked against session.current_speech on every agent_state_changed —
    a filler line flips agent_state to "speaking" too, and must not be
    logged/timed as if it were the real reply's playback (see
    silence_filler.py's module docstring)."""
    tracker = TurnLatencyTracker(session_label=session_label)
    interruption_guard.add_observer(tracker.record_guard_event)

    def _on_user_state_changed(event) -> None:
        if getattr(event, "new_state", None) == "speaking":
            tracker.on_user_speech_start()

    def _on_user_input_transcribed(event) -> None:
        tracker.on_stt_text(event.transcript, event.is_final)

    def _on_metrics_collected(event) -> None:
        m = getattr(event, "metrics", None)
        if m is None or getattr(m, "type", None) != "eou_metrics":
            return
        tracker.on_eou_metrics(
            end_of_utterance_delay=m.end_of_utterance_delay,
            transcription_delay=m.transcription_delay,
            on_user_turn_completed_delay=m.on_user_turn_completed_delay,
        )

    def _on_agent_state_changed(event) -> None:
        if filler_registry is not None and filler_registry.is_filler(session.current_speech):
            return
        if getattr(event, "new_state", None) == "speaking":
            tracker.on_playback_start()
        else:
            tracker.on_playback_end()

    def _on_tool_execution_updated(event) -> None:
        update = getattr(event, "update", None)
        update_type = getattr(update, "type", None)
        if update_type == "tool_call_started":
            fnc_call = update.function_call
            tracker.on_tool_call_start(fnc_call.call_id, fnc_call.name)
        elif update_type == "tool_call_ended":
            tracker.on_tool_call_end(update.call_id, update.status)

    session.on("user_state_changed")(_on_user_state_changed)
    session.on("user_input_transcribed")(_on_user_input_transcribed)
    session.on("metrics_collected")(_on_metrics_collected)
    session.on("agent_state_changed")(_on_agent_state_changed)
    session.on("tool_execution_updated")(_on_tool_execution_updated)
    session.on("close")(lambda _event: tracker.flush_dangling())
    return tracker
