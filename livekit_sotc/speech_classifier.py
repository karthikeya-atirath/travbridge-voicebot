"""Rolling-window speech-type classifier.

Buckets a customer's recent turns into one of 5 speech types using timing
signals already emitted by AgentSession (speaking pace, end-of-utterance
pause, barge-in behavior) — no LLM call, no extra latency or cost.

Re-evaluated every `window` turns (default 5). Classification is a
nearest-centroid match against hardcoded reference signatures below; tune
the numbers after listening to a few real calls rather than treating them
as final.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from statistics import pstdev

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

SPEECH_TYPES = ("fast_fluent", "slow_steady", "micro_pause", "hesitant", "interrupter")

# pace = words/sec, pause = end-of-utterance delay (s) averaged over the
# window, pause_var = stdev of that pause (catches "micro pause" speakers
# who pause briefly but often, vs. one long hesitant pause), interrupt_rate
# = fraction of the window's turns that barged in on the agent.
_SIGNATURES = {
    "fast_fluent":  {"pace": 3.2, "pause": 0.25, "pause_var": 0.10, "interrupt_rate": 0.1},
    "slow_steady":  {"pace": 1.3, "pause": 0.90, "pause_var": 0.10, "interrupt_rate": 0.0},
    "micro_pause":  {"pace": 2.0, "pause": 0.45, "pause_var": 0.45, "interrupt_rate": 0.1},
    "hesitant":     {"pace": 1.1, "pause": 1.40, "pause_var": 0.20, "interrupt_rate": 0.0},
    "interrupter":  {"pace": 2.6, "pause": 0.20, "pause_var": 0.15, "interrupt_rate": 0.6},
}

# The 4 features live on very different scales (pace ~1-3.2, interrupt_rate
# 0-1, pause_var ~0.1-0.45) — plain Euclidean distance would let pace's raw
# magnitude dominate the match regardless of how relevant the other features
# are. Rescale each by its own spread across _SIGNATURES so every feature
# contributes comparably; self-adjusts if the signatures above are edited.
_FEATURE_KEYS = ("pace", "pause", "pause_var", "interrupt_rate")
_FEATURE_SCALE = {
    k: (max(sig[k] for sig in _SIGNATURES.values()) - min(sig[k] for sig in _SIGNATURES.values())) or 1.0
    for k in _FEATURE_KEYS
}


@dataclass
class TurnSignal:
    pace: float = 0.0          # words / sec, from transcript length / STT audio_duration
    pause: float = 0.0         # EOUMetrics.end_of_utterance_delay, seconds
    interrupted: bool = False  # user started speaking while the agent was speaking
    mix_ratio: float = 0.0     # reserved for future accent/codemix use


class SpeechClassifier:
    """Feed it completed turns; every `window` turns it returns a label."""

    def __init__(self, window: int = 5):
        self.window = window
        self._turns: deque[TurnSignal] = deque(maxlen=window)
        self._count = 0
        # Populated by _classify() on every reclassification boundary so
        # callers (e.g. speech_tuner's reporting) can audit *why* a label
        # was picked, without changing add_turn's return contract.
        self.last_vector: dict[str, float] = {}
        self.last_distances: dict[str, float] = {}

    def add_turn(self, signal: TurnSignal) -> str | None:
        """Record one completed user turn. Returns a speech-type label
        only on turns where the window is full and it's a reclassification
        boundary (every `window` turns); otherwise returns None."""
        self._turns.append(signal)
        self._count += 1
        if len(self._turns) < self.window or self._count % self.window != 0:
            return None
        return self._classify()

    def _classify(self) -> str:
        n = len(self._turns)
        pace = sum(t.pace for t in self._turns) / n
        pause = sum(t.pause for t in self._turns) / n
        pause_var = pstdev(t.pause for t in self._turns) if n > 1 else 0.0
        interrupt_rate = sum(t.interrupted for t in self._turns) / n

        vec = {"pace": pace, "pause": pause, "pause_var": pause_var, "interrupt_rate": interrupt_rate}
        distances = {}
        best_type, best_dist = None, math.inf
        for label, sig in _SIGNATURES.items():
            dist = math.sqrt(sum(((vec[k] - sig[k]) / _FEATURE_SCALE[k]) ** 2 for k in sig))
            distances[label] = dist
            if dist < best_dist:
                best_type, best_dist = label, dist
        self.last_vector = vec
        self.last_distances = distances
        return best_type

    @staticmethod
    def mix_ratio(text: str) -> float:
        if not text:
            return 0.0
        return len(DEVANAGARI_RE.findall(text)) / max(len(text), 1)
