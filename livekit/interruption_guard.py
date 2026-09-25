"""Deterministic, low-latency interruption policy for the voice agent.

The guard owns semantic interruption decisions. LiveKit's VAD gate remains a
conservative safety net for speech the guard does not recognize. Keeping these
responsibilities separate prevents the speech tuner from making fillers more
likely to stop playback.

Reading order (each is a `# ====...====`-banner section below):
  1. Backchannel/acknowledgement/filler vocabulary + STOPWORDS.
  2. AgentPosture / InterruptDecision enums and the interrupt-threshold
     constants (one per posture, plus the shared filler/command sets).
  3. Bot-speech classification: the regex patterns (grouped by question
     type) + classify_assistant_sentence/infer_agent_posture, which decide
     what posture the bot's own generated text puts it in.
  4. classify_interruption: the pure, stateless decision function for live
     overlap (does incoming user speech interrupt the bot right now).
  5. InterruptionGuard: the stateful class wiring the above into LiveKit's
     session events, plus finalized-turn handling
     (is_non_answer_for_current_posture, is_redundant_turn,
     recover_dropped_turn) and the actual session.interrupt() side effects.
  6. attach_interruption_guard: wiring entry point used by
     agent_stt_llm_tts_v1.py.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Final

if TYPE_CHECKING:
    from livekit.agents import AgentSession

from app_logger import applog
from semantic_check import (
    SemanticCheckAnalyzer,
    TurnDelta,
    normalize_text,
    tokenize,
)


# ============================================================
# BACKCHANNEL / ACKNOWLEDGEMENT / FILLER DETECTION
#
# This guard is the sole owner of "does this speech carry any content of
# its own, or is it just noise/filler relative to the bot's current
# posture" — semantic_check.py only ever sees text the guard has already
# decided is worth comparing against the active turn. Split in two:
#   - BACKCHANNEL_*: non-lexical filler sounds (hmm, uh...) that can
#     never answer anything, not even a yes/no question.
#   - LEXICAL_ACKNOWLEDGEMENT_*: real words (yes, ok, haan...) that ARE a
#     complete answer to a yes/no confirmation, just not to an open
#     information question.
# ============================================================

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


# Lexical fillers split by what they can answer. Only AFFIRMATION_* words
# are a reply to a yes/no confirmation; REACTION_* words ("nice", "acha",
# "oh great", "got it") just acknowledge what was said and answer nothing.
AFFIRMATION_TOKENS = frozenset({
    "yeah", "yes", "yep",
    "ok", "okay",
    "right", "correct",
    "alright",
    "sure",
    "haan", "han", "haa",
    "haanji", "hanji",
    "theek", "thik",

    "हां", "हाँ", "हा",
    "जी",
    "ठीक",
})


AFFIRMATION_PHRASES = frozenset({
    "all right",
    "haan ji",
    "han ji",
    "theek hai",
    "thik hai",

    "ठीक है",
    "जी हाँ",
})


REACTION_TOKENS = frozenset({
    "understood",
    # Bare enthusiasm interjections ("yeah, great") — without these, "great"
    # survives stopword/ack stripping as a lone 5-letter "new" token and gets
    # misclassified as new_information, forcing a second, near-duplicate LLM
    # turn and tool call for what was just an acknowledgement.
    "great", "cool", "nice", "awesome", "perfect", "good",
    "oh", "ohh", "oho", "wow", "arre",
    "acha", "achha", "accha",

    "अच्छा", "अरे", "ओह", "वाह",
})


REACTION_PHRASES = frozenset({
    "got it",
    "i see",
    "thank you",
    "thanks",
})


LEXICAL_ACKNOWLEDGEMENT_TOKENS = AFFIRMATION_TOKENS | REACTION_TOKENS
LEXICAL_ACKNOWLEDGEMENT_PHRASES = AFFIRMATION_PHRASES | REACTION_PHRASES


ACKNOWLEDGEMENTS = BACKCHANNEL_TOKENS | LEXICAL_ACKNOWLEDGEMENT_TOKENS
ACKNOWLEDGEMENT_PHRASES = BACKCHANNEL_PHRASES | LEXICAL_ACKNOWLEDGEMENT_PHRASES

# Longest-first so a phrase is stripped whole rather than leaving a
# dangling word behind from a shorter phrase that's also its prefix.
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
    # and carry no content of their own for the purposes of
    # _is_meaningful_final_reply's "does this have any real content"
    # check below.
    #
    # "to"/"from" are deliberately NOT here: they're a hard semantic
    # opposite in a travel booking flow (destination vs. departure), so
    # treating them as real content is the safer default.
    "for", "of",
    "in", "on", "at",

    "please",

    # Discourse fillers/address terms that carry no content on their own
    # ("yaar two lakh rupees" is just "two lakh rupees" with a filler
    # glued on) — without this, one shows up as "real content" and a bare
    # attention-getting "Madam"/"Sir" reads as a meaningful reply.
    "yaar", "यार",
    "madam", "मैडम", "sir", "जी",

    # Hesitation fillers glued onto a restated value the same way
    # ("yahan Hyderabad" / "यहां हैदराबाद" = "here, Hyderabad"; "ya
    # Hyderabad" / "या हैदराबाद" = "or, Hyderabad" as a hedge) — without
    # this they'd count as "real content" on their own.
    "yahan", "yahaan", "यहां", "यहाँ",
    "ya", "या",
})


def extract_keyword_tokens(text: str) -> tuple[str, ...]:
    """Tokens that carry real content — stopwords and acknowledgements
    filtered out. Used only to answer "does this text say anything at
    all", not to compare meaning between two texts (that's
    semantic_check.py's job).
    """
    return tuple(
        token
        for token in tokenize(text)
        if token not in STOPWORDS
        and token not in ACKNOWLEDGEMENTS
    )


class AgentPosture(str, Enum):
    EXPLAINING = "explaining"
    AWAITING_ANSWER = "awaiting_answer"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_CLARIFICATION = "awaiting_clarification"


class InterruptDecision(str, Enum):
    INTERRUPT = "interrupt"
    IGNORE = "ignore"
    WAIT = "wait"


# Seconds of stable interim speech required before an unfinalized transcript
# is trusted enough to cut the agent off. These only gate the interim
# *stability window* while explaining — the two-word floor they're built on
# top of also applies to a final transcript there (see classify_interruption):
# Deepgram's is_final marks a settled chunk (gated by endpointing_ms, as low
# as 200-250ms of silence, see speech_tuner.CATEGORY_CONFIGS), not a settled
# utterance, so it fires on an ordinary mid-sentence breathing pause just as
# readily as at a real end of turn.
# Ordered by how much benefit of the doubt each posture owes the agent:
# explanations are the least urgent to interrupt (losing mid-explanation
# content costs the most), confirmations the most urgent (the agent is
# actively blocked on a short yes/no). Raised back up from 0.20/0.10/0.10s:
# those values sat barely above the two-word floor already required, so an
# ordinary false-start or STT mis-hear had almost no window to self-correct
# before triggering a cutoff, producing frequent false interruptions on
# ordinary breathing pauses and TTS-bleed fragments. These values trade a
# small amount of extra barge-in latency for materially fewer false
# interruptions; is_final trust for AWAITING_ANSWER/AWAITING_CONFIRMATION is
# additionally gated by _is_meaningful_final_reply below rather than
# accepted unconditionally.
EXPLANATION_INTERRUPT_THRESHOLD: Final[float] = 0.60
QUESTION_INTERRUPT_THRESHOLD: Final[float] = 0.40
CONFIRMATION_INTERRUPT_THRESHOLD: Final[float] = 0.30
# A lone interim word is the likeliest STT hallucination / noise, so it needs
# a longer sustained-speech window than a multi-word reply before it is
# trusted. Only used where the user is expected to reply (AWAITING_ANSWER /
# AWAITING_CONFIRMATION) and never mid-explanation; without it a one-word
# reply ("yes", "hello?") had to wait for the STT final (seconds).
SINGLE_WORD_REPLY_THRESHOLD: Final[float] = 0.80


# Decisions final without a semantic comparison against the active turn.
SEMANTIC_EXEMPT_REASONS: Final[frozenset[str]] = frozenset(
    {"explicit_command", "affirmation_answers_confirmation", "attention_getter"}
)

IMMEDIATE_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "wait", "stop", "pause", "cancel", "repeat", "wrong", "hold on",
        "one moment", "ruko", "rukiye", "roko", "suniye", "galat",
        "रुको", "रुकिए", "रोकिए", "सुनिए", "गलत", "बंद",
    }
)

# Softening words that can lead an otherwise-bare command ("please stop",
# "okay wait") without changing its meaning. Stripped from the front only,
# so a command is still recognized by matching the same IMMEDIATE_COMMANDS
# set after removing these — not a second, hardcoded word list.
COMMAND_PREFIX_FILLERS: Final[frozenset[str]] = frozenset(
    {"please", "just", "okay", "ok", "अच्छा"}
)


# Attention-getters: the caller calling out to the bot ("hello?", "are you
# there", "sorry?") — not an answer to anything, and not filler either.
# They must land immediately: a lone interim word is normally held back
# (SINGLE_WORD_REPLY_THRESHOLD, or forever behind an explanatory prefix, since
# _stable() wants 2+ words), which is exactly what made "hello" wait for the
# STT final. The vocabulary is tiny and unambiguous, which is what makes
# trusting one interim word safe here — the same reasoning as
# IMMEDIATE_COMMANDS. Deliberately kept apart from ACKNOWLEDGEMENTS:
# "hello" answers no question and must never be stripped as filler.
ATTENTION_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "hello", "hallo", "helo", "hullo", "hi", "hey", "oi",
        "suno", "sorry", "pardon",
        "हेलो", "हैलो", "हलो", "सुनो", "सुनिए",
    }
)

ATTENTION_PHRASES: Final[frozenset[str]] = frozenset(
    {
        "excuse me",
        "are you there", "you there", "anyone there", "is anyone there",
        "can you hear me", "do you hear me", "hear me",
        "एक्सक्यूज़ मी", "एक्सक्यूज मी",
    }
)

_SORTED_ATTENTION_PHRASES: Final[tuple[str, ...]] = tuple(
    sorted(ATTENTION_PHRASES, key=len, reverse=True)
)

# Seconds of user speech before a lone interim attention-getter is trusted.
# Short enough to be near-instant, long enough that a clipped blip that STT
# happened to render as "hi" doesn't cut the bot off. A final transcript is
# trusted immediately regardless.
ATTENTION_GETTER_MIN_DURATION: Final[float] = 0.20


def is_attention_getter(text: str) -> bool:
    """True when normalized `text` is made *entirely* of attention-getting
    words/phrases ("hello", "hello are you there", "excuse me")."""
    remaining = f" {text} "
    atoms = 0
    for phrase in _SORTED_ATTENTION_PHRASES:
        needle = f" {phrase} "
        while needle in remaining:
            atoms += 1
            remaining = remaining.replace(needle, " ", 1)
    tokens = remaining.split()
    if not atoms and not tokens:
        return False
    return all(token in ATTENTION_TOKENS for token in tokens)


def _strip_command_prefix_fillers(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0] in COMMAND_PREFIX_FILLERS:
        tokens = tokens[1:]
    return " ".join(tokens)


# Negative replies answer a yes/no question just as completely as an
# acknowledgement does, but must stay out of the ACKNOWLEDGEMENTS/STOPWORDS
# lists above: those are stripped out wherever this module treats text as
# "no real content of its own", and "no"/"nahi" very much is real content —
# it's a correction like "no, Mumbai" contrasting with what was already
# said, not filler. Kept as its own set instead, used only by
# _is_meaningful_final_reply below.
NEGATION_TOKENS: Final[frozenset[str]] = frozenset(
    {"no", "nope", "nah", "not", "nahi", "nahin", "naa", "नहीं", "ना"}
)


def _is_meaningful_final_reply(text: str, *, ambiguous_acknowledgement: bool) -> bool:
    """Gate for trusting a finalized transcript enough to force an
    interruption in AWAITING_CONFIRMATION/AWAITING_ANSWER.

    is_final only means Deepgram's endpointing timer fired on a settled
    chunk — not that the words are a real reply (see the note above
    EXPLANATION_INTERRUPT_THRESHOLD). Trusting every is_final unconditionally
    let a stray single-word STT fragment (background noise, a clipped
    syllable, a TTS-echo fragment that slipped past _looks_like_tts_echo)
    cut the agent off just because it happened to arrive marked final. A
    recognized yes/no acknowledgement, an explicit negation, or any real
    (non-stopword, non-acknowledgement) content token are each independently
    sufficient signal; anything else should wait for more speech instead.
    """
    if ambiguous_acknowledgement:
        return True
    if any(token in NEGATION_TOKENS for token in text.split()):
        return True
    return bool(extract_keyword_tokens(text))


def _looks_like_tts_echo(transcript_normalized: str, assistant_text_normalized: str) -> bool:
    """True when the heard phrase is very likely the bot's own voice
    leaking back into the mic (speaker bleed / line echo) rather than real
    user speech: a multi-word chunk that appears verbatim inside the
    sentence the bot is currently speaking.

    Restricted to matches of 2+ words on purpose: a single shared word
    (e.g. the caller answering "Mumbai" right after the bot said "...to
    Mumbai?") is exactly the kind of real short answer this must not
    swallow — genuine echo reproduces a run of consecutive words, not one
    coincidentally-shared token.
    """
    if not transcript_normalized or not assistant_text_normalized:
        return False
    if word_count(transcript_normalized) < 2:
        return False
    return transcript_normalized in assistant_text_normalized


@dataclass(frozen=True, slots=True)
class PostureResult:
    posture: AgentPosture
    reason: str


# NOTE ON \b AND DEVANAGARI: Python's \b only fires on a transition between
# a "word" char and a non-word char, and dependent Hindi vowel signs
# (matras: े ा ी etc.) are combining marks — not counted as word chars. A
# trailing \b right after a Hindi word ending in one of those (आपके, कैसे,
# कितने, बताये...) never matches. Every pattern below that needs a boundary
# after a Hindi alternative uses (?=\s|\Z) instead — safe for the English/
# Hinglish alternatives too, since normalize_text() already strips all
# punctuation, so a matched word is only ever followed by a space or the
# end of the string.

# ---- Information / WH-questions (English + Hindi + Hinglish) ----

# Requires actual information from the user (English, Hindi Devanagari, and
# Hinglish WH-questions). Optional discourse openers ("so", "okay", "तो")
# are skipped so they don't shadow the WH-word that follows.
INFORMATION_START_PATTERN = re.compile(
    r"^(?:(?:so|now|okay|ok|well|then|तो|अच्छा)\s+)*"
    r"(?:"
    r"what|why|when|where|who|how|which|whose"
    r"|"
    r"कब|कहाँ|कहां|क्यों|कैसे|कौन|किस|कितना|कितने|कितनी|किधर"
    r"|"
    r"kab|kahan|kahaan|kyun|kyu|kaise|kaun|kis|"
    r"kitna|kitne|kitni|kidhar"
    r")(?=\s|\Z)",
    re.IGNORECASE,
)

# Hindi questions often lead with "aap"/"tum" before the WH word:
# "Aap kahan jaana chahte hain?", "आप कितने दिन रुकेंगे?"
HINDI_INFORMATION_PATTERN = re.compile(
    r"^(?:"
    r"aap|ap|tum|aapko|"
    r"आप|तुम|आपको"
    r")\s+"
    r"(?:"
    r"kya|kab|kahan|kahaan|kaise|kaun|"
    r"kitna|kitne|kitni|kis|kidhar|"
    r"क्या|कब|कहाँ|कहां|कैसे|कौन|"
    r"कितना|कितने|कितनी|किस|किधर"
    r")(?=\s|\Z)",
    re.IGNORECASE,
)

# ---- Information requests phrased as imperatives/modals, not WH-words ----

# Requests that expect information even though they open with a modal
# auxiliary or imperative verb: "Could you tell me your destination?",
# "बताइए आप कब जाना चाहते हैं?"
INFORMATION_REQUEST_PATTERNS = (
    re.compile(
        r"^(?:please\s+)?(?:tell|share|provide|give)\s+(?:me\s+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:can|could|would)\s+you\s+"
        r"(?:please\s+)?"
        r"(?:tell|share|provide|give)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:"
        r"बताइए|बताएं|बताये|बताइये|"
        r"bataye|bataiye|batayen|batao"
        r")(?=\s|\Z)",
        re.IGNORECASE,
    ),
)

# "Do you have <slot> in mind?" is this bot's standard phrasing for eliciting
# a slot value (see prompts.py's own example: "Perfect. Do you have travel
# dates in mind?") — grammatically a yes/no shell, but the only useful reply
# is the value itself, not a bare "yes". Left unhandled, "do you have" alone
# satisfies CONFIRMATION_QUESTION_PATTERN below and the posture becomes
# AWAITING_CONFIRMATION, where (unlike AWAITING_ANSWER) a bare acknowledgement
# is treated as a complete, meaningful answer instead of being ignored as
# filler — so a filler "acha"/"ok" caught mid-thought gets promoted straight
# to an interruption and handed to the LLM as if it were the actual answer.
# Must be checked before the confirmation patterns for the same reason
# INFORMATION_REQUEST_PATTERNS is.
INFORMATION_IN_MIND_PATTERN = re.compile(
    r"\b(?:have|has|had)\b.{0,80}?\bin mind\b",
    re.IGNORECASE,
)

# ---- Ruling out confirmation: a WH-word anywhere in the clause ----

# Same WH-word list as INFORMATION_START_PATTERN/HINDI_INFORMATION_PATTERN,
# but not anchored to the start of the clause. English's mandatory
# aux-subject inversion means a WH-question routinely contains "...are
# you...", "...will you..." after the question word ("which city ARE YOU
# departing from?", "how many people ARE YOU traveling with?") — exactly
# what CONFIRMATION_QUESTION_PATTERN below is looking for. A real yes/no
# confirmation never contains a WH-word at all, so finding one anywhere in
# an actual question rules confirmation out first.
#
# क्या is deliberately excluded here: unlike the other WH-words it's
# ambiguous — it means "what" in "आप क्या चाहते हैं" but also opens a
# yes/no question in "क्या आप जाना चाहेंगे". Only HINDI_CONFIRMATION_PATTERN
# treats it as a signal, and only when it leads the clause.
#
# The Devanagari alternatives are searched unanchored (unlike the ^-rooted
# patterns above, where a real space always precedes the alternation), so
# a lone trailing (?=\s|\Z) isn't enough — without a matching leading
# boundary, a short word can match as the mid-word tail of a longer one
# (e.g. "कब" is not at risk, but this shape of bug is exactly what bit
# CHOICE_CONJUNCTION_PATTERN below: "या" is a real suffix of "दुनिया").
# (?<=\s) is used instead of \b because \b is unreliable right after a
# Devanagari vowel sign — see the note above INFORMATION_START_PATTERN.
QUESTION_WORD_ANYWHERE_PATTERN = re.compile(
    r"\b(?:what|why|when|where|who|how|which|whose)\b|"
    r"(?:^|(?<=\s))(?:क्यों|कैसे|कहाँ|कहां|कब|कौन|कौनसा|कौनसी|किस)(?=\s|\Z)",
    re.IGNORECASE,
)

# ---- Choice questions ("A or B?") ----

# Choice questions ("Standard or Value?", "Delhi या Mumbai?") require a
# value, not a yes/no acknowledgement. "ya" (romanized) is only matched as
# a whole word since it collides with the Hinglish spelling of "yeah" —
# low risk here since this classifies the bot's own generated text, not
# free-form user speech.
CHOICE_CONJUNCTION_PATTERN = re.compile(
    r"\bor\b|\bya\b|(?:^|(?<=\s))या(?=\s|\Z)",
    re.IGNORECASE,
)

# ---- Confirmation / yes-no questions ----

CONFIRMATION_REQUEST_PATTERN = re.compile(
    r"^(?:(?:please\s+)?confirm|(?:can|could|would|will)\s+you\s+(?:please\s+)?confirm)\b",
    re.IGNORECASE,
)

# An aux/modal alone at the start of a clause is not enough — "Will Smith
# is an actor" and "Have a nice day" both open that way without being
# yes/no questions. Requiring the aux to be directly followed by a subject
# pronoun is what tells a real confirmation ("Do you...", "Will you...")
# apart from those, and searching unanchored (not just at clause start)
# still catches confirmations after a context-setting prefix ("Since Paris
# is international, do you have a valid passport?").
CONFIRMATION_QUESTION_PATTERN = re.compile(
    r"\b(?:can|could|would|will|may|might|do|does|did|is|are|was|were|"
    r"have|has|had|should|shall)\s+(?:i|you|we|they|it|this|that|there)\b",
    re.IGNORECASE,
)

# "क्या" at the start usually forms a yes/no question ("क्या आप ...
# चाहेंगे?"), but "आप क्या चाहते हैं?" ("what do you want?") is an
# information question and is caught by HINDI_INFORMATION_PATTERN first.
HINDI_CONFIRMATION_PATTERN = re.compile(
    r"^(?:(?:तो|अच्छा|okay|ok)\s+)*"
    r"(?:"
    r"क्या\s+(?:आप|आपके|आपकी|तुम|तुम्हारे|तुम्हारी|यह|ये|वह|वो|हम|आपको|तुम्हें)"
    r"|"
    r"kya\s+(?:aap|aapke|aapki|ap|tum|tumhare|tumhari|ye|yeh|vo|woh|hum|aapko)"
    r")(?=\s|\Z)",
    re.IGNORECASE,
)

# Trailing confirmation tags: "You want to continue, right?", "Ye theek hai na?"
#
# The English alternatives need a leading \b: without one this substring-
# matches the tail of an unrelated word ending in the same letters —
# "look"/"book"/"took" end in "ok", "alright"/"outright" end in "right",
# "incorrect" ends in "correct" — turning an ordinary statement into a
# false AWAITING_CONFIRMATION posture. \b is safe here (unlike the
# trailing-boundary case documented above INFORMATION_START_PATTERN)
# because these are plain ASCII words, not Devanagari matras.
CONFIRMATION_ENDING_PATTERN = re.compile(
    r"(?:"
    r"\b(?:right|correct|okay|ok)\b|"
    r"(?:^|(?<=\s))(?:है ना|हैं ना|ना|hai na|hain na|theek hai na)"
    r")\s*\??$",
    re.IGNORECASE,
)


# ---- Clarification requests ----

# Clarification requests are questions too, but a bare filler does not answer them.
CLARIFICATION_PATTERN = re.compile(
    r"(?:\b(?:clarify|repeat|say that again|what did you (?:say|mean))\b|"
    r"(?:^|(?<=\s))(?:दोबारा|फिर से|स्पष्ट)(?=\s|\Z)|"
    r"\b(?:dobara|phir se)\b)",
    re.IGNORECASE,
)


def word_count(text: str) -> int:
    return len(text.split())


def _stable_reply(
    count: int,
    speech_duration: float,
    threshold: float,
    *,
    allow_single_word: bool,
) -> bool:
    """_stable, plus: a single interim word is trusted after the longer
    SINGLE_WORD_REPLY_THRESHOLD when the posture expects a reply."""
    if _stable(count, speech_duration, threshold):
        return True
    return (
        allow_single_word
        and count == 1
        and speech_duration >= max(threshold, SINGLE_WORD_REPLY_THRESHOLD)
    )


def _stable(count: int, speech_duration: float, threshold: float) -> bool:
    """True once interim speech has both enough words and enough elapsed
    stable duration to be trusted — the repeated shape behind every
    duration-gated INTERRUPT promotion in classify_interruption."""
    return count >= 2 and speech_duration >= threshold


def _all_tokens_are_backchannels(text: str) -> bool:
    if text in BACKCHANNEL_TOKENS or text in BACKCHANNEL_PHRASES:
        return True
    tokens = text.split()
    return bool(tokens) and all(token in BACKCHANNEL_TOKENS for token in tokens)


def filler_kind(text: str) -> str | None:
    """Classify text made *entirely* of fillers: "affirmation" (contains a
    yes-type word), "reaction" ("oh nice", "acha", "got it") or
    "backchannel" ("hmm", "uh"). None when it carries any real content.
    """
    remaining = f" {text} "
    atoms: list[str] = []
    for phrase in SORTED_ACKNOWLEDGEMENT_PHRASES:
        needle = f" {phrase} "
        while needle in remaining:
            atoms.append(phrase)
            remaining = remaining.replace(needle, " ", 1)
    atoms.extend(remaining.split())
    if not atoms or not all(a in ACKNOWLEDGEMENTS or a in ACKNOWLEDGEMENT_PHRASES for a in atoms):
        return None
    if any(a in AFFIRMATION_TOKENS or a in AFFIRMATION_PHRASES for a in atoms):
        return "affirmation"
    if any(a in REACTION_TOKENS or a in REACTION_PHRASES for a in atoms):
        return "reaction"
    return "backchannel"


EXPECTED_USER_SPEECH: Final[dict[AgentPosture, str]] = {
    AgentPosture.EXPLAINING: "nothing",
    AgentPosture.AWAITING_ANSWER: "value",
    AgentPosture.AWAITING_CONFIRMATION: "yes_no",
    AgentPosture.AWAITING_CLARIFICATION: "clarification",
}


def _is_filler_answer(text: str, posture: AgentPosture) -> bool:
    """A filler that genuinely answers the bot's current speech type. Only
    an affirmation answers a yes/no confirmation; nothing else does."""
    return posture is AgentPosture.AWAITING_CONFIRMATION and filler_kind(text) == "affirmation"


def _filler_verdict_for_posture(
    kind: str, posture: AgentPosture, *, explanatory_prefix_uncertain: bool
) -> tuple[bool, str]:
    """Whether a pure-filler `kind` (never None — the caller must already
    have confirmed filler_kind(text) is not None) counts as answering the
    bot's current posture, and the reason string to log either way.

    Single source of truth shared by classify_interruption's live-overlap
    path and is_non_answer_for_current_posture's post-hoc/finalized-turn
    path, so the two can never again independently disagree about the same
    judgement call — which is exactly what used to happen: a bare "ok"/
    "theek hai" answering a confirmation that had an explanatory prefix
    (see GuardState.posture_has_explanatory_prefix) was correctly ignored
    live, but the finalized-turn path had no notion of that same prefix and
    forwarded it to the LLM as a definite "yes" regardless.

    Reason strings returned here are load-bearing: turn_logger.py's
    _GUARD_REASON_TEXT table renders each one into human-readable log
    prose, so these must stay exactly the strings already in use there.
    """
    if posture is AgentPosture.AWAITING_CLARIFICATION:
        return False, "non_answer_to_clarification"
    if posture is AgentPosture.AWAITING_ANSWER:
        return False, "non_answer_to_information_question"
    if posture is AgentPosture.EXPLAINING:
        return False, "explanation_acknowledgement"
    # AWAITING_CONFIRMATION
    if kind != "affirmation":
        return False, "non_answer_to_confirmation"
    if explanatory_prefix_uncertain:
        return False, "explanation_prefix_acknowledgement"
    return True, "affirmation_answers_confirmation"


def classify_assistant_sentence(sentence: str) -> PostureResult:
    text = normalize_text(sentence)

    if not text:
        return PostureResult(AgentPosture.EXPLAINING, "empty_sentence")

    explicit_question = sentence.strip().endswith("?")

    if CLARIFICATION_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_CLARIFICATION, "clarification_request")

    # Information request must be checked before confirmation: "Could you
    # tell me your budget?" starts with "could" but is not yes/no.
    for pattern in INFORMATION_REQUEST_PATTERNS:
        if pattern.search(text):
            return PostureResult(AgentPosture.AWAITING_ANSWER, "information_request")

    # Choice questions require a value, not a generic yes/no acknowledgement.
    if explicit_question and CHOICE_CONJUNCTION_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_ANSWER, "choice_question")

    if INFORMATION_START_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_ANSWER, "information_question")

    if HINDI_INFORMATION_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_ANSWER, "hindi_information_question")

    # Rule out confirmation before checking for it: a WH-word anywhere in
    # an actual question means it needs a value, not a yes/no.
    if explicit_question and QUESTION_WORD_ANYWHERE_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_ANSWER, "wh_word_anywhere")

    if INFORMATION_IN_MIND_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_ANSWER, "in_mind_information_question")

    if HINDI_CONFIRMATION_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_CONFIRMATION, "hindi_confirmation_question")

    if CONFIRMATION_REQUEST_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_CONFIRMATION, "confirmation_request")

    if CONFIRMATION_QUESTION_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_CONFIRMATION, "english_confirmation_question")

    if CONFIRMATION_ENDING_PATTERN.search(text):
        return PostureResult(AgentPosture.AWAITING_CONFIRMATION, "confirmation_ending")

    # We know it's a question but couldn't confidently classify it as
    # yes/no; safer to expect an informational answer.
    if explicit_question:
        return PostureResult(AgentPosture.AWAITING_ANSWER, "question_fallback")

    return PostureResult(AgentPosture.EXPLAINING, "statement")


def infer_agent_posture(text: str) -> AgentPosture:
    """Classify the final conversational clause, not the response opening."""
    stripped = text.strip()
    if not stripped:
        return AgentPosture.EXPLAINING

    clauses = [part.strip() for part in re.split(r"[.!?।]+", stripped) if part.strip()]
    final_clause = clauses[-1] if clauses else stripped
    # re.split() consumes the delimiter, so a trailing "?" on the full
    # utterance is lost from final_clause — reattach it so
    # classify_assistant_sentence's question-mark fallback still sees it.
    if stripped.endswith("?") and not final_clause.endswith("?"):
        final_clause = f"{final_clause}?"

    return classify_assistant_sentence(final_clause).posture


@dataclass(slots=True)
class GuardState:
    posture: AgentPosture = AgentPosture.EXPLAINING
    agent_speaking: bool = False
    # True for the whole span of a turn's SpeechHandle — "thinking" (LLM +
    # tool calls, before any audio) through "speaking" — as opposed to
    # agent_speaking, which is only true once audio playback starts. A new
    # user turn that arrives while the previous one is still "thinking" has
    # nowhere to go without this: interruption.enabled=False means nothing
    # else in the pipeline cancels that in-flight generation, so it runs to
    # completion in parallel with the new turn and both get spoken back to
    # back (see on_user_input_transcribed).
    agent_active: bool = False
    user_speech_started_at: float | None = None
    interrupt_fired: bool = False
    # True from the moment the user starts speaking while agent_active,
    # through the next _reset_overlap() (agent goes idle/listening, or the
    # user goes "away"). Marks whether the transcript currently being
    # assembled ever actually talked over the agent's own current turn, as
    # opposed to arriving cleanly after the agent had already finished and
    # moved on. is_redundant_turn() uses this to decide whether comparing
    # against active_turn even makes sense for this turn — see its
    # docstring.
    turn_overlapped_agent: bool = False
    # Exact text of the most recent *final* transcript that classify_overlap
    # judged REDUNDANT against the active turn, kept only until the next
    # final classification (redundant or not) overwrites/clears it.
    #
    # Needed because the "away" user-state transition that follows a final
    # transcript almost immediately (well before LiveKit's own dropped-turn
    # warning reaches recover_dropped_turn) calls _reset_overlap(), which
    # flips turn_overlapped_agent back to False. is_redundant_turn() then
    # sees "no overlap" and skips the real comparison for the very text it
    # already classified as an exact repeat moments earlier — this snapshot
    # lets it recognize that specific case instead of silently reversing
    # its own verdict. See is_redundant_turn() and recover_dropped_turn().
    last_final_redundant_text: str | None = None
    last_transcript_event: tuple[str, bool] | None = None
    last_decision: InterruptDecision | None = None
    transcript_evolution: list[str] = field(default_factory=list)
    # Normalized text of the sentence/clause the agent is currently
    # speaking, refreshed by set_assistant_text. Compared against incoming
    # transcripts in classify_interruption to catch TTS speaker-bleed
    # before it's treated as a real barge-in.
    assistant_text_normalized: str = ""
    # True when the bot's current posture is not a plain statement (i.e. it's
    # AWAITING_ANSWER/AWAITING_CONFIRMATION/AWAITING_CLARIFICATION) and an
    # earlier, non-final clause of its just-generated reply classified as
    # EXPLAINING — meaning we can't be sure TTS playback has actually reached
    # the question/confirmation clause yet (LLM generation finishes well
    # before playback of a multi-clause reply does; see set_assistant_text).
    # Generalized from a confirmation-only flag because the same "might
    # still be mid-explanation" uncertainty applies just as much to
    # AWAITING_ANSWER/AWAITING_CLARIFICATION. Consumed by
    # _filler_verdict_for_posture (only for AWAITING_CONFIRMATION's filler
    # verdict) and by classify_interruption's interim-threshold selection
    # (all three non-EXPLAINING postures).
    posture_has_explanatory_prefix: bool = False


def classify_interruption(
    *,
    transcript: str,
    is_final: bool,
    posture: AgentPosture,
    speech_duration: float,
    assistant_text_normalized: str = "",
    posture_has_explanatory_prefix: bool = False,
) -> tuple[InterruptDecision, str]:
    """Pure decision function, separated from LiveKit event side effects."""
    text = normalize_text(transcript)
    count = word_count(text)
    if not text:
        return InterruptDecision.WAIT, "empty_transcript"
    if _looks_like_tts_echo(text, assistant_text_normalized):
        return InterruptDecision.IGNORE, "tts_echo"
    if text in IMMEDIATE_COMMANDS or _strip_command_prefix_fillers(text) in IMMEDIATE_COMMANDS:
        return InterruptDecision.INTERRUPT, "explicit_command"

    # Not when the bot's own sentence contains the word (its greeting says
    # "Hello, ..."): that is far likelier speaker bleed than the caller, so it
    # falls through to the regular, slower posture rules.
    if is_attention_getter(text) and f" {text} " not in f" {assistant_text_normalized} ":
        if is_final or speech_duration >= ATTENTION_GETTER_MIN_DURATION:
            return InterruptDecision.INTERRUPT, "attention_getter"
        return InterruptDecision.WAIT, "attention_getter_pending"

    filler = filler_kind(text)
    ambiguous_acknowledgement = filler is not None

    # NOTE ON is_final AND posture_has_explanatory_prefix: the unconditional-
    # trust is_final branches below (a genuinely finalized, meaningful
    # multi-word transcript INTERRUPTs immediately) are deliberately NOT
    # slowed down by posture_has_explanatory_prefix, unlike the interim/
    # duration-based promotion just below each of them. is_final represents
    # STT's own endpointing commitment — a materially stronger signal than
    # interim stability — so real content finalized mid-explanation is still
    # trusted immediately. Only bare fillers (via _filler_verdict_for_posture)
    # and the interim-duration promotion get the extra patience. This is a
    # deliberate, accepted scope boundary, not an oversight.

    if posture is AgentPosture.AWAITING_CLARIFICATION:
        if ambiguous_acknowledgement:
            _, reason = _filler_verdict_for_posture(
                filler, posture, explanatory_prefix_uncertain=False
            )
            return InterruptDecision.IGNORE, reason
        if is_final:
            return InterruptDecision.INTERRUPT, "clarification"
        threshold = (
            EXPLANATION_INTERRUPT_THRESHOLD
            if posture_has_explanatory_prefix
            else QUESTION_INTERRUPT_THRESHOLD
        )
        if _stable(count, speech_duration, threshold):
            return InterruptDecision.INTERRUPT, "clarification"
        return InterruptDecision.WAIT, "unstable_clarification"

    # A generic "yeah" can answer "Would you like to continue?", but cannot
    # answer "How many members?". Only confirmation questions accept it.
    if posture is AgentPosture.AWAITING_CONFIRMATION:
        if ambiguous_acknowledgement:
            is_answer, reason = _filler_verdict_for_posture(
                filler, posture, explanatory_prefix_uncertain=posture_has_explanatory_prefix
            )
            if not is_answer:
                return InterruptDecision.IGNORE, reason
            answer_reason = reason  # "affirmation_answers_confirmation"
        else:
            answer_reason = "reply_to_confirmation"
        if is_final:
            # is_final is not, on its own, proof this is a real reply — see
            # _is_meaningful_final_reply. A bare ack/negation still answers
            # a yes/no question outright; anything with no recognizable
            # signal waits for more speech instead of forcing a cutoff.
            if _is_meaningful_final_reply(
                text, ambiguous_acknowledgement=ambiguous_acknowledgement
            ):
                return InterruptDecision.INTERRUPT, answer_reason
            return InterruptDecision.WAIT, "unvalidated_final_fragment"
        threshold = (
            EXPLANATION_INTERRUPT_THRESHOLD
            if posture_has_explanatory_prefix
            else CONFIRMATION_INTERRUPT_THRESHOLD
        )
        if _stable_reply(
            count,
            speech_duration,
            threshold,
            allow_single_word=not posture_has_explanatory_prefix,
        ):
            return InterruptDecision.INTERRUPT, answer_reason
        return InterruptDecision.WAIT, "unstable_reply_to_confirmation"

    if posture is AgentPosture.AWAITING_ANSWER:
        # A WH-question needs real content ("Mumbai", "four people") — a bare
        # acknowledgement/backchannel never answers "which city", no matter
        # how finalized it is. (Unlike AWAITING_CONFIRMATION just above,
        # where a bare "ok"/"hmm" IS a complete yes/no answer.)
        if ambiguous_acknowledgement:
            _, reason = _filler_verdict_for_posture(
                filler, posture, explanatory_prefix_uncertain=False
            )
            return InterruptDecision.IGNORE, reason
        if is_final:
            # Same is_final skepticism as AWAITING_CONFIRMATION above: a
            # stray non-content fragment (STT noise, leftover echo) must not
            # be trusted just because it arrived marked final.
            if _is_meaningful_final_reply(text, ambiguous_acknowledgement=False):
                return InterruptDecision.INTERRUPT, "reply_to_question"
            return InterruptDecision.WAIT, "unvalidated_final_fragment"
        threshold = (
            EXPLANATION_INTERRUPT_THRESHOLD
            if posture_has_explanatory_prefix
            else QUESTION_INTERRUPT_THRESHOLD
        )
        if _stable_reply(
            count,
            speech_duration,
            threshold,
            allow_single_word=not posture_has_explanatory_prefix,
        ):
            return InterruptDecision.INTERRUPT, "reply_to_question"
        return InterruptDecision.WAIT, "unstable_reply_to_question"

    if _all_tokens_are_backchannels(text):
        return InterruptDecision.IGNORE, "vocal_backchannel"

    if filler is not None:
        _, reason = _filler_verdict_for_posture(
            filler, posture, explanatory_prefix_uncertain=False
        )
        return InterruptDecision.IGNORE, reason

    # A final chunk is only as trustworthy as its word count says it is —
    # Deepgram can and does emit is_final=True mid-utterance on nothing more
    # than a micro-pause (see the note above EXPLANATION_INTERRUPT_THRESHOLD).
    # A single stray word from a chunk cut short by a breathing pause gets
    # the same benefit of the doubt as an interim one; nothing here discards
    # it — LiveKit's own end-of-turn detection still delivers the full
    # utterance once the user's turn genuinely ends. Multi-word finals are
    # exactly as immediate as before: this only changes the single-word case.
    if is_final:
        if count >= 2:
            return InterruptDecision.INTERRUPT, "final_meaningful_speech"
        return InterruptDecision.WAIT, "single_word_final_fragment"
    if _stable(count, speech_duration, EXPLANATION_INTERRUPT_THRESHOLD):
        return InterruptDecision.INTERRUPT, "stable_multiword_speech"
    return InterruptDecision.WAIT, "collecting_transcript"


class InterruptionGuard:
    def __init__(
        self,
        session: AgentSession,
        session_label: str = "session",
        on_event: Callable[[str, dict], None] | None = None,
        filler_registry: object | None = None,
    ) -> None:
        self.session = session
        self.session_label = session_label
        self.state = GuardState()
        # Optional tool_filler.FillerRegistry — lets the guard tell
        # whether whatever it's about to interrupt is a filler line
        # ("Just a moment.") rather than the real assistant reply. Without
        # this the guard is blind to fillers entirely: every barge-in on a
        # filler gets classified/logged identically to one on a real reply,
        # so nothing in the decision log ever calls out that it was a
        # filler that got cut off.
        self._filler_registry = filler_registry
        # Tracks the last committed user turn and classifies whether new
        # overlapping speech restates it, extends it, corrects it, or is a
        # genuinely new turn — gates whether a candidate interruption should
        # actually cut off playback.
        self.semantic_analyzer = SemanticCheckAnalyzer()
        # Optional observer for latency/metrics logging (e.g. turn_logger.py).
        # Never influences a decision — purely notified after the fact.
        self.on_event = on_event
        # Additional observers (e.g. speech_tuner.py, which needs to know
        # whether a barge-in was a *genuine* guard-confirmed interruption
        # rather than any raw overlap) beyond the single on_event slot above.
        self._observers: list[Callable[[str, dict], None]] = []
        # Snapshot of the most recent is_redundant_turn() verdict (delta,
        # reason, timing) for the per-turn latency log (turn_logger.py) to
        # attach to its record — set as a side effect so is_redundant_turn's
        # own bool return contract (relied on by on_user_turn_completed and
        # the unit tests) doesn't have to change shape to carry this
        # diagnostic data.
        self.last_semantic_check: dict | None = None
        # De-dupes consecutive recover_dropped_turn() calls for the same
        # text — see that method's docstring for why the underlying
        # framework warning can fire more than once for one dropped turn.
        self._last_recovered_text: str = ""
        # True once the agent's first assistant turn has actually been
        # committed to chat context (on_conversation_item_added fires for
        # it) — see recover_dropped_turn's guard on this for why "no
        # assistant turn confirmed yet" must block recovery rather than
        # firing a second generate_reply().
        self._assistant_has_spoken: bool = False
        # De-dupes consecutive set_assistant_text log lines for the same
        # posture (llm_node calls it once per streamed clause) — see
        # set_assistant_text.
        self._last_logged_posture: AgentPosture | None = None

    def add_observer(self, callback: Callable[[str, dict], None]) -> None:
        self._observers.append(callback)

    def _interrupting_filler(self) -> bool:
        """True when whatever is currently playing is a filler line, not
        the real assistant reply — see FillerRegistry.is_filler."""
        if self._filler_registry is None:
            return False
        return self._filler_registry.is_filler(getattr(self.session, "current_speech", None))

    def _notify(self, kind: str, data: dict) -> None:
        if self.on_event is not None:
            self.on_event(kind, data)
        for observer in self._observers:
            observer(kind, data)

    def _resolve_audio_output(self):
        output = getattr(self.session, "output", None)
        return getattr(output, "audio", None) if output is not None else None

    def _pause_playback_for_semantic_check(self) -> bool:
        """Pause active playback the instant a candidate interruption is
        detected, before the slower semantic check confirms whether it's a
        real interruption or just redundant speech.

        Without this, the bot keeps talking for the full duration of the
        embedding-model call (see semantic_check_time in the decision log) —
        for a genuine interruption that whole window reads as unresponsive
        lag. Pausing costs nothing if the check comes back REDUNDANT:
        _resume_playback_after_false_interruption picks playback back up
        from exactly where it paused (the audio buffer is held, not
        cleared) instead of restarting anything.
        """
        if not self.state.agent_speaking:
            return False
        audio_output = self._resolve_audio_output()
        if audio_output is None or not audio_output.can_pause:
            return False
        try:
            audio_output.pause()
        except Exception:
            applog.exception(
                f"[INTERRUPT GUARD][{self.session_label}] failed to pause playback for semantic check"
            )
            return False
        return True

    def _resume_playback(self) -> bool:
        """Unconditionally clear a pause on the audio output, if any.

        Safe (and a no-op) to call whether or not this specific path was
        the one that paused — resume() just sets an already-set event when
        nothing is paused. Called from _interrupt() itself, not only from
        the false-interruption path below: once a confirmed real
        interruption tears down the current SpeechHandle, nothing else in
        the framework knows to undo a pause this guard applied (that
        bookkeeping lives entirely in the built-in false-interruption
        feature, which is disabled — see TurnHandlingOptions in
        agent_stt_llm_tts_v1.py), so skipping this would leave every
        subsequent reply's audio silently stuck paused forever.
        """
        audio_output = self._resolve_audio_output()
        if audio_output is None:
            return False
        try:
            audio_output.resume()
        except Exception:
            applog.exception(
                f"[INTERRUPT GUARD][{self.session_label}] failed to resume playback"
            )
            return False
        return True

    def _resume_playback_after_false_interruption(self) -> None:
        if not self._resume_playback():
            return
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] resumed playback: candidate interruption "
            "was redundant speech, not a real interruption"
        )
        self._notify("false_interruption", {"resumed": True, "reason": "redundant_speech"})

    def _report_filler(self, text: str, kind: str, verdict: str, *, notify: bool = True) -> dict:
        """Record how a finalized filler was routed. Fillers never reach the
        semantic check: non-answers are suppressed, answers go to the LLM."""
        info = {
            "text": text,
            "bot_speech": self.state.posture.value,
            "expected_user_speech": EXPECTED_USER_SPEECH[self.state.posture],
            "user_filler": kind,
            "verdict": verdict,
        }
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] final_filler bot_speech={info['bot_speech']} "
            f"expects={info['expected_user_speech']} user_filler={kind} verdict={verdict} "
            f"semantic_check=skipped transcript={text!r}"
        )
        if notify:
            self._notify("filler_check", info)
        return info

    def is_non_answer_for_current_posture(
        self, text: str, *, notify: bool = True, uncertain_playback_position: bool = False
    ) -> bool:
        """True when `text` is pure filler (ack/backchannel) while the agent's
        last posture was AWAITING_CLARIFICATION, AWAITING_ANSWER, or
        EXPLAINING — postures where a bare "ok"/"hmm"/"theek hai" is not
        real content: the first two require an actual answer, and EXPLAINING
        means the agent's last utterance was a plain statement with no
        question pending at all, so a trailing ack has nothing to answer
        either. Under AWAITING_CONFIRMATION only reactions/backchannels
        ("oh nice", "acha", "hmm") count as non-answers: an affirmation
        ("yes", "haan") genuinely answers a yes/no question there — unless
        `uncertain_playback_position` says we can't trust that yet (see
        below).

        EXPLAINING's inclusion here mirrors classify_interruption's own
        "explanation_acknowledgement" ignore branch for live overlap — this
        is the same rule applied to a transcript that finalizes cleanly
        after the agent already stopped talking (e.g. a trailing "ok"/
        "haan"/"theek hai" arriving right as the agent finishes an
        explanation or a closing remark). Without this, that filler used to
        sail through as a brand-new LLM turn and get a fresh, often
        confused reply generated for it.

        Unlike classify_interruption, this is meant to be called on every
        finalized user turn regardless of whether the agent was speaking:
        `posture` outlives the agent's turn (it's only overwritten by the
        next assistant utterance), so a filler reply arriving after the
        agent has gone quiet and is still waiting on an answer/clarification
        (or has simply finished a statement) must be caught too, not just
        barge-in fragments.

        `uncertain_playback_position=True` additionally treats an otherwise-
        valid confirmation affirmation as a non-answer when the bot's
        posture carries an explanatory prefix (GuardState.
        posture_has_explanatory_prefix) — i.e. the confirmation question was
        preceded by an explanation clause, so we can't be sure TTS playback
        had actually reached the question yet when this text was spoken.
        Defaults to False because a normal call here (from
        Agent.on_user_turn_completed) only ever fires once the bot's entire
        multi-clause reply has already finished playing (or was already
        force-interrupted and independently validated) — see
        recover_dropped_turn, the only caller that sets it True, since every
        call into that method is, by construction, for text that overlapped
        the bot's still-playing speech and so genuinely might have landed
        before the question was ever spoken.
        """
        normalized = normalize_text(text)
        if not normalized:
            return False
        kind = filler_kind(normalized)
        if kind is None:
            return False
        is_answer, _reason = _filler_verdict_for_posture(
            kind,
            self.state.posture,
            explanatory_prefix_uncertain=(
                uncertain_playback_position and self.state.posture_has_explanatory_prefix
            ),
        )
        non_answer = not is_answer
        self._report_filler(
            text, kind, "non_answer_suppressed" if non_answer else "answer_to_llm", notify=notify
        )
        return non_answer

    def is_redundant_turn(self, text: str, *, notify: bool = True) -> bool:
        """True when this finalized transcript adds nothing over the active
        committed user turn — a restatement, a subset of already-given
        information, an exact repeat (e.g. the caller says "Hyderabad"
        again while still mid-barge-in on the same agent turn that already
        asked for it).

        `notify=False` is for recover_dropped_turn's own internal admission
        check only: that call classifies a *different*, not-yet-opened
        utterance against whatever active_turn already holds (often the
        very turn whose reply is still being generated/spoken right now),
        so its verdict describes that other utterance, not the turn
        currently open in turn_logger. Notifying observers unconditionally
        used to attach that unrelated verdict (and its "active" text, which
        can even be the *current* turn's own just-committed text) to
        whatever box happened to still be open, showing a turn compared
        against itself instead of "n/a" or the real prior turn.

        Only ever compares when turn_overlapped_agent is set — i.e. this
        transcript actually talked over the agent's current turn. A turn
        that arrives cleanly after the agent already finished speaking and
        moved on is never checked here, even if it's textually identical to
        an earlier answer: active_turn has no notion of *which question* it
        answered, so once the agent has asked something new, the same words
        can legitimately be a fresh (if unhelpful) reply to that new
        question rather than a repeat of the old one. Comparing it against
        the old answer regardless of what was asked in between silently
        dropped genuine replies — e.g. the caller says "मुझे क्या पता?" to
        a new question, it happens to match their answer to the *previous*
        question verbatim, and gets read as an exact repeat and vetoed with
        no reply, leaving the caller hanging.

        This does give up catching the old bug this function was built for
        (an idle repeat of an already-answered slot with no new question
        asked in between sailing through as "new") in exchange for not
        swallowing turns like the one above — a duplicate LLM reply is a
        smaller failure than silently dropping the user's turn.

        Meant to be checked from Agent.on_user_turn_completed to veto
        generation before it happens.
        """
        normalized_text = normalize_text(text)
        attention = is_attention_getter(normalized_text)
        if attention or _is_filler_answer(normalized_text, self.state.posture):
            # A yes-type filler answering a confirmation is decided by the
            # guard's filler rules, never by comparing it to the active turn.
            # (is_non_answer_for_current_posture already reported it.) The
            # same goes for an attention-getter: it already interrupted live
            # (classify_interruption "attention_getter"), and "hello" being
            # a word of the active turn ("Hello, I wanna go to...") must not
            # veto the reply the caller is waiting for as a repeat.
            self.last_semantic_check = {
                "ts": time.monotonic(),
                "finalized": text,
                "active": None,
                "delta": "skipped",
                "reason": "attention_getter" if attention else "filler_answer_to_confirmation",
                "posture": self.state.posture.value,
                "decision_time": None,
            }
            if notify:
                self._notify("redundancy", self.last_semantic_check)
            return False
        if not self.state.turn_overlapped_agent:
            stripped = text.strip()
            if stripped and stripped == self.state.last_final_redundant_text:
                # The overlap this text was classified against already
                # ended (turn_overlapped_agent reset by the "away"
                # transition) by the time this call arrived, but it was
                # judged an exact repeat moments ago — reuse that verdict
                # instead of reporting "no overlap" and letting it through.
                self.state.last_final_redundant_text = None
                self.last_semantic_check = {
                    "ts": time.monotonic(),
                    "finalized": text,
                    "active": (
                        self.semantic_analyzer.active_turn.text
                        if self.semantic_analyzer.active_turn
                        else None
                    ),
                    "delta": "redundant",
                    "reason": "stale_overlap_reset:exact_repeat",
                    "posture": self.state.posture.value,
                    # No real classification ran for this branch (it just
                    # reuses an earlier verdict) — nothing to time.
                    "decision_time": None,
                    "similarity": 1.0,
                }
                if notify:
                    self._notify("redundancy", self.last_semantic_check)
                return True
            self.last_semantic_check = {
                "ts": time.monotonic(),
                "finalized": text,
                "active": None,
                "delta": "skipped",
                "reason": "no_agent_overlap",
                "posture": self.state.posture.value,
                "decision_time": None,
            }
            if notify:
                self._notify("redundancy", self.last_semantic_check)
            return False

        _classify_start = time.perf_counter()
        analysis = self.semantic_analyzer.classify_final_turn(
            text,
            # Clarification can invite an exact repeat of the user's prior
            # wording, so keep that case out of the redundancy check.
            # Confirmation affirmations are handled by filler rules; other
            # replies should be compared to catch repeated attention-getters.
            expects_short_answer=(
                self.state.posture is AgentPosture.AWAITING_CLARIFICATION
            ),
        )
        decision_time = time.perf_counter() - _classify_start
        active = self.semantic_analyzer.active_turn
        self.last_semantic_check = {
            "ts": time.monotonic(),
            "finalized": text,
            "active": active.text if active else None,
            "delta": analysis.delta.value,
            "reason": analysis.reason,
            "posture": self.state.posture.value,
            "decision_time": decision_time,
            "similarity": analysis.similarity,
        }
        if notify:
            self._notify("redundancy", self.last_semantic_check)
        return analysis.delta is TurnDelta.REDUNDANT

    def recover_dropped_turn(self, text: str) -> None:
        """Compensate for a user turn LiveKit's own pipeline silently drops.

        interruption.enabled=False (this module is the sole interruption
        owner — see the module docstring) means every SpeechHandle is
        created with allow_interruptions=False. When a second finalized
        user turn arrives while the first is still generating,
        agent_activity.py's _user_turn_completed_task checks
        current_speech.allow_interruptions *before* ever calling
        Agent.on_user_turn_completed or appending the message to chat
        context — finds it False, logs "skipping reply to user input,
        current speech generation cannot be interrupted", and returns. The
        transcript is gone: not answered, not in history, no trace besides
        that log line. This is exactly what a missed micro-pause produces —
        one utterance finalizes as two back-to-back turns, and the second
        (often the part carrying the actual new information, e.g. the
        destination in "Yeah, I think it will be [pause] traveling from
        Bangalore") is the one that vanishes.

        This guard already independently sees every transcript via
        on_user_input_transcribed and, for exactly this kind of new
        content, already decides to MANUAL_INTERRUPT the current speech
        (see _interrupt). Whether the drop above actually happens is a
        race between that manual interrupt clearing current_speech and
        LiveKit's own end-of-turn handling reaching its check first — so
        the two outcomes are mutually exclusive: either the interrupt wins
        and the turn goes through the normal path, or it loses and this
        method is the only thing that still hands the transcript to the
        LLM. There is no public event for the drop itself, only this log
        line (wired up in attach_interruption_guard), so that is the
        signal used here instead of trying to win the race directly.

        Applies the same admission checks Agent.on_user_turn_completed
        would have applied, since the framework skipped calling it.

        Also refuses to recover anything until the agent's first assistant
        turn has actually been committed to chat context
        (_assistant_has_spoken). The opening greeting is itself spoken with
        allow_interruptions=False and can take several seconds to finish
        playing, and add_to_chat_ctx only commits the item once that
        SpeechHandle completes — not once synthesis starts. A user utterance
        landing (and getting dropped) anywhere in that window would
        otherwise trigger a generate_reply() whose chat_ctx still has zero
        assistant turns in it; since the system prompt instructs the LLM to
        open with that exact greeting, the "recovered" reply is the same
        introduction verbatim, queued right behind the original (both
        non-interruptible) and played back to back — the caller hears
        "Hi, I am Tacy..." twice in a row for what was really one greeting.
        """
        text = text.strip()
        if not text or text == self._last_recovered_text:
            return
        if not self._assistant_has_spoken:
            return
        # Classify directly against active_turn instead of going through
        # is_redundant_turn(), which only compares when
        # self.state.turn_overlapped_agent is set. That flag is reset by the
        # "away" user-state transition, which reliably fires *before* this
        # method runs (LiveKit's drop warning arrives after the transcript
        # has already finalized) — so by the time we get here it's almost
        # always already False again, and the only rescue was an exact
        # string match against last_final_redundant_text, which breaks on
        # any formatting difference between the STT text the live overlap
        # check saw and the text LiveKit hands back with this drop warning
        # (e.g. punctuation added by a later finalization pass). Every call
        # into recover_dropped_turn is, by construction, for a transcript
        # that overlapped active non-interruptible speech (that's the only
        # way LiveKit drops it) — the overlap already happened, so there is
        # nothing to gate on here; always run the real comparison.
        #
        # uncertain_playback_position=True for the same reason: unlike a
        # normal Agent.on_user_turn_completed call (which only ever fires
        # once the bot's whole multi-clause reply has already finished
        # playing, or was already independently validated by a real
        # interrupt), a recovered turn's text may have landed anywhere
        # during that still-playing speech — including before a trailing
        # confirmation/question clause was actually spoken. So a bare
        # affirmation ("ok"/"theek hai") here can't be trusted as answering
        # that clause when GuardState.posture_has_explanatory_prefix says an
        # earlier clause was still just explaining.
        non_answer = self.is_non_answer_for_current_posture(
            text, notify=False, uncertain_playback_position=True
        )
        analysis = None
        if (
            not non_answer
            and not is_attention_getter(normalize_text(text))
            and not _is_filler_answer(normalize_text(text), self.state.posture)
        ):
            analysis =self.semantic_analyzer.classify_final_turn(
                text,
                # Clarification can invite an exact repeat of the user's prior
                # wording, so keep that case out of the redundancy check.
                # Confirmation affirmations are already handled by the
                # guard's filler rules; other confirmation replies should
                # still be compared so repeated attention-getters like
                # "hello" do not trigger another full response.
                expects_short_answer=(
                    self.state.posture is AgentPosture.AWAITING_CLARIFICATION
                ),
            )
        redundant = analysis is not None and analysis.delta is TurnDelta.REDUNDANT
        active = self.semantic_analyzer.active_turn
        # Recovered turns never pass through Agent.on_user_turn_completed, so
        # this is the only way turn_logger learns they existed.
        self._notify(
            "recovery",
            {
                "text": text,
                "action": "suppressed" if (non_answer or redundant) else "regenerated",
                "suppress_reason": (
                    "non_answer_filler" if non_answer else "redundant_turn" if redundant else None
                ),
                "posture": self.state.posture.value,
                "expected_user_speech": EXPECTED_USER_SPEECH[self.state.posture],
                "user_filler": filler_kind(normalize_text(text)),
                "delta": analysis.delta.value if analysis else "skipped",
                "reason": analysis.reason if analysis else "filler_rule",
                "similarity": analysis.similarity if analysis else None,
                "compared_with": active.text if analysis and active else None,
            },
        )
        if non_answer or redundant:
            # Matches the live overlap path's pause -> check -> resume
            # architecture instead of silently dropping the text: whatever
            # the bot was already saying is what should keep playing, so
            # explicitly resume it (see _pause_playback_for_semantic_check /
            # _resume_playback_after_false_interruption) rather than just
            # returning and leaving it to chance. Safe to call even when
            # nothing is actually paused here (e.g. the live path already
            # resumed it, or never paused at all) — _resume_playback()
            # is a documented no-op in that case.
            self._resume_playback_after_false_interruption()
            return
        self._last_recovered_text = text
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] recovering dropped turn: text={text!r}"
        )
        # By the time LiveKit's own pipeline drops a turn this way, the
        # SpeechHandle it was checking against didn't exist yet when
        # _interrupt() made its (single, latched) attempt above — that
        # handle now exists, is mid-flight, and nothing will stop it. Left
        # alone it plays out in full and the reply generated below queues
        # right behind it: the caller hears the stale answer and then the
        # real one, back to back. Force-interrupt again here, against
        # whatever is actually current *now*, so the recovered reply
        # replaces it instead of trailing it.
        try:
            self.session.interrupt(force=True)
        except RuntimeError as exc:
            applog.warning(
                f"[INTERRUPT GUARD][{self.session_label}] recovery interrupt not applied: {exc}"
            )
        self.session.generate_reply(user_input=text)

    def set_assistant_text(self, text: str, *, source: str) -> None:
        if not text.strip():
            return
        posture = infer_agent_posture(text)
        self.state.posture = posture
        self.state.assistant_text_normalized = normalize_text(text)
        clauses = [part.strip() for part in re.split(r"[.!?।]+", text) if part.strip()]
        self.state.posture_has_explanatory_prefix = (
            posture is not AgentPosture.EXPLAINING
            and len(clauses) > 1
            and any(
                classify_assistant_sentence(clause).posture is AgentPosture.EXPLAINING
                for clause in clauses[:-1]
            )
        )
        if source != "llm_clause" or posture != self._last_logged_posture:
            self._last_logged_posture = posture
            applog.info(
                f"[INTERRUPT GUARD][{self.session_label}] bot_speech={posture.value} "
                f"expects={EXPECTED_USER_SPEECH[posture]} "
                f"assistant_text={text!r}"
            )

    def on_agent_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state in {"speaking", "thinking"}:
            # Only reset at the true start of a turn's active window (the
            # thinking->speaking transition inside one turn must NOT reset:
            # it would wipe out overlap/interrupt state a user's speech
            # already earned during that same turn's thinking phase).
            if not self.state.agent_active:
                self._reset_overlap()
            self.state.agent_speaking = new_state == "speaking"
            self.state.agent_active = True
            return
        if new_state in {"idle", "listening"}:
            self.state.agent_speaking = False
            self.state.agent_active = False
            self._reset_overlap()

    def on_user_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking" and self.state.agent_active:
            if self.state.user_speech_started_at is None:
                self.state.user_speech_started_at = time.monotonic()
                self.state.last_transcript_event = None
                self.state.transcript_evolution.clear()
                self.semantic_analyzer.begin_overlap()
                self.state.turn_overlapped_agent = True
            return
        # Final STT commonly arrives just after VAD transitions to listening.
        if new_state == "away":
            self._reset_overlap()

    def on_conversation_item_added(self, event: object) -> None:
        item = getattr(event, "item", None)
        if item is None:
            return
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", "") or ""
        if role == "assistant":
            self._assistant_has_spoken = True
            self.set_assistant_text(text, source="conversation")
        elif role == "user":
            # If this turn came out of a barge-in, use the verdict already
            # computed for it: REDUNDANT must not overwrite active_turn
            # with the dismissed fragment. A plain (non-overlap) turn has
            # no pending verdict and gets a full commit, as before.
            analysis = self.semantic_analyzer.consume_last_analysis()
            if analysis is not None:
                self.semantic_analyzer.commit_overlap(text, analysis)
            else:
                self.semantic_analyzer.commit_user_turn(text)

    def on_user_input_transcribed(self, event: object) -> None:
        if not self.state.agent_active:
            return
        if self.state.interrupt_fired:
            # The guard already committed to interrupting on an earlier
            # (possibly non-final) transcript for this utterance. STT can
            # still keep revising that utterance after this point — e.g.
            # Deepgram emitting a corrected/shorter is_final transcript
            # once it finishes settling — and LiveKit's own turn detector
            # (not this guard) decides which version actually becomes the
            # committed user turn handed to the LLM. Previously that
            # revision was silently dropped here with no trace, so when the
            # LLM's reply looked like it answered something the user never
            # said, there was nothing in the log to compare it against. Log
            # it (without acting on it — the guard has nothing left to do
            # once it has already fired) so the transcript that triggered
            # the interrupt and whatever STT settled on afterward are both
            # visible side by side.
            return

        raw_text = (getattr(event, "transcript", "") or "").strip()
        text = normalize_text(raw_text)
        if not text:
            return
        is_final = bool(getattr(event, "is_final", False))
        event_key = (text, is_final)
        # A duplicate of the exact last event is only safe to skip once it
        # has already been acted on (INTERRUPT/IGNORE are terminal for a
        # given transcript). A prior WAIT verdict is provisional and purely
        # time-dependent (see QUESTION_INTERRUPT_THRESHOLD etc.) — if the
        # STT layer keeps re-emitting the identical stable interim while
        # still short of that stability window, unconditionally dropping
        # every repeat here froze `speech_duration` at whatever it was on
        # the first occurrence, so the duration-based promotion to
        # INTERRUPT could then only ever fire once is_final finally
        # arrived — silently adding however long the provider takes to
        # finalize on top of the window that was supposed to bound the
        # wait.
        if (
            event_key == self.state.last_transcript_event
            and self.state.last_decision is not InterruptDecision.WAIT
        ):
            return

        self.state.last_transcript_event = event_key
        self.state.transcript_evolution.append(text)
        del self.state.transcript_evolution[:-8]

        now = time.monotonic()
        if self.state.user_speech_started_at is None:
            # STT can arrive before (or without) LiveKit emitting a matching
            # user-state transition. Start the stability clock here instead of
            # evaluating every such transcript with a permanent zero duration.
            self.state.user_speech_started_at = now
            onset_source = "transcript_fallback"
        else:
            onset_source = "user_state"
        speech_duration = max(0.0, now - self.state.user_speech_started_at)
        _decision_start = time.perf_counter()
        decision, reason = classify_interruption(
            transcript=text,
            is_final=is_final,
            posture=self.state.posture,
            speech_duration=speech_duration,
            assistant_text_normalized=self.state.assistant_text_normalized,
            posture_has_explanatory_prefix=self.state.posture_has_explanatory_prefix,
        )

        # A candidate interruption still might not be worth cutting the
        # agent off for: it can be a restatement/subset of what the user
        # already committed as their active turn (STT re-hearing itself,
        # a repeated slot value, etc). Only genuinely new meaning proceeds.
        #
        # Two categories are exempt from this second-guessing, both
        # identified by the *category* classify_interruption already put
        # them in — not by matching specific words, so this stays correct
        # as those categories grow:
        #   - explicit_command: IMMEDIATE_COMMANDS are control signals
        #     ("stop", "wait", ...), not content to compare for overlap —
        #     one coinciding with a keyword already in the active turn
        #     must not suppress it.
        #   - Confirmation/clarification postures: classify_interruption
        #     treats a final reply here as meaningful without an ambiguous-
        #     acknowledgement gate, since a short "yes"/"haan" IS a
        #     complete answer to a yes/no question, not filler — and for
        #     clarification specifically, the active turn being compared
        #     against is usually the very thing the agent just asked the
        #     user to repeat, so an exact repeat is the *expected* answer,
        #     not noise to suppress.
        #
        #   - affirmation_answers_confirmation: a yes-type filler answering a
        #     yes/no is decided by the filler rules alone; comparing "ok"
        #     against the active turn's meaning is meaningless.
        #
        #   AWAITING_ANSWER is deliberately NOT in this exempt set (unlike
        #   the other two): an open WH-question expects real new content,
        #   so a reply that's just a repeated/subset restatement of the
        #   active turn (e.g. the caller re-saying "Hyderabad" as their
        #   "answer" to "when are you traveling?") must still be measured
        #   against it and caught as REDUNDANT — that's exactly the
        #   content this comparison exists to catch, not a case to skip.
        turn_delta: TurnDelta | None = None
        analysis = None
        # Isolated separately from decision_time below: classify_interruption
        # is a cheap regex/wordlist check, but classify_overlap runs the
        # sentence-embedding model, which is the part actually worth
        # measuring when a real interruption feels slow to land.
        semantic_check_time: float | None = None
        paused_for_semantic_check = False
        if decision is InterruptDecision.INTERRUPT and reason not in SEMANTIC_EXEMPT_REASONS:
            # Stop the bot the instant this looks like it might be a real
            # interruption, before waiting on the semantic check to confirm
            # it — see _pause_playback_for_semantic_check. Explicit commands
            # never reach here (they interrupt immediately above, with
            # nothing to wait on), so there's nothing to pause for them.
            paused_for_semantic_check = self._pause_playback_for_semantic_check()
            _semantic_start = time.perf_counter()
            analysis = self.semantic_analyzer.classify_overlap(
                raw_text,
                is_final=is_final,
                # Clarification can invite an exact repeat of the user's prior
                # wording, so keep that case out of the redundancy check.
                # Confirmation affirmations are already handled by the
                # guard's filler rules; other confirmation replies should
                # still be compared so repeated attention-getters like
                # "hello" do not trigger another full response.
                expects_short_answer=(
                    self.state.posture is AgentPosture.AWAITING_CLARIFICATION
                ),
            )
            semantic_check_time = time.perf_counter() - _semantic_start
            turn_delta = analysis.delta
            if analysis.delta is TurnDelta.REDUNDANT:
                decision = InterruptDecision.IGNORE
                reason = f"redundant_turn:{analysis.reason}"
                # Snapshot this verdict for recover_dropped_turn — see
                # GuardState.last_final_redundant_text for why.
                if is_final:
                    self.state.last_final_redundant_text = raw_text.strip()
                # A dismissed fragment shouldn't leave the interim-
                # stability clock running: any further speech in this
                # same overlap must be judged on its own duration, not
                # inherit time already spent on the discarded fragment.
                self._reset_timer()
                # False alarm — hand playback back exactly where it paused
                # instead of leaving the bot silent for a redundant blip.
                if paused_for_semantic_check:
                    self._resume_playback_after_false_interruption()
            else:
                if is_final:
                    self.state.last_final_redundant_text = None
                # analysis.delta is just INTERRUPT here (the semantic check
                # only distinguishes REDUNDANT from everything else), so
                # analysis.reason carries the actual diagnostic detail
                # (e.g. "semantic_new_information:41.2%", "expected_answer").
                reason = f"{reason}+{analysis.reason}"
                # Confirmed real: leave playback paused (if we paused it) —
                # _interrupt() below fully cancels/clears it now, so there's
                # nothing to resume first.

        decision_time = time.perf_counter() - _decision_start
        self.state.last_decision = decision

        # LiveKit's own turn-completion pipeline runs independently of this
        # guard: whenever it sees a finalized user turn while the agent's
        # current speech is non-interruptible (true for every reply here,
        # since interruption.enabled=False makes this guard the sole
        # interruption owner — see the TurnHandlingOptions comment in
        # agent_stt_llm_tts_v1.py), it drops that turn *before* it reaches
        # on_user_turn_completed: no LLM call, no chat history entry, just a
        # bare "skipping reply to user input, current speech generation
        # cannot be interrupted" warning with none of this context attached
        # (agent_activity.py:2455-2461 in livekit-agents 1.6.10).
        #
        # For IGNORE that drop is harmless: is_non_answer_for_current_posture
        # would have vetoed the exact same text via StopResponse anyway, so
        # nothing is lost. INTERRUPT is also safe — `_interrupt()` below
        # forces the current speech to actually interrupt, so the SDK's own
        # pipeline no longer sees it as non-interruptible by the time it
        # runs. The one gap is a *final* transcript that resolves to WAIT:
        # classify_interruption deliberately doesn't trust every is_final
        # (see its module docstring), so it can leave genuinely final
        # content sitting in limbo, un-interrupted and un-vetoed, right
        # where the SDK's drop can take it with no recovery. Tag that one
        # combination distinctly so it's greppable instead of indistinguishable
        # from the harmless cases above.
        drop_risk = "possible_turn_loss" if (is_final and decision is InterruptDecision.WAIT) else "none"
        # Captured before _interrupt() runs, while session.current_speech
        # still reflects whatever is actually playing right now.
        interrupting_filler = self._interrupting_filler()

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] posture={self.state.posture.value} "
            f"decision={decision.value} reason={reason} is_final={is_final} "
            f"duration={speech_duration:.3f}s onset={onset_source} words={word_count(text)} "
            f"turn_delta={turn_delta.value if turn_delta else 'n/a'} "
            f"semantic_check_time={f'{semantic_check_time:.3f}s' if semantic_check_time is not None else 'n/a'} "
            f"paused_for_check={paused_for_semantic_check} "
            f"drop_risk={drop_risk} "
            f"expects={EXPECTED_USER_SPEECH[self.state.posture]} "
            f"user_filler={filler_kind(text) or 'none'} "
            f"interrupting_filler={interrupting_filler} transcript={raw_text!r}"
        )
        self._notify(
            "decision",
            {
                "decision": decision.value,
                "reason": reason,
                "is_final": is_final,
                "speech_duration": speech_duration,
                "posture": self.state.posture.value,
                "turn_delta": turn_delta.value if turn_delta else None,
                "drop_risk": drop_risk,
                "decision_time": decision_time,
                "semantic_check_time": semantic_check_time,
                "paused_for_check": paused_for_semantic_check,
                "interrupted_filler": interrupting_filler,
                "expected_user_speech": EXPECTED_USER_SPEECH[self.state.posture],
                "user_filler": filler_kind(text),
                "transcript": raw_text,
                "similarity": analysis.similarity if analysis else None,
                "semantic_reason": analysis.reason if analysis else None,
                "compared_with": (
                    self.semantic_analyzer.active_turn.text
                    if analysis and self.semantic_analyzer.active_turn
                    else None
                ),
            },
        )
        if decision is InterruptDecision.INTERRUPT:
            self._interrupt(reason, interrupting_filler=interrupting_filler)

    def on_false_interruption(self, event: object) -> None:
        resumed = bool(getattr(event, "resumed", False))
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] false_interruption resumed={resumed}"
        )
        self._notify("false_interruption", {"resumed": resumed})

    def _reset_timer(self) -> None:
        """Reset only the interim-stability clock.

        Deliberately leaves interrupt_fired and semantic_analyzer state alone:
        a dismissed fragment's turn-evolution verdict is still needed by
        on_conversation_item_added once that fragment's transcript lands,
        so this must not clear it early the way _reset_overlap() does.
        """
        self.state.user_speech_started_at = None
        self.state.last_transcript_event = None
        self.state.last_decision = None
        self.state.transcript_evolution.clear()

    def _reset_overlap(self) -> None:
        self._reset_timer()
        self.state.interrupt_fired = False
        self.state.turn_overlapped_agent = False
        self.semantic_analyzer.begin_overlap()

    def _interrupt(self, reason: str, *, interrupting_filler: bool = False) -> None:
        if self.state.interrupt_fired:
            return
        self.state.interrupt_fired = True
        # Undo any pause this guard applied while waiting on the semantic
        # check (see _pause_playback_for_semantic_check) — the SpeechHandle
        # is being torn down below regardless, but the audio output's pause
        # flag is independent of it and must be cleared explicitly or the
        # next reply's audio never plays. No-op if nothing was paused.
        self._resume_playback()
        now = time.monotonic()
        latency = (
            now - self.state.user_speech_started_at
            if self.state.user_speech_started_at is not None
            else -1.0
        )
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] MANUAL_INTERRUPT reason={reason} "
            f"interrupting_filler={interrupting_filler} decision_latency={latency:.3f}s "
            f"transcript_evolution={self.state.transcript_evolution!r}"
        )
        try:
            future = self.session.interrupt(force=True)
            future.add_done_callback(self._on_interrupt_complete)
        except RuntimeError as exc:
            applog.warning(
                f"[INTERRUPT GUARD][{self.session_label}] interrupt not applied: {exc}"
            )
        except Exception:
            applog.exception(
                f"[INTERRUPT GUARD][{self.session_label}] unexpected interruption error"
            )

    def _on_interrupt_complete(self, future: asyncio.Future[None]) -> None:
        try:
            future.result()
        except asyncio.CancelledError:
            applog.warning(f"[INTERRUPT GUARD][{self.session_label}] manual interrupt cancelled")
        except Exception:
            applog.exception(f"[INTERRUPT GUARD][{self.session_label}] manual interrupt failed")


_DROPPED_TURN_LOG_MESSAGE = (
    "skipping reply to user input, current speech generation cannot be interrupted"
)


class _DroppedTurnLogHandler(logging.Handler):
    """Bridges agent_activity.py's own drop warning back to the guard.

    See InterruptionGuard.recover_dropped_turn for why this is the only
    available signal for that drop — there is no public session event for
    it, only this log line, carrying the dropped text as extra={"user_input": ...}.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__(level=logging.WARNING)
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() != _DROPPED_TURN_LOG_MESSAGE:
            return
        user_input = getattr(record, "user_input", "")
        if user_input:
            self._callback(user_input)


def attach_interruption_guard(
    session: AgentSession,
    session_label: str | None = None,
    on_event: Callable[[str, dict], None] | None = None,
    filler_registry: object | None = None,
) -> InterruptionGuard:
    guard = InterruptionGuard(
        session=session,
        session_label=session_label or "session",
        on_event=on_event,
        filler_registry=filler_registry,
    )
    session.on("agent_state_changed")(guard.on_agent_state_changed)
    session.on("user_state_changed")(guard.on_user_state_changed)
    session.on("conversation_item_added")(guard.on_conversation_item_added)
    session.on("user_input_transcribed")(guard.on_user_input_transcribed)
    session.on("agent_false_interruption")(guard.on_false_interruption)

    dropped_turn_handler = _DroppedTurnLogHandler(guard.recover_dropped_turn)
    logging.getLogger("livekit.agents").addHandler(dropped_turn_handler)
    session.on("close")(
        lambda _event: logging.getLogger("livekit.agents").removeHandler(dropped_turn_handler)
    )
    return guard