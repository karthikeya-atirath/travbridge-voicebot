from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ============================================================
# NORMALIZATION
# ============================================================

def normalize_text(text: str) -> str:
    text = (
        unicodedata
        .normalize("NFKC", text)
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


# ============================================================
# LANGUAGE SIGNALS
# ============================================================

# Single source of truth for "this utterance carries no content of its
# own" — also used by interruption_guard.py, which used to keep a second,
# independently drifting copy of these same lists. Split in two:
#   - BACKCHANNEL_*: non-lexical filler sounds (hmm, uh...) that can
#     never answer anything, not even a yes/no question.
#   - LEXICAL_ACKNOWLEDGEMENT_*: real words (yes, ok, haan...) that ARE a
#     complete answer to a yes/no confirmation, just not to an open
#     information question. interruption_guard cares about this split;
#     turn evolution doesn't, so it exposes the merged view below.
BACKCHANNEL_TOKENS = frozenset({
    "hm", "hmm", "hmmm",
    "mm", "mmm",
    "uh", "uhh", "um", "erm",
    "huh", "ah",

    "हम्म", "हूँ", "हूं", "उम्म",
})


BACKCHANNEL_PHRASES = frozenset({
    "uh huh", "uh-huh",
    "mm hmm", "mm-hmm",
})


LEXICAL_ACKNOWLEDGEMENT_TOKENS = frozenset({
    "yeah", "yes", "yep",
    "ok", "okay",
    "right", "correct",
    "alright",
    "sure",
    "understood",
    "haan", "han",
    "haanji", "hanji",
    "acha", "achha", "accha",
    "theek", "thik",

    "हां", "हाँ",
    "जी",
    "अच्छा",
    "ठीक",
})


LEXICAL_ACKNOWLEDGEMENT_PHRASES = frozenset({
    "got it",
    "i see",
    "thank you",
    "thanks",
    "all right",

    "haan ji",
    "han ji",
    "theek hai",
    "thik hai",

    "ठीक है",
    "जी हाँ",
})


ACKNOWLEDGEMENTS = BACKCHANNEL_TOKENS | LEXICAL_ACKNOWLEDGEMENT_TOKENS
ACKNOWLEDGEMENT_PHRASES = BACKCHANNEL_PHRASES | LEXICAL_ACKNOWLEDGEMENT_PHRASES

# Longest-first so a phrase is stripped whole rather than leaving a
# dangling word behind from a shorter phrase that's also its prefix.
# Computed once here rather than on every acknowledgement check —
# interruption_guard.py's own acknowledgement check reuses this too.
SORTED_ACKNOWLEDGEMENT_PHRASES: tuple[str, ...] = tuple(
    sorted(ACKNOWLEDGEMENT_PHRASES, key=len, reverse=True)
)


STOPWORDS = frozenset({
    "i", "me", "my",
    "we", "our",
    "you", "your",

    "am", "is", "are",
    "was", "were",

    "a", "an", "the",

    # "for"/"of"/"in"/"on"/"at" are largely interchangeable in this domain
    # (STT choosing "in Mumbai" vs "at Mumbai" doesn't change the answer),
    # so dropping them keeps comparison robust to that variation.
    #
    # "to"/"from" are deliberately NOT here, unlike the rest of this
    # preposition group: they're a hard semantic opposite in a travel
    # booking flow (destination vs. departure), not interchangeable
    # phrasing. Stripping them made "flight from Delhi" and "flight to
    # Delhi" collapse to the identical token list ["flight", "delhi"],
    # so a caller correcting their departure/destination was swallowed
    # as a redundant repeat instead of registering as new information.
    "for", "of",
    "in", "on", "at",

    "please",

    # Discourse fillers/address terms that carry no slot content on their
    # own ("yaar two lakh rupees" is just "two lakh rupees" with a filler
    # glued on) — without this, one shows up as an "extra" keyword and the
    # restatement looks like it added new information instead of repeating
    # the same answer. A bare, repeated "Madam"/"Sir" trying to get the
    # agent's attention mid-explanation is the same pattern and was
    # observed misclassifying as INTERRUPT instead of REDUNDANT since
    # nothing else in the utterance carried real content.
    "yaar", "यार",
    "madam", "मैडम", "sir", "जी",
})


# Number/scale words that commonly show up in a restated budget or headcount
# answer, canonicalized to the same token across English, Hindi (Devanagari),
# and Hinglish. A caller who repeats an already-given number in a different
# script ("do lakh" after already saying "two lakh") must compare equal here
# — plain token matching would otherwise see two disjoint keyword sets and
# misclassify the repeat as INTERRUPT instead of REDUNDANT.
#
# Deliberately excludes romanized spellings that collide with ordinary
# English dictionary words, even though they're the "natural" Hinglish
# transliteration for that digit — e.g. "do" (दो/2) is also the English
# auxiliary verb ("do you...", "I do want..."), "sat" (सात/7) is the past
# tense of "sit", "tin"/"teen" (तीन/3) and "char" (चार/4) are ordinary nouns.
# Canonicalizing those would inject a phantom digit keyword into completely
# unrelated English sentences and corrupt the overlap comparison below. The
# Devanagari script form (दो, सात, तीन, चार, ...) has no such collision and
# covers the actual code-switching pattern seen in practice — callers who
# say a number in Hindi say it in Devanagari-transliterated STT output, not
# in ambiguous romanized digits.
NUMBER_CANON: dict[str, str] = {
    "zero": "0", "शून्य": "0",
    "one": "1", "एक": "1", "ek": "1",
    "two": "2", "दो": "2",
    "three": "3", "तीन": "3",
    "four": "4", "चार": "4", "chaar": "4",
    "five": "5", "पांच": "5", "पाँच": "5", "paanch": "5", "panch": "5",
    "six": "6", "छह": "6", "छः": "6", "chhah": "6", "chhe": "6",
    "seven": "7", "सात": "7", "saat": "7",
    "eight": "8", "आठ": "8", "aath": "8", "aat": "8",
    "nine": "9", "नौ": "9", "nau": "9",
    "ten": "10", "दस": "10", "das": "10",
    "twenty": "20", "बीस": "20", "bis": "20",
    "thirty": "30", "तीस": "30", "tees": "30",
    "forty": "40", "चालीस": "40", "chaalis": "40",
    "fifty": "50", "पचास": "50", "pachaas": "50", "pachas": "50",
    "hundred": "hundred", "सौ": "hundred", "sau": "hundred",
    "thousand": "thousand", "हज़ार": "thousand", "हजार": "thousand", "hazar": "thousand",
    "lakh": "lakh", "लाख": "lakh", "lac": "lakh",
    "crore": "crore", "करोड़": "crore", "करोड": "crore",
    "rupees": "rupees", "rupee": "rupees", "रुपये": "rupees", "रुपए": "rupees",
    "rupaye": "rupees", "rs": "rupees", "inr": "rupees",
}


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
    # complexity tracking word lists (correction markers, a trailing "?")
    # that drifted out of sync with how real STT output actually looks.
    INTERRUPT = "interrupt"


@dataclass(frozen=True, slots=True)
class TurnAnalysis:
    delta: TurnDelta
    reason: str


# ============================================================
# ACTIVE COMMITTED USER TURN
# ============================================================

@dataclass(slots=True)
class ActiveTurn:

    text: str = ""
    normalized: str = ""

    # Content tokens only (stopwords/acknowledgements filtered, numbers
    # canonicalized), in original order — order matters for
    # is_subsequence() below, unlike a keyword set.
    tokens: tuple[str, ...] = ()

    created_at: float = field(
        default_factory=time.monotonic
    )

    def update(self, text: str) -> None:

        self.text = text.strip()
        self.normalized = normalize_text(text)

        self.tokens = extract_keyword_tokens(
            self.normalized
        )


# ============================================================
# HELPERS
# ============================================================

def extract_keyword_tokens(text: str) -> tuple[str, ...]:

    return tuple(
        NUMBER_CANON.get(token, token)
        for token in tokenize(text)
        if token not in STOPWORDS
        and token not in ACKNOWLEDGEMENTS
    )


def is_subsequence(
    incoming: tuple[str, ...],
    active: tuple[str, ...],
) -> bool:
    """True if every incoming token occurs in active, in the same
    relative order (not necessarily contiguous) — i.e. incoming adds
    nothing that active didn't already say.

    Filtering through extract_keyword_tokens first (stopwords/
    acknowledgements dropped, numbers canonicalized) is what lets, say,
    "Yeah, two lakh" register as a subsequence of "two lakh rupees"
    instead of failing on the leading filler word alone.
    """

    if not incoming:
        return True

    index = 0

    for token in active:

        if token == incoming[index]:
            index += 1

            if index == len(incoming):
                return True

    return False


# ============================================================
# ANALYZER
# ============================================================

class TurnEvolutionAnalyzer:
    """
    Tracks the latest COMMITTED user meaning.

    Important:

    Interim STT is evaluated but never committed.

    Example:

        committed:
            "I want to travel from Bangalore"

        overlap:
            "Bangalore"

        -> REDUNDANT

        overlap:
            "Bangalore tomorrow"

        -> INTERRUPT (new active turn)

        overlap:
            "No, Mumbai"

        -> INTERRUPT (new active turn)
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
        analysis = self._classify_overlap(
            transcript, is_final=is_final, expects_short_answer=expects_short_answer
        )
        self.last_analysis = analysis
        return analysis

    def _classify_overlap(
        self,
        transcript: str,
        *,
        is_final: bool,
        expects_short_answer: bool = False,
    ) -> TurnAnalysis:

        raw = transcript.strip()

        incoming = normalize_text(raw)

        # Save latest STT candidate only.
        #
        # DO NOT modify active_turn here.
        self.candidate_text = raw
        self.candidate_normalized = incoming

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

        # EXPECTED SHORT ANSWER -> INTERRUPT
        #
        # A generic "yeah"/"haan" IS a complete answer when the agent just
        # asked a yes/no question — it isn't a restatement of anything, so
        # it must never be measured against the active turn at all.
        if expects_short_answer:

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
            )

        # CURRENT TOKENS ARE AN ORDERED SUBSEQUENCE OF PREVIOUS TOKENS
        # -> REDUNDANT
        #
        # active:   "I want to travel from Bangalore"
        # incoming: "Bangalore"                          -> REDUNDANT
        # incoming: "Yeah, Bangalore"                     -> REDUNDANT
        # incoming: "not Bangalore"                       -> INTERRUPT
        #   ("not" never appears in active, so it isn't a subsequence —
        #   a correction naturally falls out of this check on its own,
        #   without needing a separate word-list category for it.)
        incoming_tokens = extract_keyword_tokens(incoming)

        if is_subsequence(incoming_tokens, active.tokens):

            return TurnAnalysis(
                TurnDelta.REDUNDANT,
                "existing_information",
            )

        # OTHERWISE -> INTERRUPT
        return TurnAnalysis(
            TurnDelta.INTERRUPT,
            "new_information",
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

