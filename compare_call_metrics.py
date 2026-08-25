"""Compare micro-pause and interruption-handling metrics across bot variants.

Reads each bot directory's call_metrics.jsonl (written by call_metrics.py —
see livekit/call_metrics.py and livekit_sotc/call_metrics.py) and prints a
side-by-side summary. Read-only: does not touch any bot's runtime code.

Usage:
    python3 compare_call_metrics.py                       # scan default dirs
    python3 compare_call_metrics.py livekit livekit_sotc   # explicit dirs
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_BOT_DIRS = ["livekit", "livekit_sotc", "livekit_forex"]


def load_summaries(jsonl_path: Path) -> list[dict]:
    if not jsonl_path.exists():
        return []
    summaries = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "summary":
                summaries.append(record)
    return summaries


def aggregate(summaries: list[dict]) -> dict:
    calls = len(summaries)
    total_turns = sum(s["turns"] for s in summaries)

    def turn_weighted(key: str) -> float:
        if total_turns == 0:
            return 0.0
        return sum(s[key] * s["turns"] for s in summaries) / total_turns

    decisions: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for s in summaries:
        decisions.update(s.get("decisions", {}))
        reasons.update(s.get("reasons", {}))
    total_decisions = sum(decisions.values())

    interrupts_fired = sum(s["interrupts_fired"] for s in summaries)
    latency_weighted = (
        sum(s["avg_decision_latency"] * s["interrupts_fired"] for s in summaries) / interrupts_fired
        if interrupts_fired
        else 0.0
    )

    false_interruptions = sum(s["false_interruptions"] for s in summaries)
    false_interruptions_resumed = sum(s["false_interruptions_resumed"] for s in summaries)

    return {
        "calls": calls,
        "total_turns": total_turns,
        "avg_pause": turn_weighted("avg_pause"),
        "avg_pause_stdev": turn_weighted("pause_stdev"),
        "micro_pause_ratio": turn_weighted("micro_pause_ratio"),
        "decisions_pct": {
            k: (v / total_decisions * 100 if total_decisions else 0.0) for k, v in decisions.items()
        },
        "reasons_top": reasons.most_common(5),
        "interrupts_fired": interrupts_fired,
        "avg_decision_latency": latency_weighted,
        "false_interruptions": false_interruptions,
        "false_interruption_resume_rate": (
            false_interruptions_resumed / false_interruptions if false_interruptions else 0.0
        ),
    }


def print_comparison(bots: dict[str, dict]) -> None:
    if not bots:
        print("No call_metrics.jsonl files found (or none contained summary records).")
        return

    rows = [
        ("calls", "calls", "{:d}"),
        ("total turns", "total_turns", "{:d}"),
        ("avg pause (s)", "avg_pause", "{:.3f}"),
        ("avg pause stdev (s)", "avg_pause_stdev", "{:.3f}"),
        ("micro-pause ratio", "micro_pause_ratio", "{:.2%}"),
        ("interrupts fired", "interrupts_fired", "{:d}"),
        ("avg decision latency (s)", "avg_decision_latency", "{:.3f}"),
        ("false interruptions", "false_interruptions", "{:d}"),
        ("false-interrupt resume rate", "false_interruption_resume_rate", "{:.2%}"),
    ]

    bot_names = list(bots.keys())
    label_width = max(len(label) for label, _, _ in rows) + 2
    col_width = max(max(len(name) for name in bot_names) + 2, 14)

    header = " " * label_width + "".join(name.ljust(col_width) for name in bot_names)
    print(header)
    print("-" * len(header))
    for label, key, fmt in rows:
        line = label.ljust(label_width)
        for name in bot_names:
            value = bots[name].get(key, 0)
            line += fmt.format(value).ljust(col_width)
        print(line)

    print()
    for name in bot_names:
        print(f"[{name}] decision mix: " + ", ".join(
            f"{k}={v:.1f}%" for k, v in sorted(bots[name]["decisions_pct"].items(), key=lambda kv: -kv[1])
        ) or "(no decisions recorded)")
        top_reasons = ", ".join(f"{k}={v}" for k, v in bots[name]["reasons_top"])
        print(f"[{name}] top reasons: {top_reasons or '(none)'}")


def main() -> None:
    repo_root = Path(__file__).resolve().parent
    bot_dirs = sys.argv[1:] or DEFAULT_BOT_DIRS

    bots: dict[str, dict] = {}
    for bot_dir in bot_dirs:
        jsonl_path = repo_root / bot_dir / "call_metrics.jsonl"
        summaries = load_summaries(jsonl_path)
        if not summaries:
            continue
        bots[bot_dir] = aggregate(summaries)

    print_comparison(bots)


if __name__ == "__main__":
    main()
