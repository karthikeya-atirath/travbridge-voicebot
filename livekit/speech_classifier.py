"""Classify callers into two turn-taking policies.

The classifier is a weak prior, not the authority for turn completion. It
uses completed-turn pace/pause/interruption metrics to select a broad policy;
LiveKit's dynamic endpointing then learns the caller's pause floor inside that
policy's configured ceiling. Repeated ceiling hits are an explicit signal to
move to the patient policy, even when other metrics look fast.
"""

from __future__ import annotations

import math
import os
import re
from collections import deque
from dataclasses import dataclass
from statistics import median

DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")

SPEECH_TYPES = ("responsive", "patient")

_DEFAULT_MAX_ENDPOINTING_DELAY = float(os.environ.get("MAX_ENDPOINTING_DELAY", "1.0"))
_CEILING_NEAR_RATIO = 0.9
_CEILING_FAR_RATIO = 1.25

# Centroids deliberately describe broad policies rather than trying to tell
# slow, deliberate speech apart from frequent short pauses. Pace and pause
# variance still contribute, but no single feature decides the label.
_SIGNATURES = {
    "responsive": {
        "pace": 2.9,
        "pause": 0.35,
        "pause_var": 0.12,
        "interrupt_rate": 0.35,
        "ceiling_rate": 0.0,
    },
    "patient": {
        "pace": 1.45,
        "pause": 0.80,
        "pause_var": 0.22,
        "interrupt_rate": 0.05,
        "ceiling_rate": 0.30,
    },
}

_FEATURE_KEYS = ("pace", "pause", "pause_var", "interrupt_rate", "ceiling_rate")
_FEATURE_SCALE = {
    key: (max(sig[key] for sig in _SIGNATURES.values()) - min(sig[key] for sig in _SIGNATURES.values())) or 1.0
    for key in _FEATURE_KEYS
}

# Repeated ceiling hits or repeated pauses followed by speech over the bot
# are direct evidence that the caller was cut off mid-thought. Either signal
# overrides the centroid vote so pace cannot mask that behavior.
_PATIENT_EVIDENCE_HITS = 2


@dataclass
class TurnSignal:
    pace: float = 0.0
    pause: float = 0.0
    interrupted: bool = False
    continued_after_bot: bool = False
    mix_ratio: float = 0.0


class SpeechClassifier:
    """Use a rolling window; classify after each full window of new turns."""

    def __init__(self, window: int = 5, max_endpointing_delay: float | None = None):
        self.window = window
        self._max_endpointing_delay = (
            max_endpointing_delay
            if max_endpointing_delay is not None
            else _DEFAULT_MAX_ENDPOINTING_DELAY
        )
        self._turns: deque[TurnSignal] = deque(maxlen=window)
        self._count = 0
        self.last_vector: dict[str, float] = {}
        self.last_distances: dict[str, float] = {}

    def set_max_endpointing_delay(self, delay: float | None) -> None:
        """Keep ceiling-hit measurements aligned with the live policy."""
        if delay is not None and delay > 0:
            self._max_endpointing_delay = delay

    def add_turn(self, signal: TurnSignal) -> str | None:
        self._turns.append(signal)
        self._count += 1
        if len(self._turns) < self.window or self._count % self.window != 0:
            return None
        return self._classify()

    def preview(self) -> tuple[dict[str, float], dict[str, float], str]:
        return self._classify_with_details()

    def _classify(self) -> str:
        vector, distances, best_type = self._classify_with_details()
        self.last_vector = vector
        self.last_distances = distances
        return best_type

    def _classify_with_details(self) -> tuple[dict[str, float], dict[str, float], str]:
        if not self._turns:
            return {}, {}, "patient"

        turns = list(self._turns)
        paces = [turn.pace for turn in turns if turn.pace > 0]
        pauses = [turn.pause for turn in turns if turn.pause >= 0]
        pause = median(pauses) if pauses else _SIGNATURES["patient"]["pause"]
        pace = median(paces) if paces else _SIGNATURES["patient"]["pace"]
        pause_var = median(abs(value - pause) for value in pauses) if len(pauses) > 1 else 0.0
        # Only count very short-pause interruptions as an interruption-style
        # cue. Resuming after a longer pause is evidence for patience instead.
        interrupt_rate = sum(
            turn.interrupted and turn.pause < 0.45 for turn in turns
        ) / len(turns)
        continuation_rate = sum(turn.continued_after_bot for turn in turns) / len(turns)
        ceiling = self._max_endpointing_delay
        ceiling_hits = sum(
            ceiling * _CEILING_NEAR_RATIO <= value <= ceiling * _CEILING_FAR_RATIO
            for value in pauses
        )
        ceiling_rate = ceiling_hits / len(turns)
        vector = {
            "pace": pace,
            "pause": pause,
            "pause_var": pause_var,
            "interrupt_rate": interrupt_rate,
            "ceiling_rate": ceiling_rate,
            "continuation_rate": continuation_rate,
        }

        distances = {
            label: math.sqrt(
                sum(((vector[key] - signature[key]) / _FEATURE_SCALE[key]) ** 2 for key in signature)
            )
            for label, signature in _SIGNATURES.items()
        }
        # Ceiling hits and subsequent speech during the bot's reply are
        # direct evidence that an apparent EOT was premature.
        patient_evidence = (
            ceiling_hits >= _PATIENT_EVIDENCE_HITS
            or sum(turn.continued_after_bot for turn in turns) >= _PATIENT_EVIDENCE_HITS
        )
        best_type = "patient" if patient_evidence else min(distances, key=distances.get)
        return vector, distances, best_type

    @staticmethod
    def mix_ratio(text: str) -> float:
        if not text:
            return 0.0
        return len(DEVANAGARI_RE.findall(text)) / max(len(text), 1)
