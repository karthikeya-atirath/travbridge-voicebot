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
import os
import re
from collections import deque
from dataclasses import dataclass
from statistics import median

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

SPEECH_TYPES = ("fast_fluent", "slow_steady", "micro_pause", "interrupter")

# Fallback only, for a caller that can't peek the live session (or when the
# peek fails — see speech_tuner._dynamic_endpointing_ceiling). Since
# CATEGORY_CONFIGS gives each profile its own max_delay now instead of one
# session-wide figure, this constant does NOT reliably describe any given
# session's real ceiling — SpeechClassifier takes the actual live value as
# a constructor argument (attach_speech_tuner passes it in) precisely so
# ceiling_rate isn't computed against a guess that silently diverges from
# whichever profile the session actually started on.
_DEFAULT_MAX_ENDPOINTING_DELAY = float(os.environ.get("MAX_ENDPOINTING_DELAY", "1.2"))
# A turn whose end_of_utterance_delay lands at/near the ceiling above is one
# where DynamicEndpointing waited its full budget before releasing the turn
# — the false-EOT pattern a mid-utterance micro-pauser produces, per the
# mechanism described in speech_tuner.py's module docstring. Bounded on both
# sides: a genuine ceiling hit sits close to the configured max_delay, so an
# upper bound excludes one-off delivery-lag outliers (a late SDK event
# reporting e.g. 9s) that are not actually the endpointer capping out.
_CEILING_NEAR_RATIO = 0.9
_CEILING_FAR_RATIO = 1.25

# pace = words/sec, pause = end-of-utterance delay (s) averaged over the
# window, pause_var = stdev of that pause (catches "micro pause" speakers
# who pause briefly but often, vs. one long hesitant pause), interrupt_rate
# = fraction of the window's turns that barged in on the agent, ceiling_rate
# = fraction of the window's turns whose pause sat at/near the endpointing
# max_delay ceiling.
#
# ceiling_rate exists because pause_var alone stops working once
# DynamicEndpointing converges: a caller who pauses briefly mid-utterance
# repeatedly makes the endpointer wait out its full max_delay turn after
# turn, so end_of_utterance_delay clusters tightly *at* the ceiling instead
# of varying — the exact opposite of what pause_var was meant to detect,
# and close enough to slow_steady's pause centroid to get misclassified as
# slow_steady. Hitting the ceiling repeatedly is itself the signal.
#
# "hesitant" (pace=1.1, pause=1.40) was merged into micro_pause rather than
# just deleted: dropping it outright would have let nearest-centroid matching
# throw halting/self-correcting callers onto whichever of the 4 remaining
# types happened to be closest by accident (most likely slow_steady, since
# it's the next-slowest pace) instead of somewhere deliberately chosen.
# micro_pause's signature below is the midpoint of its old signature and
# hesitant's, so it now covers both "several short frequent pauses" and
# "one long halting pause" callers under a single profile.
_SIGNATURES = {
    "fast_fluent":  {"pace": 3.2, "pause": 0.25, "pause_var": 0.10, "interrupt_rate": 0.1, "ceiling_rate": 0.0},
    "slow_steady":  {"pace": 1.3, "pause": 0.90, "pause_var": 0.10, "interrupt_rate": 0.0, "ceiling_rate": 0.0},
    "micro_pause":  {"pace": 1.55, "pause": 0.925, "pause_var": 0.325, "interrupt_rate": 0.05, "ceiling_rate": 0.6},
    "interrupter":  {"pace": 2.6, "pause": 0.20, "pause_var": 0.15, "interrupt_rate": 0.6, "ceiling_rate": 0.0},
}

# The 4 features live on very different scales (pace ~1-3.2, interrupt_rate
# 0-1, pause_var ~0.1-0.45) — plain Euclidean distance would let pace's raw
# magnitude dominate the match regardless of how relevant the other features
# are. Rescale each by its own spread across _SIGNATURES so every feature
# contributes comparably; self-adjusts if the signatures above are edited.
_FEATURE_KEYS = ("pace", "pause", "pause_var", "interrupt_rate", "ceiling_rate")
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

    def __init__(self, window: int = 5, max_endpointing_delay: float | None = None):
        self.window = window
        # The session's actual live max_delay ceiling, passed in by the
        # caller (attach_speech_tuner peeks it off the real AgentSession) —
        # see _DEFAULT_MAX_ENDPOINTING_DELAY above for why this can no
        # longer default to one module-wide constant. Falls back to that
        # constant only if the caller couldn't determine the live value.
        self._max_endpointing_delay = (
            max_endpointing_delay
            if max_endpointing_delay is not None
            else _DEFAULT_MAX_ENDPOINTING_DELAY
        )
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

    def preview(self) -> tuple[dict[str, float], dict[str, float], str]:
        """Return the rolling classification without advancing the window."""
        return self._classify_with_details()

    def _classify(self) -> str:
        vector, distances, best_type = self._classify_with_details()
        self.last_vector = vector
        self.last_distances = distances
        return best_type

    def _classify_with_details(self) -> tuple[dict[str, float], dict[str, float], str]:
        """Use robust statistics so one delayed SDK event cannot dominate."""
        n = len(self._turns)
        if not n:
            return {}, {}, "slow_steady"
        paces = [t.pace for t in self._turns if t.pace > 0]
        pauses = [t.pause for t in self._turns if t.pause >= 0]
        pace = median(paces) if paces else _SIGNATURES["slow_steady"]["pace"]
        pause = median(pauses) if pauses else _SIGNATURES["slow_steady"]["pause"]
        pause_var = median(abs(value - pause) for value in pauses) if len(pauses) > 1 else 0.0
        interrupt_rate = sum(t.interrupted for t in self._turns) / n
        ceiling = self._max_endpointing_delay
        ceiling_rate = sum(
            1 for value in pauses
            if ceiling * _CEILING_NEAR_RATIO <= value <= ceiling * _CEILING_FAR_RATIO
        ) / n

        vec = {
            "pace": pace,
            "pause": pause,
            "pause_var": pause_var,
            "interrupt_rate": interrupt_rate,
            "ceiling_rate": ceiling_rate,
        }
        distances = {}
        best_type, best_dist = None, math.inf
        for label, sig in _SIGNATURES.items():
            dist = math.sqrt(sum(((vec[k] - sig[k]) / _FEATURE_SCALE[k]) ** 2 for k in sig))
            distances[label] = dist
            if dist < best_dist:
                best_type, best_dist = label, dist
        return vec, distances, best_type

    @staticmethod
    def mix_ratio(text: str) -> float:
        if not text:
            return 0.0
        return len(DEVANAGARI_RE.findall(text)) / max(len(text), 1)