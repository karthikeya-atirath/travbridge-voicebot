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
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Callable, Final

if TYPE_CHECKING:
    from livekit.agents import AgentSession

from app_logger import applog


class AgentPosture(str, Enum):
    EXPLAINING = "explaining"
    AWAITING_ANSWER = "awaiting_answer"
    AWAITING_CONFIRMATION = "awaiting_confirmation"


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
# actively blocked on a short yes/no). ~0.3-0.5s is roughly one spoken word
# at normal pace, so these sit just above a single word's worth of noise.
EXPLANATION_INTERRUPT_THRESHOLD: Final[float] = 0.60
QUESTION_INTERRUPT_THRESHOLD: Final[float] = 0.50
CONFIRMATION_INTERRUPT_THRESHOLD: Final[float] = 0.35


VOCAL_BACKCHANNELS: Final[frozenset[str]] = frozenset(
    {
        "hm", "hmm", "hmmm", "mm", "mmm", "uh", "um", "uh huh", "uh-huh", "mm hmm", "mm-hmm",
        "हम्म", "हूँ", "हूं", "उम्म",
    }
)
EXPLANATION_ACKNOWLEDGEMENTS: Final[frozenset[str]] = frozenset(
    {
        "ok", "okay", "yeah", "yes", "right", "alright", "i see", "got it",
        "acha", "achha", "accha", "haan", "han", "हां", "हाँ", "अच्छा", "ठीक है",
    }
)
ACKNOWLEDGEMENT_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "ok", "okay", "yeah", "yes", "right", "alright",
        "haan", "han", "acha", "achha", "accha",
        "हां", "हाँ", "अच्छा",
    }
)
ACKNOWLEDGEMENT_PHRASES: Final[frozenset[str]] = frozenset(
    {"i see", "got it", "ठीक है", "uh huh", "uh-huh", "mm hmm", "mm-hmm"}
)
VOCAL_BACKCHANNEL_TOKENS: Final[frozenset[str]] = frozenset(
    {"hm", "hmm", "hmmm", "mm", "mmm", "uh", "um", "हम्म", "हूँ", "हूं", "उम्म"}
)

IMMEDIATE_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "wait", "stop", "pause", "cancel", "repeat", "wrong", "hold on",
        "one moment", "ruko", "rukiye", "roko", "suniye", "galat",
        "रुको", "रुकिए", "रोकिए", "सुनिए", "गलत", "बंद",
    }
)

QUESTION_PATTERN = re.compile(
    r"^(?:what|why|when|where|who|how|which|whose|क्या|क्यों|कैसे|कहाँ|कहां|कब|कौन|कौनसा|कौनसी)\b",
    re.IGNORECASE,
)

# Same word list, but not anchored to the start of the clause. English's
# mandatory aux-subject inversion means a WH-question routinely contains
# "...are you...", "...will you..." after the question word ("which city
# ARE YOU departing from?", "how many people ARE YOU traveling with?") —
# exactly what CONFIRMATION_QUESTION_PATTERN below is looking for. A real
# yes/no confirmation never contains a WH-word at all, so finding one
# anywhere in an actual question rules confirmation out first.
QUESTION_WORD_ANYWHERE_PATTERN = re.compile(
    r"\b(?:what|why|when|where|who|how|which|whose|क्या|क्यों|कैसे|कहाँ|कहां|कब|कौन|कौनसा|कौनसी)\b",
    re.IGNORECASE,
)

INFORMATION_REQUEST_PATTERN = re.compile(
    r"^(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:tell|share|provide)|(?:please\s+)?(?:tell|share|provide))\b",
    re.IGNORECASE,
)

CONFIRMATION_QUESTION_PATTERN = re.compile(
    r"\b(?:can|could|would|will|may|might|do|does|did|is|are|was|were|have|has|should|shall)\s+(?:i|you|we|they|it|this|that|there)\b",
    re.IGNORECASE,
)

CONFIRMATION_REQUEST_PATTERN = re.compile(
    r"^(?:(?:please\s+)?confirm|(?:can|could|would|will)\s+you\s+(?:please\s+)?confirm)\b",
    re.IGNORECASE,
)

CONFIRMATION_TAG_PATTERN = re.compile(
    r"\b(?:right|correct|isn.t it|aren.t you|don.t you|okay)\s*\Z",
    re.IGNORECASE,
)

HINDI_CONFIRMATION_PATTERN = re.compile(
    r"^(?:क्या|kya)\s+(?:आप|आपके|आपकी|तुम|तुम्हारे|तुम्हारी|यह|वह|ये|वे|aap|aapke|aapki|tum|tumhare|tumhari|yeh|woh)(?=\s|\Z)",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    chars: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if char.isspace() or char in {"'", "-"} or category.startswith(("L", "M", "N")):
            chars.append(char)
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def word_count(text: str) -> int:
    return len(text.split())


def _all_tokens_are_backchannels(text: str) -> bool:
    if text in VOCAL_BACKCHANNELS:
        return True
    tokens = text.split()
    return bool(tokens) and all(token in VOCAL_BACKCHANNELS for token in tokens)


def _all_tokens_are_acknowledgements(text: str) -> bool:
    """Recognize sequences made entirely of acknowledgements/backchannels."""
    if text in EXPLANATION_ACKNOWLEDGEMENTS or text in VOCAL_BACKCHANNELS:
        return True

    # Remove known multi-token units before checking the remaining atoms.
    # Padding keeps replacement on phrase boundaries rather than substrings.
    remaining = f" {text} "
    for phrase in sorted(ACKNOWLEDGEMENT_PHRASES, key=len, reverse=True):
        remaining = remaining.replace(f" {phrase} ", " ")

    tokens = remaining.split()
    return not tokens or all(
        token in ACKNOWLEDGEMENT_TOKENS or token in VOCAL_BACKCHANNEL_TOKENS
        for token in tokens
    )


def infer_agent_posture(text: str) -> AgentPosture:
    """Classify the final conversational clause, not the response opening."""
    text = text.strip()
    if not text:
        return AgentPosture.EXPLAINING

    clauses = [part.strip() for part in re.split(r"[.!?।]+", text) if part.strip()]
    final_clause = normalize_text(clauses[-1] if clauses else text)
    explicit_question = text.endswith("?")

    # Requests such as "Could you tell me which month?" ask for information
    # even though they begin with a modal auxiliary. Choice questions also
    # require a value, not a generic yes/no acknowledgement.
    if INFORMATION_REQUEST_PATTERN.search(final_clause):
        return AgentPosture.AWAITING_ANSWER
    if explicit_question and re.search(r"\bor\b", final_clause):
        return AgentPosture.AWAITING_ANSWER

    # Rule out confirmation before checking for it: a WH-word anywhere in
    # an actual question means it needs a value, not a yes/no.
    if explicit_question and QUESTION_WORD_ANYWHERE_PATTERN.search(final_clause):
        return AgentPosture.AWAITING_ANSWER

    # Search the whole final clause so discourse prefixes such as
    # "Since Paris is international, do you have ...?" remain confirmations.
    if (
        CONFIRMATION_REQUEST_PATTERN.search(final_clause)
        or CONFIRMATION_TAG_PATTERN.search(final_clause)
        or HINDI_CONFIRMATION_PATTERN.search(final_clause)
        or CONFIRMATION_QUESTION_PATTERN.search(final_clause)
    ):
        return AgentPosture.AWAITING_CONFIRMATION

    if explicit_question or QUESTION_PATTERN.search(final_clause):
        return AgentPosture.AWAITING_ANSWER
    return AgentPosture.EXPLAINING


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
    if text in IMMEDIATE_COMMANDS:
        return InterruptDecision.INTERRUPT, "explicit_command"

    ambiguous_acknowledgement = _all_tokens_are_acknowledgements(text)

    # A generic "yeah" can answer "Would you like to continue?", but cannot
    # answer "How many members?". Only confirmation questions accept it.
    if posture is AgentPosture.AWAITING_CONFIRMATION:
        if is_final:
            return InterruptDecision.INTERRUPT, "reply_to_confirmation"
        if count >= 2 and speech_duration >= CONFIRMATION_INTERRUPT_THRESHOLD:
            return InterruptDecision.INTERRUPT, "reply_to_confirmation"
        return InterruptDecision.WAIT, "unstable_reply_to_confirmation"

    if posture is AgentPosture.AWAITING_ANSWER:
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
        # Optional observer for evaluation/metrics (e.g. call_metrics.py).
        # Never influences a decision — purely notified after the fact.
        self.on_event = on_event

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
            return
        # Final STT commonly arrives just after VAD transitions to listening.
        if new_state == "away":
            self._reset_overlap()

    def on_conversation_item_added(self, event: object) -> None:
        item = getattr(event, "item", None)
        if item is None or getattr(item, "role", None) != "assistant":
            return
        self.set_assistant_text(getattr(item, "text_content", "") or "", source="conversation")

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
        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] posture={self.state.posture.value} "
            f"decision={decision.value} reason={reason} is_final={is_final} "
            f"duration={speech_duration:.3f}s onset={onset_source} words={word_count(text)} transcript={raw_text!r}"
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

    def _reset_overlap(self) -> None:
        self.state.user_speech_started_at = None
        self.state.interrupt_fired = False
        self.state.last_transcript_event = None
        self.state.transcript_evolution.clear()

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
