from __future__ import annotations

import threading
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(text: str) -> str:
    text = (
        unicodedata
        .normalize("NFKC", text or "")
        .casefold()
        .replace("’", "'")
    )

    chars: list[str] = []

    for ch in text:
        cat = unicodedata.category(ch)

        if (
            ch.isspace()
            or ch in {"'", "-"}
            or cat.startswith(("L", "M", "N"))
        ):
            chars.append(ch)
        else:
            chars.append(" ")

    return " ".join("".join(chars).split())


def tokenize(text: str) -> list[str]:
    return normalize_text(text).split()


def _has_real_content(text: str) -> bool:
    """True if at least one token is long enough to plausibly be real
    content rather than a garbled STT scrap (background noise, a clipped
    syllable, a single stray letter). Deliberately does not filter
    backchannels/acknowledgements — that classification belongs to
    interruption_guard.py, which decides whether to call into this module
    at all; by the time text reaches here it's already assumed to be a
    real candidate worth comparing.
    """
    return any(len(token) >= 2 for token in tokenize(text))


# ============================================================
# SEMANTIC MODEL
# ============================================================

# Small multilingual sentence-embedding model — covers English, Hindi
# (Devanagari), and Hinglish well enough for short conversational
# utterances, and is cheap enough (~10ms/sentence on CPU) to run inline on
# every barge-in candidate without adding noticeable latency to the
# interruption path.
_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Empirically calibrated against normalize_text()'d pairs (lowercased,
# punctuation stripped — the actual form compared at runtime; raw-case
# text scores meaningfully differently on this model). Below this,
# utterances reliably read as unrelated/new content; above it, they
# reliably read as the same content restated.
#
# Not perfect in both directions, and can't be made so by moving this
# number: a short slot-value correction that keeps most of the sentence
# unchanged ("book me a flight for next Friday" replacing "...next
# Monday") scores *higher* on this model than several genuine paraphrase
# repeats, because the model measures topical/lexical closeness, not
# whether the one differing word changes the real-world meaning. This is
# the balance point across a wide calibration set of paraphrases,
# extensions, and corrections in this domain — it correctly separates the
# clear cases (obvious repeats below ~0.5, obvious topic changes) but a
# same-structure correction can still slip through as REDUNDANT.
SEMANTIC_SIMILARITY_THRESHOLD = 0.60

# Cosine alone can't tell "same words, one slot changed" from a paraphrase, so
# it is not allowed to decide alone when the new speech contains content the
# active turn never said (see _novel_tokens). In that case a much higher bar
# applies — only a near-verbatim paraphrase reaches it — and a novel token
# that looks like a slot value (digit, month, weekday) rules redundancy out
# altogether. Wrongly calling speech redundant silently drops the caller's
# turn; wrongly calling it new only costs an extra reply, so the bias is
# deliberately toward "new".
NOVEL_CONTENT_SIMILARITY_THRESHOLD = 0.85

# Words that never carry the information of an utterance: pronouns, auxiliaries,
# question scaffolding and politeness (English + Hinglish + Hindi). A token
# outside this set that the active turn didn't contain counts as novel content.
_SCAFFOLD_TOKENS = frozenset({
    "i", "me", "my", "we", "our", "you", "your", "it", "this", "that",
    "am", "is", "are", "was", "were", "be", "do", "does", "did", "can",
    "could", "would", "will", "shall", "should", "may", "might", "have",
    "has", "had", "a", "an", "the", "to", "of", "in", "on", "at", "for",
    "and", "or", "so", "then", "well", "now", "just", "also", "too",
    "please", "sorry", "pardon", "excuse", "again", "tell", "say", "said",
    "hello", "hi", "hey", "yes", "no", "yeah", "ok", "okay", "yaar",
    "sir", "madam", "itself", "only", "actually",
    "what", "how", "why", "when", "where", "which", "who",
    "मैं", "मुझे", "आप", "आपको", "है", "हैं", "का", "की", "के", "में",
    "से", "को", "और", "या", "तो", "भी", "जी", "क्या", "यार",
    "kya", "hai", "hain", "ka", "ki", "ke", "mein", "se", "ko", "aur",
    "ya", "to", "bhi", "ji", "aap", "mujhe", "main",
})

_SLOT_TOKENS = frozenset({
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
    "sunday", "today", "tomorrow", "yesterday",
})


def _is_slot_token(token: str) -> bool:
    return token in _SLOT_TOKENS or any(ch.isdigit() for ch in token)


def _novel_tokens(incoming_tokens: list[str], active_tokens: list[str]) -> list[str]:
    """Content tokens in the new speech that the active turn never said."""
    seen = set(active_tokens)
    return [
        token
        for token in incoming_tokens
        if token not in seen and token not in _SCAFFOLD_TOKENS
    ]

_model_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(_MODEL_NAME, device="cpu")
    return _model


def warm_model() -> None:
    """Force the lazy-loaded embedding model to load now, synchronously.

    Meant to be called from a worker's prewarm step (off the job's event
    loop) rather than left to load on the first real barge-in of a session
    — that first load can take several seconds, and doing it inline on
    classify_overlap's caller (interruption_guard.py, on the asyncio event
    loop) stalls the whole process, starving the STT/LLM connections that
    are mid-flight at that moment.
    """
    _get_model()


def _embed(text: str) -> np.ndarray:
    return _get_model().encode(text, normalize_embeddings=True)


def semantic_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


# ============================================================
# RESULT TYPES
# ============================================================

class TurnDelta(str, Enum):

    # User did not add any meaning — safe to ignore for both interruption
    # and reply purposes.
    REDUNDANT = "redundant"

    # Anything else: a correction, an extension, a genuinely new turn.
    # These used to be distinguished into their own categories, but
    # nothing downstream ever branched on which one it was — only on
    # REDUNDANT vs. not — so the extra categories were unverified
    # complexity tracking word lists that drifted out of sync with how
    # real STT output actually looks.
    INTERRUPT = "interrupt"


@dataclass(frozen=True, slots=True)
class TurnAnalysis:
    delta: TurnDelta
    reason: str
    # Raw cosine similarity for the branches that computed one (semantic
    # comparison, exact_repeat) — None everywhere else (empty, no active
    # turn, short-answer bypass, etc). The reason string already carries
    # the percentage in human-readable form; this is the same number as a
    # float for callers that want to log/threshold on it directly instead
    # of parsing it back out of the string.
    similarity: Optional[float] = None


# ============================================================
# ACTIVE COMMITTED USER TURN
# ============================================================

@dataclass(slots=True)
class ActiveTurn:

    text: str = ""
    normalized: str = ""

    # Sentence embedding of `normalized`, computed once here rather than
    # on every classify call against it — an overlap can be classified
    # several times (interim STT re-evaluation) before the turn changes.
    embedding: Optional[np.ndarray] = field(default=None, repr=False)

    created_at: float = field(
        default_factory=time.monotonic
    )

    def update(self, text: str) -> None:

        self.text = text.strip()
        self.normalized = normalize_text(text)
        self.embedding = _embed(self.normalized)


# ============================================================
# ANALYZER
# ============================================================

class SemanticCheckAnalyzer:
    """
    Tracks the latest COMMITTED user meaning and decides whether new
    overlapping speech is a semantic restatement of it (REDUNDANT) or
    genuinely different content (INTERRUPT).

    Important:

    Interim STT is evaluated but never committed.

    The comparison itself is a sentence-embedding cosine similarity
    against the active turn — see SEMANTIC_SIMILARITY_THRESHOLD — rather
    than a literal keyword/subsequence match. Cosine is not trusted alone,
    though: speech containing content words the active turn never said is
    treated as new unless it is a near-verbatim paraphrase (see
    NOVEL_CONTENT_SIMILARITY_THRESHOLD), so a loose paraphrase ("heading out
    of Bangalore" vs. "I want to travel from Bangalore") now reads as new —
    the deliberate cost of never silently dropping a changed slot value.

    Example:

        committed:
            "I want to travel from Bangalore"

        overlap:
            "Bangalore"

        -> REDUNDANT (high similarity to the active turn)

        overlap:
            "actually can you tell me about hotels instead"

        -> INTERRUPT (low similarity — a different topic entirely)

    See SEMANTIC_SIMILARITY_THRESHOLD for where this does and doesn't
    work: a same-structure correction ("...next Friday" replacing "...next
    Monday") can still score high enough to read as REDUNDANT, since nearly
    every other word in the sentence is unchanged.
    """

    def __init__(self) -> None:

        self.active_turn: Optional[ActiveTurn] = None

        # Current overlapping user's STT.
        #
        # This is intentionally separate from active_turn.
        self.candidate_text: str = ""
        self.candidate_normalized: str = ""

        # The most recent classify_overlap() verdict, held so the caller
        # can later commit the matching finalized transcript with
        # commit_overlap() instead of blindly overwriting active_turn —
        # see consume_last_analysis().
        self.last_analysis: Optional[TurnAnalysis] = None

    # ========================================================
    # NORMAL USER TURN
    # ========================================================

    def commit_user_turn(
        self,
        transcript: str,
    ) -> None:

        transcript = transcript.strip()

        if not transcript:
            return

        turn = ActiveTurn()
        turn.update(transcript)

        self.active_turn = turn

    # ========================================================
    # USER STARTS SPEAKING OVER BOT
    # ========================================================

    def begin_overlap(self) -> None:

        self.candidate_text = ""
        self.candidate_normalized = ""
        self.last_analysis = None

    def consume_last_analysis(self) -> Optional[TurnAnalysis]:
        """Read-and-clear the last classify_overlap() verdict.

        Cleared on read so a verdict is only ever applied to the one
        finalized transcript it was computed for, never reused for an
        unrelated later turn.
        """
        analysis = self.last_analysis
        self.last_analysis = None
        return analysis

    # ========================================================
    # CLASSIFICATION
    # ========================================================

    def classify_overlap(
        self,
        transcript: str,
        *,
        is_final: bool,
        expects_short_answer: bool = False,
    ) -> TurnAnalysis:
        raw = transcript.strip()
        # Save latest STT candidate only.
        #
        # DO NOT modify active_turn here.
        self.candidate_text = raw
        self.candidate_normalized = normalize_text(raw)

        analysis = self._classify_against_active(
            raw, expects_short_answer=expects_short_answer
        )
        self.last_analysis = analysis
        return analysis

    def classify_final_turn(
        self,
        transcript: str,
        *,
        expects_short_answer: bool = False,
    ) -> TurnAnalysis:
        """Classify a *finalized* user turn against the active committed
        turn, independent of the live barge-in overlap tracking above.

        Distinct from classify_overlap() in mechanism, not in when it's
        used. Two callers, both in interruption_guard.py:

        - is_redundant_turn(), for a turn that actually talked over the
          agent's current turn (turn_overlapped_agent) — see that
          docstring for why comparing a turn that arrived *after* the agent
          had already gone quiet produces false positives (the same words
          can be a legitimate fresh reply to a different, later question).

        - recover_dropped_turn(), for a transcript LiveKit's own pipeline
          silently dropped because it arrived while the current speech was
          non-interruptible — by construction that always means it
          overlapped active speech, so it calls this directly rather than
          through is_redundant_turn()'s turn_overlapped_agent gate (that
          flag is typically already reset by the time the drop warning
          reaches it).

        This method itself doesn't enforce any scoping; it just does the
        comparison once the caller has decided it's warranted. No side
        effects on candidate_text/last_analysis — those belong solely to
        the overlap-commit workflow.
        """
        return self._classify_against_active(
            transcript, expects_short_answer=expects_short_answer
        )

    def _classify_against_active(
        self,
        transcript: str,
        *,
        expects_short_answer: bool = False,
    ) -> TurnAnalysis:

        raw = transcript.strip()

        incoming = normalize_text(raw)

        # EMPTY -> REDUNDANT
        if not incoming:

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                "empty",
            )

        # NO ACTIVE TURN -> INTERRUPT
        #
        # Nothing previously committed to compare against.
        if self.active_turn is None:

            return TurnAnalysis(
                TurnDelta.INTERRUPT,
                "no_active_turn",
            )

        # EXPECTED SHORT ANSWER -> INTERRUPT (usually)
        #
        # A generic "yeah"/"haan" IS a complete answer when the agent just
        # asked a yes/no confirmation, or when it's the literal repeat the
        # agent asked for during clarification — neither is a restatement
        # of anything, so it must never be measured against the active
        # turn at all. Callers only set this for AWAITING_CONFIRMATION/
        # AWAITING_CLARIFICATION (see interruption_guard.py), which already
        # owns the backchannel/acknowledgement classification that decides
        # whether this branch is even reached.
        #
        # Still requires at least one token of real length, though: a
        # single-character STT blip (background noise, a clipped syllable,
        # a TTS-echo scrap) must not stop playback just as readily as a
        # real "haan"/"no".
        if expects_short_answer:

            if not _has_real_content(incoming):

                return TurnAnalysis(
                    TurnDelta.REDUNDANT,
                    "insufficient_content_for_expected_answer",
                )

            return TurnAnalysis(
                TurnDelta.INTERRUPT,
                "expected_answer",
            )

        active = self.active_turn

        # EXACT SAME NORMALIZED TEXT -> REDUNDANT
        if incoming == active.normalized:

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                "exact_repeat",
                similarity=1.0,
            )

        # A stray single-character/garbled fragment is far more likely a
        # garbled STT scrap than a real new utterance — never worth an
        # embedding call, let alone trusting it to interrupt playback.
        if not _has_real_content(incoming):

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                "insufficient_content",
            )

        # SINGLE WORD THAT LITERALLY APPEARS IN THE ACTIVE TURN -> REDUNDANT
        #
        # A one-word repeat ("Bangalore" against "I want to travel from
        # Bangalore") is usually already caught by the semantic comparison
        # below, but a single word's embedding is diluted once the active
        # turn is long (a full multi-slot sentence), and can occasionally
        # land under threshold even though it's a literal, unambiguous
        # match — skip the model entirely and settle it deterministically.
        # A word that ISN'T already in the active turn ("Hyderabad") falls
        # through to the real comparison below, same as any other case.
        incoming_tokens = tokenize(incoming)

        if (
            len(incoming_tokens) == 1
            and incoming_tokens[0] in tokenize(active.normalized)
        ):

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                "single_word_repeat",
                similarity=1.0,
            )

        # SEMANTIC COMPARISON -> REDUNDANT if it reads as the same meaning
        # as the active turn, INTERRUPT otherwise. See
        # SEMANTIC_SIMILARITY_THRESHOLD for the calibration/tradeoffs.
        similarity = semantic_similarity(_embed(incoming), active.embedding)
        similarity_pct = round(similarity * 100, 1)

        # NOVEL CONTENT -> not redundant unless a near-verbatim paraphrase.
        #
        # The model scores "can i go in september" against "can i come on
        # october" at ~75% because the sentence shape matches, yet the one
        # differing word IS the new information. If the caller said something
        # the active turn never contained, cosine alone must not suppress it.
        novel = _novel_tokens(incoming_tokens, tokenize(active.normalized))
        if novel and (
            similarity < NOVEL_CONTENT_SIMILARITY_THRESHOLD
            or any(_is_slot_token(token) for token in novel)
        ):

            return TurnAnalysis(
                TurnDelta.INTERRUPT,
                f"novel_content:{similarity_pct}%:{','.join(novel[:4])}",
                similarity=similarity,
            )

        if similarity >= SEMANTIC_SIMILARITY_THRESHOLD:

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                f"semantic_redundant:{similarity_pct}%",
                similarity=similarity,
            )

        return TurnAnalysis(
            TurnDelta.INTERRUPT,
            f"semantic_new_information:{similarity_pct}%",
            similarity=similarity,
        )

    # ========================================================
    # COMMIT FINAL OVERLAP
    # ========================================================

    def commit_overlap(
        self,
        transcript: str,
        analysis: TurnAnalysis,
    ) -> None:
        """
        Call ONLY after final STT.

        REDUNDANT: do nothing — the active turn already covers this.
        INTERRUPT: new meaning; it becomes the active turn.
        """

        transcript = transcript.strip()

        if not transcript or analysis.delta == TurnDelta.REDUNDANT:

            self.clear_overlap()
            return

        self.commit_user_turn(
            transcript
        )

        self.clear_overlap()

    # ========================================================

    def clear_overlap(self) -> None:

        self.candidate_text = ""
        self.candidate_normalized = ""
