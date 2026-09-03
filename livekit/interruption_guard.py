"""Deterministic, low-latency interruption policy for the voice agent.

The guard owns semantic interruption decisions. LiveKit's VAD gate remains a
conservative safety net for speech the guard does not recognize. Keeping these
responsibilities separate prevents the speech tuner from making fillers more
likely to stop playback.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Final

if TYPE_CHECKING:
    from livekit.agents import AgentSession

from app_logger import applog
from turn_evolution import (
    BACKCHANNEL_PHRASES,
    BACKCHANNEL_TOKENS,
    LEXICAL_ACKNOWLEDGEMENT_PHRASES,
    LEXICAL_ACKNOWLEDGEMENT_TOKENS,
    SORTED_ACKNOWLEDGEMENT_PHRASES,
    TurnDelta,
    TurnEvolutionAnalyzer,
    normalize_text,
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
# is trusted enough to cut the agent off. A final transcript always cuts in
# immediately regardless of this value — these only gate the interim path.
# Ordered by how much benefit of the doubt each posture owes the agent:
# explanations are the least urgent to interrupt (losing mid-explanation
# content costs the most), confirmations the most urgent (the agent is
# actively blocked on a short yes/no). Tuned down from 0.60/0.50/0.35s to
# claw back latency against the pipeline's own ~1s delay — at these values
# the window is barely above the two-word floor already required, so it is
# no longer much of a guard against a false-start/mis-hear self-correcting;
# see the dry-run note in the commit for the actual tradeoff this buys.
EXPLANATION_INTERRUPT_THRESHOLD: Final[float] = 0.20
QUESTION_INTERRUPT_THRESHOLD: Final[float] = 0.10
CONFIRMATION_INTERRUPT_THRESHOLD: Final[float] = 0.10


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


def _strip_command_prefix_fillers(text: str) -> str:
    tokens = text.split()
    while tokens and tokens[0] in COMMAND_PREFIX_FILLERS:
        tokens = tokens[1:]
    return " ".join(tokens)

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

# Choice questions ("Standard or Value?", "Delhi या Mumbai?") require a
# value, not a yes/no acknowledgement. "ya" (romanized) is only matched as
# a whole word since it collides with the Hinglish spelling of "yeah" —
# low risk here since this classifies the bot's own generated text, not
# free-form user speech.
CHOICE_CONJUNCTION_PATTERN = re.compile(
    r"\bor\b|\bya\b|(?:^|(?<=\s))या(?=\s|\Z)",
    re.IGNORECASE,
)

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


# Clarification requests are questions too, but a bare filler does not answer them.
CLARIFICATION_PATTERN = re.compile(
    r"(?:\b(?:clarify|repeat|say that again|what did you (?:say|mean))\b|"
    r"(?:^|(?<=\s))(?:दोबारा|फिर से|स्पष्ट)(?=\s|\Z)|"
    r"\b(?:dobara|phir se)\b)",
    re.IGNORECASE,
)


def word_count(text: str) -> int:
    return len(text.split())


def _all_tokens_are_backchannels(text: str) -> bool:
    if text in BACKCHANNEL_TOKENS or text in BACKCHANNEL_PHRASES:
        return True
    tokens = text.split()
    return bool(tokens) and all(token in BACKCHANNEL_TOKENS for token in tokens)


def _all_tokens_are_acknowledgements(text: str) -> bool:
    """Recognize sequences made entirely of acknowledgements/backchannels."""
    if (
        text in LEXICAL_ACKNOWLEDGEMENT_TOKENS
        or text in LEXICAL_ACKNOWLEDGEMENT_PHRASES
        or text in BACKCHANNEL_TOKENS
        or text in BACKCHANNEL_PHRASES
    ):
        return True

    # Remove known multi-token units before checking the remaining atoms.
    # Padding keeps replacement on phrase boundaries rather than substrings.
    remaining = f" {text} "
    for phrase in SORTED_ACKNOWLEDGEMENT_PHRASES:
        remaining = remaining.replace(f" {phrase} ", " ")

    tokens = remaining.split()
    return not tokens or all(
        token in LEXICAL_ACKNOWLEDGEMENT_TOKENS or token in BACKCHANNEL_TOKENS
        for token in tokens
    )


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
    user_speech_started_at: float | None = None
    interrupt_fired: bool = False
    last_transcript_event: tuple[str, bool] | None = None
    transcript_evolution: list[str] = field(default_factory=list)


def classify_interruption(
    *,
    transcript: str,
    is_final: bool,
    posture: AgentPosture,
    speech_duration: float,
) -> tuple[InterruptDecision, str]:
    """Pure decision function, separated from LiveKit event side effects."""
    text = normalize_text(transcript)
    count = word_count(text)
    if not text:
        return InterruptDecision.WAIT, "empty_transcript"
    if text in IMMEDIATE_COMMANDS or _strip_command_prefix_fillers(text) in IMMEDIATE_COMMANDS:
        return InterruptDecision.INTERRUPT, "explicit_command"

    ambiguous_acknowledgement = _all_tokens_are_acknowledgements(text)

    if posture is AgentPosture.AWAITING_CLARIFICATION:
        if ambiguous_acknowledgement:
            return InterruptDecision.IGNORE, "non_answer_to_clarification"
        if is_final:
            return InterruptDecision.INTERRUPT, "clarification"
        if count >= 2 and speech_duration >= QUESTION_INTERRUPT_THRESHOLD:
            return InterruptDecision.INTERRUPT, "clarification"
        return InterruptDecision.WAIT, "unstable_clarification"

    # A generic "yeah" can answer "Would you like to continue?", but cannot
    # answer "How many members?". Only confirmation questions accept it.
    if posture is AgentPosture.AWAITING_CONFIRMATION:
        if is_final:
            return InterruptDecision.INTERRUPT, "reply_to_confirmation"
        if count >= 2 and speech_duration >= CONFIRMATION_INTERRUPT_THRESHOLD:
            return InterruptDecision.INTERRUPT, "reply_to_confirmation"
        return InterruptDecision.WAIT, "unstable_reply_to_confirmation"

    if posture is AgentPosture.AWAITING_ANSWER:
        # A WH-question needs real content ("Mumbai", "four people") — a bare
        # acknowledgement/backchannel never answers "which city", no matter
        # how finalized it is. (Unlike AWAITING_CONFIRMATION just above,
        # where a bare "ok"/"hmm" IS a complete yes/no answer.)
        if ambiguous_acknowledgement:
            return InterruptDecision.IGNORE, "non_answer_to_information_question"
        if is_final:
            return InterruptDecision.INTERRUPT, "reply_to_question"
        if count >= 2 and speech_duration >= QUESTION_INTERRUPT_THRESHOLD:
            return InterruptDecision.INTERRUPT, "reply_to_question"
        return InterruptDecision.WAIT, "unstable_reply_to_question"

    if _all_tokens_are_backchannels(text):
        return InterruptDecision.IGNORE, "vocal_backchannel"

    if _all_tokens_are_acknowledgements(text):
        return InterruptDecision.IGNORE, "explanation_acknowledgement"

    # Final non-fillers are intentional enough to take the turn. Interim
    # multi-word speech gets a short stability window before interrupting.
    if is_final:
        return InterruptDecision.INTERRUPT, "final_meaningful_speech"
    if count >= 2 and speech_duration >= EXPLANATION_INTERRUPT_THRESHOLD:
        return InterruptDecision.INTERRUPT, "stable_multiword_speech"
    return InterruptDecision.WAIT, "collecting_transcript"


class InterruptionGuard:
    def __init__(
        self,
        session: AgentSession,
        session_label: str = "session",
        on_event: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.session = session
        self.session_label = session_label
        self.state = GuardState()
        # Tracks the last committed user turn and classifies whether new
        # overlapping speech restates it, extends it, corrects it, or is a
        # genuinely new turn — gates whether a candidate interruption should
        # actually cut off playback.
        self.turn_analyzer = TurnEvolutionAnalyzer()
        # Optional observer for evaluation/metrics (e.g. call_metrics.py).
        # Never influences a decision — purely notified after the fact.
        self.on_event = on_event

    def is_non_answer_for_current_posture(self, text: str) -> bool:
        """True when `text` is pure filler (ack/backchannel) while the agent's
        last posture was AWAITING_CLARIFICATION or AWAITING_ANSWER — the two
        postures where real content is required and a bare "ok"/"hmm" isn't
        an answer. AWAITING_CONFIRMATION is deliberately excluded: a bare
        acknowledgement genuinely does answer a yes/no question there.

        Unlike classify_interruption, this is meant to be called on every
        finalized user turn regardless of whether the agent was speaking:
        `posture` outlives the agent's turn (it's only overwritten by the
        next assistant utterance), so a filler reply arriving after the
        agent has gone quiet and is still waiting on an answer/clarification
        must be caught too, not just barge-in fragments.
        """
        normalized = normalize_text(text)
        if not normalized:
            return False
        if self.state.posture not in (
            AgentPosture.AWAITING_CLARIFICATION,
            AgentPosture.AWAITING_ANSWER,
        ):
            return False
        return _all_tokens_are_acknowledgements(normalized) or _all_tokens_are_backchannels(
            normalized
        )

    def is_redundant_turn(self, text: str) -> bool:
        """True when turn evolution's most recent overlap verdict for this
        exact text was REDUNDANT — i.e. classify_interruption saw
        meaningful, non-filler content, but comparing it against the
        active committed turn showed it adds nothing new (a restatement,
        a subset of already-given information, an exact repeat...).

        That REDUNDANT verdict already stops classify_interruption's
        candidate INTERRUPT from cutting off TTS (see the decision
        downgrade in on_user_input_transcribed), but by itself it does
        nothing about the second half of the problem: the same finalized
        transcript is still handed to the LLM as the next turn once the
        agent stops talking, producing a reply to content the agent
        already addressed. This is meant to be checked from
        Agent.on_user_turn_completed to veto that generation too.
        """
        analysis = self.turn_analyzer.last_analysis
        if analysis is None or analysis.delta is not TurnDelta.REDUNDANT:
            return False
        return self.turn_analyzer.candidate_normalized == normalize_text(text)

    def set_assistant_text(self, text: str, *, source: str) -> None:
        if not text.strip():
            return
        posture = infer_agent_posture(text)
        self.state.posture = posture
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] posture={posture.value} "
            f"source={source} assistant_text={text!r}"
        )

    def on_agent_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking":
            if not self.state.agent_speaking:
                self._reset_overlap()
            self.state.agent_speaking = True
            return
        if new_state in {"idle", "listening", "thinking"}:
            self.state.agent_speaking = False
            self._reset_overlap()

    def on_user_state_changed(self, event: object) -> None:
        new_state = getattr(event, "new_state", None)
        if new_state == "speaking" and self.state.agent_speaking:
            if self.state.user_speech_started_at is None:
                self.state.user_speech_started_at = time.monotonic()
                self.state.last_transcript_event = None
                self.state.transcript_evolution.clear()
                self.turn_analyzer.begin_overlap()
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
            self.set_assistant_text(text, source="conversation")
        elif role == "user":
            # If this turn came out of a barge-in, use the verdict already
            # computed for it: REDUNDANT must not overwrite active_turn
            # with the dismissed fragment. A plain (non-overlap) turn has
            # no pending verdict and gets a full commit, as before.
            analysis = self.turn_analyzer.consume_last_analysis()
            if analysis is not None:
                self.turn_analyzer.commit_overlap(text, analysis)
            else:
                self.turn_analyzer.commit_user_turn(text)

    def on_user_input_transcribed(self, event: object) -> None:
        if not self.state.agent_speaking or self.state.interrupt_fired:
            return

        raw_text = (getattr(event, "transcript", "") or "").strip()
        text = normalize_text(raw_text)
        if not text:
            return
        is_final = bool(getattr(event, "is_final", False))
        event_key = (text, is_final)
        if event_key == self.state.last_transcript_event:
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
        decision, reason = classify_interruption(
            transcript=text,
            is_final=is_final,
            posture=self.state.posture,
            speech_duration=speech_duration,
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
        #   - Answer/confirmation/clarification postures: classify_interruption
        #     treats a final reply here as meaningful without an ambiguous-
        #     acknowledgement gate, since a short "yes"/"haan" IS a
        #     complete answer to a yes/no question, not filler — and for
        #     clarification specifically, the active turn being compared
        #     against is usually the very thing the agent just asked the
        #     user to repeat, so an exact repeat is the *expected* answer,
        #     not noise to suppress.
        turn_delta: TurnDelta | None = None
        if decision is InterruptDecision.INTERRUPT and reason != "explicit_command":
            analysis = self.turn_analyzer.classify_overlap(
                raw_text,
                is_final=is_final,
                expects_short_answer=self.state.posture in {
                    AgentPosture.AWAITING_ANSWER,
                    AgentPosture.AWAITING_CONFIRMATION,
                    AgentPosture.AWAITING_CLARIFICATION,
                },
            )
            turn_delta = analysis.delta
            if analysis.delta is TurnDelta.REDUNDANT:
                decision = InterruptDecision.IGNORE
                reason = f"redundant_turn:{analysis.reason}"
                # A dismissed fragment shouldn't leave the interim-
                # stability clock running: any further speech in this
                # same overlap must be judged on its own duration, not
                # inherit time already spent on the discarded fragment.
                self._reset_timer()
            else:
                # analysis.delta is just INTERRUPT here (turn evolution
                # now only distinguishes REDUNDANT from everything else),
                # so analysis.reason carries the actual diagnostic detail
                # (e.g. "new_information", "expected_answer").
                reason = f"{reason}+{analysis.reason}"

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] posture={self.state.posture.value} "
            f"decision={decision.value} reason={reason} is_final={is_final} "
            f"duration={speech_duration:.3f}s onset={onset_source} words={word_count(text)} "
            f"turn_delta={turn_delta.value if turn_delta else 'n/a'} transcript={raw_text!r}"
        )
        if self.on_event is not None:
            self.on_event(
                "decision",
                {
                    "decision": decision.value,
                    "reason": reason,
                    "is_final": is_final,
                    "speech_duration": speech_duration,
                    "posture": self.state.posture.value,
                    "turn_delta": turn_delta.value if turn_delta else None,
                },
            )
        if decision is InterruptDecision.INTERRUPT:
            self._interrupt(reason)

    def on_false_interruption(self, event: object) -> None:
        resumed = bool(getattr(event, "resumed", False))
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] false_interruption resumed={resumed}"
        )
        if self.on_event is not None:
            self.on_event("false_interruption", {"resumed": resumed})

    def _reset_timer(self) -> None:
        """Reset only the interim-stability clock.

        Deliberately leaves interrupt_fired and turn_analyzer state alone:
        a dismissed fragment's turn-evolution verdict is still needed by
        on_conversation_item_added once that fragment's transcript lands,
        so this must not clear it early the way _reset_overlap() does.
        """
        self.state.user_speech_started_at = None
        self.state.last_transcript_event = None
        self.state.transcript_evolution.clear()

    def _reset_overlap(self) -> None:
        self._reset_timer()
        self.state.interrupt_fired = False
        self.turn_analyzer.begin_overlap()

    def _interrupt(self, reason: str) -> None:
        if self.state.interrupt_fired:
            return
        self.state.interrupt_fired = True
        now = time.monotonic()
        latency = (
            now - self.state.user_speech_started_at
            if self.state.user_speech_started_at is not None
            else -1.0
        )
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] MANUAL_INTERRUPT reason={reason} "
            f"decision_latency={latency:.3f}s transcript_evolution={self.state.transcript_evolution!r}"
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
            applog.info(f"[INTERRUPT GUARD][{self.session_label}] manual interrupt completed")
        except asyncio.CancelledError:
            applog.warning(f"[INTERRUPT GUARD][{self.session_label}] manual interrupt cancelled")
        except Exception:
            applog.exception(f"[INTERRUPT GUARD][{self.session_label}] manual interrupt failed")


def attach_interruption_guard(
    session: AgentSession,
    session_label: str | None = None,
    on_event: Callable[[str, dict], None] | None = None,
) -> InterruptionGuard:
    guard = InterruptionGuard(
        session=session, session_label=session_label or "session", on_event=on_event
    )
    session.on("agent_state_changed")(guard.on_agent_state_changed)
    session.on("user_state_changed")(guard.on_user_state_changed)
    session.on("conversation_item_added")(guard.on_conversation_item_added)
    session.on("user_input_transcribed")(guard.on_user_input_transcribed)
    session.on("agent_false_interruption")(guard.on_false_interruption)
    return guard
