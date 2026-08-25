"""Passive, read-only metrics for evaluating two things: how much the caller
micro-pauses (short, uneven gaps between turns) and how the interruption
guard is deciding/acting on barge-ins.

Purely observational — never calls session.interrupt(), never changes STT/
TTS/endpointing options, and doesn't touch interruption_guard's decision
logic or speech_tuner's classifier. Logs one aggregate summary per call,
tagged [CALL METRICS], when the session closes, and also appends the same
data as JSON to call_metrics.jsonl (one file per bot directory, auto-labeled
by directory name) so different bot variants' numbers can be diffed with a
script instead of by eyeballing log lines — see compare_call_metrics.py.

The session "close" event depends on a graceful shutdown sequence and can be
skipped entirely if the room is force-closed or the process exits abruptly
mid-call. So the aggregate isn't the only record: every turn's pause is also
logged/appended live as it's observed, so the raw data survives even when the
final summary doesn't fire (interruption-guard decisions are already logged
live by interruption_guard.py itself, independent of this module).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from statistics import mean, pstdev

from livekit.agents import AgentSession
from livekit.agents.metrics import EOUMetrics

from app_logger import applog

# Buckets for classifying each turn's end-of-utterance pause. "Micro" is a
# short-but-present gap (not instant, not a long hesitant silence) — the
# behavior speech_tuner's micro_pause profile targets (~0.45s, high variance).
MICRO_PAUSE_BAND = (0.15, 0.6)

# Which bot this is (the containing directory name, e.g. "livekit" or
# "livekit_sotc") — lets compare_call_metrics.py tell variants apart when
# reading each one's call_metrics.jsonl.
BOT_NAME = os.path.basename(os.path.dirname(os.path.abspath(__file__)))
_METRICS_JSONL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_metrics.jsonl")


def _append_jsonl(record: dict) -> None:
    try:
        with open(_METRICS_JSONL_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        applog.warning(f"[CALL METRICS] failed to write call_metrics.jsonl: {e}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CallMetrics:
    def __init__(self, session_label: str = "session") -> None:
        self.session_label = session_label
        self.pauses: list[float] = []
        self.decisions: Counter[str] = Counter()
        self.reasons: Counter[str] = Counter()
        self.interrupt_latencies: list[float] = []
        self.false_interruptions = 0
        self.false_interruptions_resumed = 0

    # ---- micro-pause side: one EOUMetrics event per completed user turn ----
    def on_metrics_collected(self, event) -> None:
        m = event.metrics
        if isinstance(m, EOUMetrics) and m.end_of_utterance_delay > 0:
            pause = m.end_of_utterance_delay
            self.pauses.append(pause)
            # Logged/appended live (not just in the final summary) so the raw
            # pause data survives even if the session never emits "close".
            applog.info(
                f"[CALL METRICS][{self.session_label}] "
                f"turn={len(self.pauses)} pause={pause:.3f}s"
            )
            _append_jsonl(
                {
                    "type": "turn",
                    "bot": BOT_NAME,
                    "session_label": self.session_label,
                    "ts": _now_iso(),
                    "turn_index": len(self.pauses),
                    "pause": pause,
                }
            )

    # ---- interruption-guard side: wired via InterruptionGuard(on_event=...) ----
    def record_guard_event(self, kind: str, data: dict) -> None:
        if kind == "decision":
            self.decisions[data["decision"]] += 1
            self.reasons[data["reason"]] += 1
            if data["decision"] == "interrupt":
                self.interrupt_latencies.append(data["speech_duration"])
        elif kind == "false_interruption":
            self.false_interruptions += 1
            if data["resumed"]:
                self.false_interruptions_resumed += 1

    def log_summary(self) -> None:
        n = len(self.pauses)
        pause_mean = mean(self.pauses) if self.pauses else 0.0
        pause_stdev = pstdev(self.pauses) if n > 1 else 0.0
        micro = sum(1 for p in self.pauses if MICRO_PAUSE_BAND[0] <= p <= MICRO_PAUSE_BAND[1])
        micro_ratio = micro / n if n else 0.0

        n_interrupts = len(self.interrupt_latencies)
        latency_mean = mean(self.interrupt_latencies) if self.interrupt_latencies else 0.0

        applog.info(
            f"[CALL METRICS][{self.session_label}] "
            f"turns={n} avg_pause={pause_mean:.3f}s pause_stdev={pause_stdev:.3f}s "
            f"micro_pause_turns={micro} micro_pause_ratio={micro_ratio:.2f} | "
            f"decisions={dict(self.decisions)} reasons={dict(self.reasons)} "
            f"interrupts_fired={n_interrupts} avg_decision_latency={latency_mean:.3f}s "
            f"false_interruptions={self.false_interruptions} "
            f"false_interruptions_resumed={self.false_interruptions_resumed}"
        )
        _append_jsonl(
            {
                "type": "summary",
                "bot": BOT_NAME,
                "session_label": self.session_label,
                "ts": _now_iso(),
                "turns": n,
                "avg_pause": pause_mean,
                "pause_stdev": pause_stdev,
                "micro_pause_turns": micro,
                "micro_pause_ratio": micro_ratio,
                "decisions": dict(self.decisions),
                "reasons": dict(self.reasons),
                "interrupts_fired": n_interrupts,
                "avg_decision_latency": latency_mean,
                "false_interruptions": self.false_interruptions,
                "false_interruptions_resumed": self.false_interruptions_resumed,
            }
        )


def attach_call_metrics(session: AgentSession, session_label: str | None = None) -> CallMetrics:
    """Attach a passive metrics collector for evaluating micro-pause behavior
    and interruption-guard decisions. Logs one [CALL METRICS] summary line
    (and appends the same data as JSON to call_metrics.jsonl) per call at
    session close. Feed guard events into it by passing
    `metrics.record_guard_event` as `on_event=` to attach_interruption_guard.
    """
    metrics = CallMetrics(session_label=session_label or "session")
    session.on("metrics_collected")(metrics.on_metrics_collected)
    session.on("close")(lambda _event: metrics.log_summary())
    return metrics
