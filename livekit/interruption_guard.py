"""
Low-latency interruption guard for self-hosted LiveKit agents.

Design
------
Keep LiveKit's native VAD interruption handling enabled.

Native LiveKit handles normal multi-word interruptions using:
    - min_duration
    - min_words
    - false_interruption_timeout
    - resume_false_interruption

This guard only handles the important semantic exceptions:

1. Agent is asking a question:
       A one-word reply such as:
       "yes", "no", "Cambodia", "September", "हाँ"
       is meaningful and should interrupt immediately.

2. Agent is explaining:
       A one-word acknowledgement normally should NOT manually interrupt.

3. Clear one-word commands:
       "wait", "stop", "रुको", etc.
       should interrupt immediately even during an explanation.

4. Very safe backchannels:
       "hmm", "uh-huh", "हम्म"
       never cause a manual interruption.

No LLM or additional model is used in the real-time path.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from livekit.agents import AgentSession

from app_logger import applog


# ---------------------------------------------------------------------------
# STATES
# ---------------------------------------------------------------------------


class AgentPosture(str, Enum):
    ASKING_QUESTION = "asking_question"
    EXPLAINING = "explaining"


class InterruptDecision(str, Enum):
    INTERRUPT = "interrupt"
    IGNORE = "ignore"
    DEFER_TO_NATIVE = "defer_to_native"


# ---------------------------------------------------------------------------
# VERY SMALL HIGH-CONFIDENCE WORD SETS
# ---------------------------------------------------------------------------

# These are vocal backchannels that almost never carry useful information.
#
# Keep this list intentionally small.
SAFE_BACKCHANNELS: Final[frozenset[str]] = frozenset(
    {
        # English / universal vocal sounds
        "hmm",
        "hm",
        "mm",
        "mmm",
        "uh",
        "um",
        "uh huh",
        "mm hmm",

        # Hindi / Devanagari vocal sounds
        "हम्म",
        "हूँ",
        "हूं",
        "उम्म",

        # Common two-word acknowledgements that could otherwise hit
        # native min_words=2.
        "i see",
        "ठीक है",
    }
)


# Only extremely clear one-word commands belong here.
#
# Do NOT add:
# visa, price, booking, payment, hotel, etc.
IMMEDIATE_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        # English
        "wait",
        "stop",
        "pause",
        "cancel",
        "repeat",
        "wrong",

        # Romanized Hindi
        "ruko",
        "rukiye",
        "roko",
        "suniye",
        "galat",

        # Devanagari
        "रुको",
        "रुकिए",
        "रोकिए",
        "सुनिए",
        "गलत",
        "बंद",
    }
)


# ---------------------------------------------------------------------------
# TEXT HELPERS
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """
    Normalize English, Hindi and mixed-language STT output while
    preserving Devanagari characters.
    """

    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.replace("’", "'")

    chars: list[str] = []

    for char in text:
        category = unicodedata.category(char)

        if (
            char.isspace()
            or char in {"'", "-"}
            or category.startswith(("L", "M", "N"))
        ):
            chars.append(char)
        else:
            chars.append(" ")

    return " ".join("".join(chars).split())


def word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# QUESTION / EXPLANATION POSTURE
# ---------------------------------------------------------------------------


# Small structural patterns, NOT a list of conversation scenarios.
QUESTION_PATTERN = re.compile(
    r"""
    ^(
        can|could|would|will|
        do|does|did|
        is|are|was|were|
        have|has|
        what|where|when|why|how|who|which|
        please\s+(share|provide|confirm|tell)|
        share|provide|confirm|tell|
        क्या|क्यों|कैसे|कहाँ|कहां|कब|कौन|
        कृपया|बताइए|बताएं
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def infer_agent_posture(text: str) -> AgentPosture:
    """
    Determine whether the final part of the assistant's message is
    asking the user for information / confirmation.
    """

    text = text.strip()

    if not text:
        return AgentPosture.EXPLAINING

    # Most reliable case.
    if text.endswith("?"):
        return AgentPosture.ASKING_QUESTION

    # Only inspect the final sentence because an answer may be:
    #
    # "The package includes breakfast. Would you like to continue?"
    #
    parts = [
        part.strip()
        for part in re.split(r"[.!?।]+", text)
        if part.strip()
    ]

    final_part = parts[-1] if parts else text
    final_part = normalize_text(final_part)

    if QUESTION_PATTERN.search(final_part):
        return AgentPosture.ASKING_QUESTION

    return AgentPosture.EXPLAINING


# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GuardState:
    posture: AgentPosture = AgentPosture.EXPLAINING

    agent_speaking: bool = False

    agent_speech_started_at: float | None = None
    user_speech_started_at: float | None = None

    interrupt_fired: bool = False

    last_transcript_event: tuple[str, bool] | None = None

    transcript_evolution: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# GUARD
# ---------------------------------------------------------------------------


class InterruptionGuard:
    """
    Small semantic override around LiveKit's native VAD interruption gate.

    Important:
    This does NOT replace LiveKit interruption handling.

    Native LiveKit handles normal multi-word interruptions.

    This class mainly handles one-word semantic cases.
    """

    def __init__(
        self,
        session: AgentSession,
        session_label: str = "session",
    ) -> None:

        self.session = session
        self.session_label = session_label

        self.state = GuardState()

    # ------------------------------------------------------------------
    # AGENT STATE
    # ------------------------------------------------------------------

    def on_agent_state_changed(self, event: object) -> None:

        new_state = getattr(event, "new_state", None)

        if new_state == "speaking":

            if not self.state.agent_speaking:

                self.state.agent_speech_started_at = time.monotonic()

                self.state.user_speech_started_at = None

                self.state.interrupt_fired = False

                self.state.last_transcript_event = None

                self.state.transcript_evolution.clear()

            self.state.agent_speaking = True

            return

        if new_state in {
            "idle",
            "listening",
            "thinking",
        }:

            self.state.agent_speaking = False

            self.state.user_speech_started_at = None

    # ------------------------------------------------------------------
    # USER / VAD STATE
    # ------------------------------------------------------------------

    def on_user_state_changed(self, event: object) -> None:

        new_state = getattr(event, "new_state", None)

        # User began speaking while agent is speaking.
        if (
            new_state == "speaking"
            and self.state.agent_speaking
        ):

            self.state.user_speech_started_at = time.monotonic()

            self.state.last_transcript_event = None

            self.state.transcript_evolution.clear()

            return

        # Don't immediately clear on "listening".
        #
        # Deepgram final STT may arrive shortly after VAD says the user
        # stopped speaking.

        if new_state == "away":

            self.state.user_speech_started_at = None

            self.state.last_transcript_event = None

            self.state.transcript_evolution.clear()

    # ------------------------------------------------------------------
    # AGENT MESSAGE
    # ------------------------------------------------------------------

    def on_conversation_item_added(self, event: object) -> None:

        item = getattr(event, "item", None)

        if item is None:
            return

        if getattr(item, "role", None) != "assistant":
            return

        text = (
            getattr(item, "text_content", "")
            or ""
        ).strip()

        if not text:
            return

        self.state.posture = infer_agent_posture(text)

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] "
            f"posture={self.state.posture.value} "
            f"assistant_text={text!r}"
        )

    # ------------------------------------------------------------------
    # USER STT
    # ------------------------------------------------------------------

    def on_user_input_transcribed(self, event: object) -> None:

        # We only care about speech overlapping the bot.
        if not self.state.agent_speaking:
            return

        raw_text = (
            getattr(event, "transcript", "")
            or ""
        ).strip()

        if not raw_text:
            return

        text = normalize_text(raw_text)

        if not text:
            return

        is_final = bool(
            getattr(event, "is_final", False)
        )

        event_key = (
            text,
            is_final,
        )

        # Deepgram can emit repeated partials.
        if event_key == self.state.last_transcript_event:
            return

        self.state.last_transcript_event = event_key

        self.state.transcript_evolution.append(text)

        if len(self.state.transcript_evolution) > 8:
            self.state.transcript_evolution.pop(0)

        decision, reason = self._decide(
            transcript=text,
            is_final=is_final,
        )

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] "
            f"posture={self.state.posture.value} "
            f"decision={decision.value} "
            f"reason={reason} "
            f"is_final={is_final} "
            f"words={word_count(text)} "
            f"transcript={raw_text!r}"
        )

        if decision == InterruptDecision.INTERRUPT:
            self._interrupt(reason)

    # ------------------------------------------------------------------
    # DECISION
    # ------------------------------------------------------------------

    def _decide(
        self,
        transcript: str,
        is_final: bool,
    ) -> tuple[InterruptDecision, str]:

        count = word_count(transcript)

        # --------------------------------------------------------------
        # 1. SAFE BACKCHANNEL
        # --------------------------------------------------------------

        if transcript in SAFE_BACKCHANNELS:

            return (
                InterruptDecision.IGNORE,
                "safe_backchannel",
            )

        # --------------------------------------------------------------
        # 2. AGENT ASKED A QUESTION
        #
        # This is the most important custom behavior.
        #
        # Native min_words=2 would normally ignore:
        #
        #   "yes"
        #   "no"
        #   "Cambodia"
        #   "September"
        #   "हाँ"
        #
        # But here these are meaningful answers.
        # --------------------------------------------------------------

        if (
            self.state.posture
            == AgentPosture.ASKING_QUESTION
        ):

            # Only manually handle the case native gating cannot:
            # a single-word answer.
            if count == 1:

                return (
                    InterruptDecision.INTERRUPT,
                    "one_word_reply_to_question",
                )

            # Two or more words:
            # native LiveKit gate can handle it.
            return (
                InterruptDecision.DEFER_TO_NATIVE,
                "multiword_reply_native_gate",
            )

        # --------------------------------------------------------------
        # 3. AGENT IS EXPLAINING
        #
        # Single-word acknowledgements should normally NOT manually stop
        # the bot.
        # --------------------------------------------------------------

        if count == 1:

            # But clear stop commands should immediately stop it.
            if transcript in IMMEDIATE_COMMANDS:

                return (
                    InterruptDecision.INTERRUPT,
                    "one_word_command",
                )

            # Everything else remains below native min_words=2.
            return (
                InterruptDecision.IGNORE,
                "single_word_during_explanation",
            )

        # --------------------------------------------------------------
        # 4. NORMAL MULTI-WORD INTERRUPTION
        #
        # Let LiveKit do what it is already optimized to do.
        # --------------------------------------------------------------

        return (
            InterruptDecision.DEFER_TO_NATIVE,
            "native_gate",
        )

    # ------------------------------------------------------------------
    # MANUAL INTERRUPT
    # ------------------------------------------------------------------

    def _interrupt(
        self,
        reason: str,
    ) -> None:

        if self.state.interrupt_fired:
            return

        self.state.interrupt_fired = True

        now = time.monotonic()

        if self.state.user_speech_started_at is not None:

            decision_latency = (
                now
                - self.state.user_speech_started_at
            )

        else:

            decision_latency = -1.0

        if self.state.agent_speech_started_at is not None:

            agent_speaking_for = (
                now
                - self.state.agent_speech_started_at
            )

        else:

            agent_speaking_for = -1.0

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] "
            f"MANUAL_INTERRUPT "
            f"reason={reason} "
            f"decision_latency={decision_latency:.3f}s "
            f"agent_speaking_for={agent_speaking_for:.3f}s "
            f"transcript_evolution="
            f"{self.state.transcript_evolution!r}"
        )

        try:

            future = self.session.interrupt(
                force=True
            )

            future.add_done_callback(
                self._on_interrupt_complete
            )

        except RuntimeError as exc:

            applog.warning(
                f"[INTERRUPT GUARD][{self.session_label}] "
                f"interrupt not applied: {exc}"
            )

        except Exception:

            applog.exception(
                f"[INTERRUPT GUARD][{self.session_label}] "
                "unexpected interruption error"
            )

    def _on_interrupt_complete(
        self,
        future: asyncio.Future[None],
    ) -> None:

        try:

            future.result()

            applog.info(
                f"[INTERRUPT GUARD][{self.session_label}] "
                "manual interrupt completed"
            )

        except asyncio.CancelledError:

            applog.warning(
                f"[INTERRUPT GUARD][{self.session_label}] "
                "manual interrupt future cancelled"
            )

        except Exception:

            applog.exception(
                f"[INTERRUPT GUARD][{self.session_label}] "
                "manual interrupt failed"
            )

    # ------------------------------------------------------------------
    # FALSE INTERRUPTION LOGGING
    # ------------------------------------------------------------------

    def on_false_interruption(
        self,
        event: object,
    ) -> None:

        resumed = bool(
            getattr(event, "resumed", False)
        )

        applog.info(
            f"[INTERRUPT GUARD][{self.session_label}] "
            f"false_interruption "
            f"resumed={resumed}"
        )


# ---------------------------------------------------------------------------
# ATTACH
# ---------------------------------------------------------------------------


def attach_interruption_guard(
    session: AgentSession,
    session_label: str | None = None,
) -> InterruptionGuard:

    guard = InterruptionGuard(
        session=session,
        session_label=session_label or "session",
    )

    @session.on("agent_state_changed")
    def _agent_state(event: object) -> None:
        guard.on_agent_state_changed(event)

    @session.on("user_state_changed")
    def _user_state(event: object) -> None:
        guard.on_user_state_changed(event)

    @session.on("conversation_item_added")
    def _conversation_item(event: object) -> None:
        guard.on_conversation_item_added(event)

    @session.on("user_input_transcribed")
    def _user_transcript(event: object) -> None:
        guard.on_user_input_transcribed(event)

    @session.on("agent_false_interruption")
    def _false_interruption(event: object) -> None:
        guard.on_false_interruption(event)

    return guard