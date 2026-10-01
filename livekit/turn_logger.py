"""Readable per-turn logging for the voice pipeline.

Each completed turn is rendered as one compact block. Only sections that
apply to the turn are printed:

    USER -> SPEECH TUNING -> ENDPOINTING (incl. end-of-turn model) -> SEMANTIC CHECK
         -> INTERRUPTION GUARD -> FILLER -> LLM -> TTS -> LATENCY

Sections that explain a decision (tuning, endpointing, semantic check, guard,
filler) end with a ``summary`` saying *why* it happened, using the recorded
numbers. LATENCY lists every measured stage; its last line,
``time_to_first_audio``, is the caller-facing number (end of user speech to
the first audio of the main reply).

Attribution rules:
- Guard decisions are made while the user is still talking, i.e. before the
  turn they produce is opened. They are buffered and attached to the next
  turn whose text they belong to, not to the reply still playing.
- Dropped-then-recovered user turns (see InterruptionGuard.recover_dropped_turn)
  never reach Agent.on_user_turn_completed; the guard's "recovery" event opens
  (or, if suppressed, directly prints) their block.
- A stage timing that comes out negative was measured against another turn's
  timestamps; it is printed as n/a instead of a misleading number.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app_logger import applog
from semantic_check import SEMANTIC_SIMILARITY_THRESHOLD, normalize_text

_BOX_WIDTH = 58


# Longest an interrupt decision may wait for its final transcript before it is
# considered to belong to a different utterance.
_GUARD_INTERRUPT_MAX_AGE = 15.0


def _now() -> float:
    return time.monotonic()


def _dt(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b < a:
        return None
    return round(b - a, 3)


def _ms(value: float | None) -> str:
    return f"{value * 1000:.0f}ms" if isinstance(value, (int, float)) else "n/a"


def _pct(value: float | None) -> str:
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "n/a"


def _header(title: str) -> str:
    label = f" {title} "
    side = max(0, (_BOX_WIDTH - len(label)) // 2)
    return ("━" * side) + label + ("━" * (_BOX_WIDTH - side - len(label)))


def _footer() -> str:
    return "━" * _BOX_WIDTH


def _section(title: str, rows: list[tuple[str, object]]) -> str:
    lines = [title]
    width = max((len(key) for key, _ in rows), default=0)
    for key, value in rows:
        lines.append(f"  {key.ljust(width)} : {value}")
    return "\n".join(lines)


# ============================================================
# WORDING
# ============================================================

_POSTURE_TEXT = {
    "awaiting_answer": "waiting for an answer to an open question",
    "awaiting_confirmation": "waiting for a yes/no confirmation",
    "awaiting_clarification": "waiting for the user to clarify",
    "explaining": "explaining (no question pending)",
}

# Why classify_interruption reached each verdict, in plain words. Keyed by the
# base reason code (text before any "+semantic..." / ":..." suffix).
_GUARD_REASON_TEXT = {
    "empty_transcript": "the transcript was empty",
    "tts_echo": "the words repeat the assistant's own current sentence (speaker bleed)",
    "explicit_command": "it is an explicit stop/wait-style command",
    "attention_getter": "the caller is calling out to the bot (hello, are you there), trusted immediately",
    "attention_getter_pending": "an attention-getter is waiting out its short minimum duration",
    "non_answer_to_confirmation": (
        "a reaction/backchannel (nice, acha, oh great, hmm) does not answer a yes/no question"
    ),
    "affirmation_answers_confirmation": (
        "a yes-type filler (yes, ok, haan, theek hai) is a complete answer to the yes/no question"
    ),
    "non_answer_to_clarification": "a bare acknowledgement does not answer a clarification request",
    "clarification": "it is the user's reply to a clarification request",
    "unstable_clarification": "the clarification reply is still too short/unstable to trust",
    "explanation_prefix_acknowledgement": (
        "a bare acknowledgement after an explanation is not a real reply to the trailing yes/no question"
    ),
    "reply_to_confirmation": "it answers the yes/no question the assistant asked",
    "unvalidated_final_fragment": "the final transcript has no recognisable answer content",
    "unstable_reply_to_confirmation": "the reply is not yet 2+ words / long enough to trust",
    "non_answer_to_information_question": "a bare acknowledgement cannot answer an open question",
    "reply_to_question": "it carries real content in answer to the open question",
    "unstable_reply_to_question": "the reply is not yet 2+ words / long enough to trust",
    "vocal_backchannel": "it is a filler sound (hmm/uh), not speech content",
    "explanation_acknowledgement": "it is just an acknowledgement while the assistant explains",
    "final_meaningful_speech": "it is a final multi-word utterance",
    "single_word_final_fragment": "a single final word may be a mid-sentence pause, so it waits for more",
    "stable_multiword_speech": "it is stable multi-word speech",
    "collecting_transcript": "the transcript is still forming",
}

_SEMANTIC_REASON_TEXT = {
    "exact_repeat": "the text is identical to the active turn",
    "single_word_repeat": "a single word already present in the active turn",
    "semantic_redundant": "cosine similarity reached the redundancy threshold",
    "semantic_new_information": "cosine similarity stayed below the redundancy threshold",
    "novel_content": "it contains content words the active turn never said, so cosine alone can't call it redundant",
    "expected_answer": "a short answer to a yes/no or clarification question is never compared",
    "no_active_turn": "there was no earlier committed turn to compare against",
    "insufficient_content": "too little real content (STT scrap) to compare",
    "insufficient_content_for_expected_answer": "too little real content (STT scrap) to count as an answer",
    "empty": "the text was empty",
    "stale_overlap_reset": "reused the verdict already given to this exact text",
}

_SUPPRESS_TEXT = {
    "redundant_turn": "suppressed as redundant, no reply generated",
    "non_answer_filler": "suppressed as a bare acknowledgement with nothing to answer, no reply generated",
    "session_closed_mid_turn": "session closed mid-turn",
}


def _base_reason(reason: str | None) -> str:
    if not reason:
        return ""
    reason = reason.split("+")[0]
    if reason.startswith("redundant_turn:"):
        return "redundant_turn"
    return reason


def _semantic_key(reason: str | None) -> str:
    return (reason or "").split(":")[0]


def _tokens(text: str) -> set[str]:
    return set(normalize_text(text).split())


# ============================================================
# RECORD
# ============================================================

def _guard_semantic_text(rec: "_Turn") -> str:
    """Where the semantic check sat in a live guard decision."""
    if rec.decision_user_filler:
        return "skipped (filler)"
    return "ran" if rec.decision_has_semantic else "not reached"


@dataclass
class _Turn:
    index: int
    event_type: str = "user_turn"
    stt_text: str = ""
    t_stt_final: float = 0.0
    transcription_delay: float | None = None
    eou_delay: float | None = None
    hook_delay: float | None = None
    t_user_speech_end: float | None = None

    t_llm_start: float | None = None
    t_llm_first_token: float | None = None
    t_llm_complete: float | None = None
    tool_calls: dict[str, dict] = field(default_factory=dict)

    t_tts_start: float | None = None
    t_tts_first_audio: float | None = None
    t_tts_complete: float | None = None
    t_playback_start: float | None = None
    t_playback_end: float | None = None

    # SpeechHandle id of the reply generation that answers this turn. LLM/TTS
    # events are routed by this id, not by "whichever turn is current", so a
    # generation that is cancelled or finishes after a newer turn opened still
    # lands on its own turn.
    speech_id: str | None = None
    flushed: bool = False

    reply_cancelled: bool = False
    suppressed: str | None = None
    note: str | None = None

    # Interruption guard (live overlap decision for the speech behind this turn)
    decision: str | None = None
    decision_reason: str | None = None
    decision_posture: str | None = None
    decision_is_final: bool | None = None
    decision_transcript: str | None = None
    decision_duration: float | None = None
    decision_time: float | None = None
    decision_paused_for_check: bool = False
    decision_interrupted_filler: bool = False
    decision_expects: str | None = None
    decision_user_filler: str | None = None
    decision_has_semantic: bool = False
    # Final-turn filler routing reported by the guard (no live overlap needed)
    filler_check: dict | None = None

    # Semantic check (cosine similarity against the active committed turn)
    sem_delta: str | None = None
    sem_reason: str | None = None
    sem_similarity: float | None = None
    sem_compared_with: str | None = None
    sem_time: float | None = None
    sem_source: str | None = None

    # Speech tuning
    tuning_pace: float | None = None
    tuning_pause: float | None = None
    tuning_mix_ratio: float | None = None
    tuning_floor: float | None = None
    tuning_ceiling: float | None = None
    tuning_profile_used: str | None = None
    tuning_next_profile: str | None = None
    tuning_params_used: dict = field(default_factory=dict)
    tuning_params_next: dict = field(default_factory=dict)
    tuning_calculation_time: float | None = None
    tuning_floor_prev: float | None = None
    tuning_error: str | None = None

    fillers: list[dict] = field(default_factory=list)

    # Audio end-of-turn model (smart_turn.py): last prediction before commit,
    # and how many predictions this turn took (>1 = the caller paused, the
    # model said "incomplete", and they resumed).
    eot_prediction: dict | None = None
    eot_predictions: int = 0
    # Seconds between this turn committing and the caller speaking again,
    # when that happened within PREMATURE_EOT_WINDOW (likely cut off).
    resumed_after_commit: float | None = None


# Caller speech starting this soon after a commit suggests the turn was
# committed while they were still mid-thought.
PREMATURE_EOT_WINDOW = 2.0


class TurnLatencyTracker:
    """Collect lifecycle signals and emit one readable block per turn."""

    def __init__(
        self,
        session_label: str = "session",
        *,
        vad_threshold: float | None = None,
        vad_min_silence: float | None = None,
        default_min_delay: float | None = None,
        default_max_delay: float | None = None,
    ) -> None:
        self.session_label = session_label
        self._vad_threshold = vad_threshold
        self._vad_min_silence = vad_min_silence
        self._default_min_delay = default_min_delay
        self._default_max_delay = default_max_delay

        self._index = 0
        self._current: _Turn | None = None
        self._closed = False
        self._last_floor: float | None = None

        # Latest guard decision not yet claimed by a turn (see module docstring).
        self._pending_guard: dict | None = None
        # Reply generations, keyed by SpeechHandle id. _by_speech holds the
        # turn each generation answers (kept after the turn prints, so late
        # events from a cancelled generation are dropped instead of landing
        # on a newer turn). _pending_speech holds timings of generations no
        # turn owns yet — preemptive generation starts the LLM (and TTS) on
        # an interim transcript before the turn commits; the turn claims them
        # once the framework reports which SpeechHandle answers it.
        self._by_speech: dict[str, _Turn] = {}
        self._pending_speech: dict[str, _Turn] = {}
        self._watched_speech: set[str] = set()
        self._done_speech: dict[str, float] = {}
        # Turn whose reply audio is playing right now.
        self._playing: _Turn | None = None
        # Name of the audio end-of-turn model, and its predictions not yet
        # claimed by a turn (predictions land before the turn opens).
        self.turn_detector_name: str | None = None
        self._pending_eot: dict | None = None
        self._pending_eot_count = 0

    # ---- turn intake ----
    def start_turn(self, stt_text: str, event_type: str = "user_turn") -> None:
        prev = self._current
        if prev is not None:
            prev.note = "superseded by a newer turn before it finished"
            if prev.speech_id is not None and prev.speech_id not in self._done_speech:
                # Its reply is still in flight: keep collecting that reply's
                # timings and print the block when it ends (see _on_speech_done).
                self._current = None
            else:
                self._flush()
        self._index += 1
        self._current = _Turn(
            index=self._index,
            event_type=event_type,
            stt_text=stt_text,
            t_stt_final=_now(),
        )
        self._claim_pending_guard(self._current)
        self._claim_pending_eot(self._current)

    # ---- end-of-turn model ----
    def record_eot_prediction(self, info: dict) -> None:
        self._pending_eot = info
        self._pending_eot_count += 1

    def _claim_pending_eot(self, rec: _Turn) -> None:
        rec.eot_prediction, self._pending_eot = self._pending_eot, None
        rec.eot_predictions, self._pending_eot_count = self._pending_eot_count, 0

    def on_user_speech_started(self) -> None:
        rec = self._current
        if rec is None or rec.event_type != "user_turn" or rec.resumed_after_commit is not None:
            return
        gap = _now() - rec.t_stt_final
        if gap <= PREMATURE_EOT_WINDOW:
            rec.resumed_after_commit = gap

    def _claim_pending_guard(self, rec: _Turn) -> None:
        pending, self._pending_guard = self._pending_guard, None
        if not pending:
            return
        heard = _tokens(pending.get("transcript") or "")
        # Only attach a decision to a turn that actually contains the words it
        # judged; otherwise it belonged to speech that never became a turn.
        # An interrupt is the exception: the guard acted on this utterance, and
        # STT routinely revises an interim ("yeah footing") into a different
        # final ("Yeah, fourteen days."), which must not hide the decision.
        recent = _now() - pending.get("_logged_at", 0.0) < _GUARD_INTERRUPT_MAX_AGE
        revised_interrupt = pending.get("decision") == "interrupt" and recent
        if not heard or not (heard <= _tokens(rec.stt_text) or revised_interrupt):
            return
        rec.decision = pending.get("decision")
        rec.decision_reason = pending.get("reason")
        rec.decision_posture = pending.get("posture")
        rec.decision_is_final = pending.get("is_final")
        rec.decision_transcript = pending.get("transcript")
        rec.decision_duration = pending.get("speech_duration")
        rec.decision_time = pending.get("decision_time")
        rec.decision_paused_for_check = bool(pending.get("paused_for_check"))
        rec.decision_interrupted_filler = bool(pending.get("interrupted_filler"))
        rec.decision_expects = pending.get("expected_user_speech")
        rec.decision_user_filler = pending.get("user_filler")
        rec.decision_has_semantic = pending.get("turn_delta") is not None
        if pending.get("turn_delta") is not None:
            self._set_semantic(
                rec,
                delta=pending["turn_delta"],
                reason=pending.get("semantic_reason"),
                similarity=pending.get("similarity"),
                compared_with=pending.get("compared_with"),
                elapsed=pending.get("semantic_check_time"),
                source="live overlap",
            )

    @staticmethod
    def _set_semantic(
        rec: _Turn,
        *,
        delta: str | None,
        reason: str | None,
        similarity: float | None,
        compared_with: str | None,
        elapsed: float | None,
        source: str,
    ) -> None:
        if delta not in ("redundant", "interrupt"):
            return
        rec.sem_delta = delta
        rec.sem_reason = reason
        rec.sem_similarity = similarity
        rec.sem_compared_with = compared_with
        rec.sem_time = elapsed
        rec.sem_source = source

    def on_eou_metrics(
        self,
        end_of_utterance_delay: float,
        transcription_delay: float,
        on_user_turn_completed_delay: float,
        speech_id: str | None = None,
    ) -> None:
        if self._current is None:
            return
        now = _now()
        self._current.t_user_speech_end = (
            now - on_user_turn_completed_delay - end_of_utterance_delay
        )
        self._current.transcription_delay = round(transcription_delay, 3)
        self._current.eou_delay = round(end_of_utterance_delay, 3)
        self._current.hook_delay = round(on_user_turn_completed_delay, 3)
        if speech_id:
            # The SpeechHandle the framework chose to answer this turn — a
            # reused preemptive generation or a fresh one.
            self._bind(self._current, speech_id)

    # ---- reply generation routing ----
    def bind_speech(self, speech) -> None:
        """Attach a SpeechHandle created for the current turn (a direct
        session.say(), or a recovered turn's generate_reply())."""
        if self._current is not None and speech is not None:
            self._watch(speech)
            self._bind(self._current, speech.id)

    def _bind(self, rec: _Turn, speech_id: str) -> None:
        if rec.speech_id == speech_id:
            return
        if rec.speech_id is not None:
            self._by_speech.pop(rec.speech_id, None)
        rec.speech_id = speech_id
        self._by_speech[speech_id] = rec
        pending = self._pending_speech.pop(speech_id, None)
        if pending is not None:
            for name in (
                "t_llm_start", "t_llm_first_token", "t_llm_complete",
                "t_tts_start", "t_tts_first_audio", "t_tts_complete",
            ):
                if getattr(rec, name) is None:
                    setattr(rec, name, getattr(pending, name))
            rec.tool_calls = {**pending.tool_calls, **rec.tool_calls}
        if speech_id in self._done_speech:
            # The generation already ended (e.g. interrupted as stale before
            # the framework reported it); nothing more will arrive for it.
            self._flush_rec(rec)

    def _watch(self, speech) -> None:
        if speech is None or speech.id in self._watched_speech:
            return
        self._watched_speech.add(speech.id)
        speech.add_done_callback(lambda handle: self._on_speech_done(handle.id))

    def _rec_for(self, speech) -> _Turn | None:
        """The record a reply-generation event belongs to: the turn that owns
        the SpeechHandle, a pending holder if no turn owns it yet, or None if
        that turn already printed (a late event from a cancelled reply)."""
        if speech is None:
            return self._current
        self._watch(speech)
        rec = self._by_speech.get(speech.id)
        if rec is not None:
            return None if rec.flushed else rec
        rec = self._pending_speech.get(speech.id)
        if rec is None:
            self._prune_speech_state()
            rec = _Turn(index=0, t_stt_final=_now(), speech_id=speech.id)
            self._pending_speech[speech.id] = rec
        return rec

    def _prune_speech_state(self) -> None:
        cutoff = _now() - 60.0
        for sid in [k for k, v in self._pending_speech.items() if v.t_stt_final < cutoff]:
            del self._pending_speech[sid]
        for sid in [k for k, t in self._done_speech.items() if t < cutoff]:
            del self._done_speech[sid]

    def _on_speech_done(self, speech_id: str) -> None:
        self._done_speech[speech_id] = _now()
        self._watched_speech.discard(speech_id)
        # A pending generation that ends unclaimed was a discarded
        # preemptive attempt (or the opening greeting).
        self._pending_speech.pop(speech_id, None)
        rec = self._by_speech.get(speech_id)
        if rec is None or rec.flushed:
            return
        if rec.t_playback_start is not None and rec.t_playback_end is None:
            rec.t_playback_end = _now()
        self._flush_rec(rec)

    def suppress_turn(self, reason: str) -> None:
        if self._current is None:
            return
        self._current.suppressed = reason
        self._flush()

    # ---- speech tuner ----
    def record_speech_tuning(
        self,
        *,
        pace: float,
        pause: float,
        mix_ratio: float,
        endpointing_floor: float | None,
        endpointing_ceiling: float | None,
        profile_used: str,
        next_profile: str,
        params_used: dict,
        params_next: dict,
        calculation_time: float | None = None,
    ) -> None:
        rec = self._current
        if rec is None:
            return
        rec.tuning_pace = pace
        rec.tuning_pause = pause
        rec.tuning_mix_ratio = mix_ratio
        rec.tuning_floor = endpointing_floor
        rec.tuning_ceiling = endpointing_ceiling
        rec.tuning_profile_used = profile_used
        rec.tuning_next_profile = next_profile
        rec.tuning_params_used = params_used
        rec.tuning_params_next = params_next
        rec.tuning_calculation_time = calculation_time
        rec.tuning_floor_prev = self._last_floor
        if endpointing_floor is not None:
            self._last_floor = endpointing_floor

    def record_speech_tuning_error(self, message: str) -> None:
        if self._current is not None:
            self._current.tuning_error = message

    # ---- tool-call filler ----
    def on_tool_filler_spoken(
        self,
        tool_name: str,
        text: str,
        step: int = 0,
        elapsed: float | None = None,
        limit: float | None = None,
    ) -> None:
        if self._current is None:
            return
        self._current.fillers.append(
            {"tool": tool_name, "text": text, "step": step, "elapsed": elapsed, "limit": limit}
        )

    # ---- LLM ----
    # ``speech`` is the SpeechHandle the generation runs under (None falls
    # back to the current turn, e.g. a direct session.say()).
    def on_llm_start(self, speech=None) -> None:
        rec = self._rec_for(speech)
        # Tool follow-up steps rerun llm_node under the same SpeechHandle;
        # keep the first dispatch so ttft covers the whole reply.
        if rec is not None and rec.t_llm_start is None:
            rec.t_llm_start = _now()

    def on_llm_first_signal(self, speech=None) -> None:
        pass

    def on_llm_first_token(self, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is not None and rec.t_llm_first_token is None:
            rec.t_llm_first_token = _now()

    def on_llm_complete(self, text: str, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is not None:
            rec.t_llm_complete = _now()

    def on_llm_cancelled(self, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is None:
            return
        if rec.index == 0:
            self._pending_speech.pop(rec.speech_id, None)
            return
        rec.reply_cancelled = True
        self._flush_rec(rec)

    # ---- TTS ----
    def on_tts_start(self, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is not None and rec.t_tts_start is None:
            rec.t_tts_start = _now()

    def on_tts_first_audio(self, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is not None and rec.t_tts_first_audio is None:
            rec.t_tts_first_audio = _now()

    def on_tts_complete(self, chars: int, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is None:
            return
        rec.t_tts_complete = _now()
        self._maybe_flush(rec)

    def on_tts_no_output(self, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is not None and rec.index != 0:
            self._flush_rec(rec)

    def on_tts_cancelled(self, chars: int, speech=None) -> None:
        rec = self._rec_for(speech)
        if rec is None:
            return
        if rec.index == 0:
            self._pending_speech.pop(rec.speech_id, None)
            return
        if rec.t_playback_start is not None and rec.t_playback_end is None:
            rec.t_playback_end = _now()
        rec.reply_cancelled = True
        self._flush_rec(rec)

    # ---- tool calls ----
    def on_tool_call_start(self, call_id: str, name: str) -> None:
        if self._current is None:
            return
        self._current.tool_calls[call_id] = {
            "name": name,
            "t_start": _now(),
            "t_end": None,
        }

    def on_tool_call_end(self, call_id: str, status: str) -> None:
        if self._current is None:
            return
        entry = self._current.tool_calls.get(call_id)
        if entry is None:
            return
        entry["t_end"] = _now()
        if status not in ("completed", "success"):
            applog.info(
                f"[TURN][{self.session_label}][turn {self._current.index}] "
                f"tool call {entry['name']!r} ended status={status}"
            )

    # ---- room playback ----
    def on_playback_start(self, speech_id: str | None = None) -> None:
        rec = self._by_speech.get(speech_id) if speech_id else self._current
        if rec is None or rec.flushed:
            return
        self._playing = rec
        if rec.t_playback_start is None:
            rec.t_playback_start = _now()

    def on_playback_end(self) -> None:
        rec, self._playing = self._playing, None
        if rec is None or rec.flushed or rec.t_playback_start is None:
            return
        if rec.t_playback_end is None:
            rec.t_playback_end = _now()
        self._maybe_flush(rec)

    def _maybe_flush(self, rec: _Turn) -> None:
        if (
            rec.index != 0
            and rec.t_tts_complete is not None
            and rec.t_playback_end is not None
        ):
            self._flush_rec(rec)

    # ---- guard observer ----
    def record_guard_event(self, kind: str, data: dict) -> None:
        if kind == "decision":
            # Never let a later wait/ignore overwrite an interrupt already
            # decided for the same utterance.
            prev = self._pending_guard
            if prev is None or prev.get("decision") != "interrupt":
                self._pending_guard = {**data, "_logged_at": _now()}
        elif kind == "redundancy":
            # Fired from Agent.on_user_turn_completed, after start_turn, so
            # _current is already this turn.
            if self._current is not None:
                self._set_semantic(
                    self._current,
                    delta=data.get("delta"),
                    reason=data.get("reason"),
                    similarity=data.get("similarity"),
                    compared_with=data.get("active"),
                    elapsed=data.get("decision_time"),
                    source="final turn",
                )
        elif kind == "filler_check":
            # Fired from Agent.on_user_turn_completed, after start_turn.
            if self._current is not None:
                self._current.filler_check = data
        elif kind == "recovery":
            self._on_recovery(data)
        elif kind == "recovery_speech":
            self.bind_speech(data.get("speech"))

    def _apply_recovery_semantic(self, rec: _Turn | None, data: dict) -> None:
        if rec is None:
            return
        self._set_semantic(
            rec,
            delta=data.get("delta"),
            reason=data.get("reason"),
            similarity=data.get("similarity"),
            compared_with=data.get("compared_with"),
            elapsed=None,
            source="dropped turn",
        )
        rec.decision_posture = rec.decision_posture or data.get("posture")
        if data.get("user_filler"):
            rec.filler_check = {
                "bot_speech": data.get("posture"),
                "expected_user_speech": data.get("expected_user_speech"),
                "user_filler": data["user_filler"],
                "verdict": (
                    "non_answer_suppressed"
                    if data.get("suppress_reason") == "non_answer_filler"
                    else "answer_to_llm"
                ),
            }

    def _on_recovery(self, data: dict) -> None:
        if data.get("action") == "regenerated":
            # Open the block now; the guard reports the new reply's
            # SpeechHandle right after ("recovery_speech"). Events from the
            # reply being replaced stay on its own turn via its speech id.
            self.start_turn(data.get("text", ""), "recovered_turn")
            self._apply_recovery_semantic(self._current, data)
            return
        # Suppressed: the reply still playing must keep its own block intact,
        # so print this one standalone without touching _current.
        self._index += 1
        rec = _Turn(
            index=self._index,
            event_type="dropped_turn",
            stt_text=data.get("text", ""),
            t_stt_final=_now(),
            suppressed=data.get("suppress_reason"),
        )
        self._claim_pending_guard(rec)
        self._apply_recovery_semantic(rec, data)
        self._render(rec)

    # ---- shutdown ----
    def flush_dangling(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._current is not None:
            self._current.suppressed = (
                self._current.suppressed or "session_closed_mid_turn"
            )
            self._flush()
        # Superseded turns whose reply never finished.
        for rec in list(self._by_speech.values()):
            self._flush_rec(rec)

    def _flush(self) -> None:
        if self._current is not None:
            self._flush_rec(self._current)

    def _flush_rec(self, rec: _Turn) -> None:
        if rec.flushed or rec.index == 0:
            return
        rec.flushed = True
        if self._current is rec:
            self._current = None
        if self._playing is rec:
            self._playing = None
        self._render(rec)

    # ============================================================
    # RENDER
    # ============================================================
    def _render(self, rec: _Turn) -> None:
        blocks = [_header(f"TURN {rec.index}")]

        blocks.append(self._user_block(rec))
        if rec.tuning_profile_used is not None:
            blocks.append(self._tuning_block(rec))
        if rec.eou_delay is not None:
            blocks.append(self._endpointing_block(rec))
        if rec.sem_delta is not None:
            blocks.append(self._semantic_block(rec))
        if rec.decision is not None:
            blocks.append(self._guard_block(rec))
        if rec.filler_check is not None:
            blocks.append(self._filler_check_block(rec))
        if rec.fillers:
            blocks.append(self._filler_block(rec))

        replied = any(
            v is not None
            for v in (rec.t_llm_start, rec.t_llm_first_token, rec.t_tts_start, rec.t_tts_first_audio)
        )
        if replied:
            blocks.append(self._llm_block(rec))
            blocks.append(self._tts_block(rec))
        blocks.append(self._latency_block(rec))

        blocks.append(_footer())
        applog.info("\n" + "\n\n".join(blocks))

    @staticmethod
    def _user_block(rec: _Turn) -> str:
        if rec.suppressed:
            outcome = _SUPPRESS_TEXT.get(rec.suppressed, f"suppressed ({rec.suppressed})")
        elif rec.reply_cancelled:
            outcome = "reply started, then was cut off by the user"
        else:
            outcome = "replied"
        rows: list[tuple[str, object]] = [("text", f'"{rec.stt_text}"')]
        if rec.event_type != "user_turn":
            rows.append(("type", rec.event_type))
        rows.append(("outcome", outcome))
        if rec.note:
            rows.append(("note", rec.note))
        return _section("USER", rows)

    # ---- speech tuning ----
    @staticmethod
    def _tuning_block(rec: _Turn) -> str:
        used, nxt = rec.tuning_profile_used, rec.tuning_next_profile
        p_used, p_next = rec.tuning_params_used, rec.tuning_params_next

        rows: list[tuple[str, object]] = [
            ("profile_in_use", used),
            ("pace", f"{rec.tuning_pace:.2f} wps"),
            ("pause", _ms(rec.tuning_pause)),
            ("mix_ratio", f"{rec.tuning_mix_ratio:.2f}"),
        ]
        if p_used.get("tts_pace") is not None:
            rows.append(("tts_pace_in_use", p_used["tts_pace"]))
        if p_used.get("stt_endpointing_ms") is not None:
            rows.append(("stt_endpointing_in_use", f"{p_used['stt_endpointing_ms']}ms"))
        if p_used.get("endpointing_max_delay") is not None:
            rows.append(("endpointing_max_in_use", f"{p_used['endpointing_max_delay']:.2f}s"))
        if p_used.get("vad_min_silence") is not None:
            rows.append(("vad_min_silence_in_use", f"{p_used['vad_min_silence']:.2f}s"))
        rows.append(("calculation_time", _ms(rec.tuning_calculation_time)))

        measured = (
            f"Measured pace {rec.tuning_pace:.2f} wps, pause {_ms(rec.tuning_pause)}, "
            f"mix {rec.tuning_mix_ratio:.2f}."
        )
        if rec.tuning_error:
            summary = (
                f"{measured} Reclassified {used} -> {nxt} but applying it failed "
                f"({rec.tuning_error}); this turn's parameters stay in force."
            )
        elif used != nxt:
            changes = []
            for key, label, unit in (
                ("tts_pace", "tts pace", ""),
                ("stt_endpointing_ms", "stt endpointing", "ms"),
                ("endpointing_max_delay", "endpointing ceiling", "s"),
                ("vad_min_silence", "VAD silence", "s"),
            ):
                old, new = p_used.get(key), p_next.get(key)
                if old != new:
                    changes.append(f"{label} {old}{unit} -> {new}{unit}")
            change_text = ", ".join(changes) if changes else "no live parameter differs between the two policies"
            summary = (
                f"{measured} Reclassified {used} -> {nxt}. Changed for the next turn: {change_text}. "
                "The learned endpointing minimum is preserved; only its ceiling changes."
            )
        else:
            summary = (
                f"{measured} {used} policy kept: tts pace {p_used.get('tts_pace')}, "
                f"endpointing ceiling {p_used.get('endpointing_max_delay')}s, "
                f"VAD silence {p_used.get('vad_min_silence')}s."
            )
        rows.append(("summary", summary))
        return _section("SPEECH TUNING", rows)

    # ---- dynamic endpointing ----
    def _endpointing_block(self, rec: _Turn) -> str:
        ep_min = rec.tuning_floor if rec.tuning_floor is not None else self._default_min_delay
        ep_max = rec.tuning_ceiling if rec.tuning_ceiling is not None else self._default_max_delay
        profile = rec.tuning_profile_used or "default"
        eou, stt_final = rec.eou_delay, rec.transcription_delay

        rows: list[tuple[str, object]] = [
            ("profile", profile),
            ("min_delay", _ms(ep_min)),
            ("max_delay", _ms(ep_max)),
            ("observed_eou_delay", _ms(eou)),
            ("stt_final_delay", _ms(stt_final)),
        ]
        vad_min_silence = rec.tuning_params_used.get("vad_min_silence", self._vad_min_silence)
        if vad_min_silence is not None:
            rows.append(("vad_min_silence", _ms(vad_min_silence)))

        window = f"min {_ms(ep_min)} / max {_ms(ep_max)}"
        if ep_max is not None and eou > ep_max:
            excess = eou - ep_max
            rows.append(("over_max_by", _ms(excess)))
            if stt_final is not None and stt_final > ep_max:
                cause = (
                    f"the final transcript only arrived {_ms(stt_final)} after speech ended, and the "
                    "turn cannot commit before it"
                )
            else:
                cause = "the STT final transcript delay does not explain it, so the extra wait came from elsewhere in the pipeline"
            summary = (
                f"Using the {profile} window ({window}). The observed end-of-utterance delay {_ms(eou)} "
                f"exceeded max_delay by {_ms(excess)}. Endpointing itself never waits past max_delay, so "
                f"the extra time was not deliberate patience: {cause}."
            )
        elif ep_min is not None and eou < ep_min:
            summary = (
                f"Using the {profile} window ({window}). The turn committed {_ms(eou)} after speech ended, "
                f"under min_delay: the endpointer released it as early as it was allowed to."
            )
        else:
            summary = (
                f"Using the {profile} window ({window}). The turn committed {_ms(eou)} after speech ended, "
                "inside the window, so endpointing waited for the pause and was not capped."
            )
        if (
            rec.tuning_floor is not None
            and rec.tuning_floor_prev is not None
            and abs(rec.tuning_floor - rec.tuning_floor_prev) > 1e-9
        ):
            summary += (
                f" Live-learned min_delay moved {_ms(rec.tuning_floor_prev)} -> {_ms(rec.tuning_floor)} "
                "since the previous turn."
            )
        rows.append(("summary", summary))
        rows.extend(self._eot_rows(rec, ep_max))
        return _section("ENDPOINTING", rows)

    def _eot_rows(self, rec: _Turn, ep_max: float | None) -> list[tuple[str, object]]:
        rows: list[tuple[str, object]] = []
        if self.turn_detector_name:
            rows.append(("turn_detector", self.turn_detector_name))
        pred = rec.eot_prediction
        if pred is not None:
            verdict = "complete" if pred.get("complete") else "incomplete"
            rows.append(("eot_probability", f"{pred.get('probability', 0.0):.3f}"))
            rows.append(("eot_threshold", f"{pred.get('threshold', 0.0):.2f}"))
            rows.append(("eot_verdict", verdict + (" (inference failed)" if pred.get("failed") else "")))
            rows.append(("eot_inference", _ms((pred.get("inference_ms") or 0.0) / 1000)))
            rows.append(("eot_predictions_this_turn", rec.eot_predictions))
            eou = rec.eou_delay
            if eou is not None and ep_max is not None:
                committed_by = (
                    "incomplete ceiling (max_delay reached, caller stayed silent)"
                    if not pred.get("complete") and eou >= 0.95 * ep_max
                    else "complete path (min_delay)" if pred.get("complete")
                    else "incomplete path"
                )
                rows.append(("committed_by", committed_by))
            rows.extend(self._eot_timing_rows(rec, pred))
        if rec.resumed_after_commit is not None:
            rows.append((
                "premature_eot",
                f"caller spoke again {_ms(rec.resumed_after_commit)} after the commit",
            ))
        return rows

    @staticmethod
    def _eot_timing_rows(rec: _Turn, pred: dict) -> list[tuple[str, object]]:
        """Place the end-of-turn prediction on the speech end -> commit timeline.

        The framework starts the prediction after ~200ms of VAD silence and
        measures the endpointing delay from speech end, not from the verdict,
        so verdict -> commit is whatever is left of min/max_delay (0 when the
        delay already elapsed while the model ran).
        """
        speech_end, eou = rec.t_user_speech_end, rec.eou_delay
        t_req, t_verdict = pred.get("t_requested"), pred.get("t_verdict")
        if speech_end is None or eou is None or t_req is None or t_verdict is None:
            return []
        commit = speech_end + eou
        requested = t_req - speech_end
        verdict = t_verdict - speech_end
        after_verdict = commit - t_verdict
        rows: list[tuple[str, object]] = [
            ("eot_requested_after_speech_end", _ms(requested)),
            ("eot_verdict_after_speech_end", _ms(verdict)),
            ("eot_verdict_to_commit", _ms(after_verdict) if after_verdict >= 0 else "n/a (verdict after commit)"),
        ]
        if after_verdict >= 0:
            rows.append((
                "eot_timeline",
                f"speech end -> {_ms(requested)} silence before the model was asked -> "
                f"{_ms(verdict - requested)} to verdict -> {_ms(after_verdict)} more until commit "
                f"(total {_ms(eou)})",
            ))
        return rows

    # ---- semantic check ----
    @staticmethod
    def _semantic_block(rec: _Turn) -> str:
        redundant = rec.sem_delta == "redundant"
        threshold = SEMANTIC_SIMILARITY_THRESHOLD
        score = rec.sem_similarity
        key = _semantic_key(rec.sem_reason)
        why = _SEMANTIC_REASON_TEXT.get(key, rec.sem_reason or "no detail recorded")

        if redundant:
            action = "suppressed (no interruption, no new reply)"
        else:
            action = "not suppressed (treated as new information)"

        rows: list[tuple[str, object]] = [
            ("checked_at", rec.sem_source or "n/a"),
            ("compared_with", f'"{rec.sem_compared_with}"' if rec.sem_compared_with else "n/a"),
            ("cosine_similarity", _pct(score) if score is not None else "n/a (not computed)"),
            ("threshold", f">= {_pct(threshold)} counts as redundant"),
            ("verdict", "REDUNDANT" if redundant else "NEW_INFORMATION"),
            ("action", action),
            ("check_time", _ms(rec.sem_time)),
        ]

        if score is not None and key in ("semantic_redundant", "semantic_new_information"):
            if redundant:
                summary = (
                    f"Redundant speech observed: cosine similarity {_pct(score)} is at or above the "
                    f"{_pct(threshold)} threshold against the earlier turn, so it was suppressed and "
                    "the assistant did not react to it again."
                )
            else:
                summary = (
                    f"Cosine similarity {_pct(score)} is below the {_pct(threshold)} threshold, so it "
                    f"reads as new information ({_pct(threshold - score)} short of being redundant) and "
                    "was not suppressed."
                )
        elif redundant:
            summary = (
                f"Redundant speech observed: {why}"
                + (f" (score {_pct(score)})" if score is not None else "")
                + ", so it was suppressed and the assistant did not react to it again."
            )
        else:
            summary = f"Not suppressed: {why}, so it was treated as new information."
        rows.append(("summary", summary))
        return _section("SEMANTIC CHECK", rows)

    # ---- final-turn filler routing ----
    @staticmethod
    def _filler_check_block(rec: _Turn) -> str:
        fc = rec.filler_check or {}
        answered = fc.get("verdict") == "answer_to_llm"
        rows: list[tuple[str, object]] = [
            ("heard", f'"{rec.stt_text}"'),
            ("bot_speech", fc.get("bot_speech") or "n/a"),
            ("expected_user_speech", fc.get("expected_user_speech") or "n/a"),
            ("user_filler", fc.get("user_filler") or "n/a"),
            ("semantic_check", "skipped (filler)"),
            ("verdict", "ANSWER -> sent to LLM" if answered else "NON-ANSWER -> suppressed, no reply"),
            (
                "summary",
                f'"{rec.stt_text}" is a {fc.get("user_filler")} filler while the bot expected '
                f'{fc.get("expected_user_speech")}: '
                + (
                    "it answers that, so it went straight to the LLM without a semantic check."
                    if answered
                    else "it does not answer that, so it was dropped without a semantic check."
                ),
            ),
        ]
        return _section("FILLER CHECK", rows)

    # ---- interruption guard ----
    @staticmethod
    def _guard_block(rec: _Turn) -> str:
        decision = rec.decision
        base = _base_reason(rec.decision_reason)
        posture = _POSTURE_TEXT.get(rec.decision_posture or "", rec.decision_posture or "an unknown state")
        heard = rec.decision_transcript or rec.stt_text

        if base == "redundant_turn":
            trigger = (
                "it qualified as a possible interruption, but the semantic check judged it redundant "
                f"(cosine {_pct(rec.sem_similarity)} vs threshold {_pct(SEMANTIC_SIMILARITY_THRESHOLD)})"
            )
        else:
            trigger = _GUARD_REASON_TEXT.get(base, rec.decision_reason or "no detail recorded")
            if "semantic_new_information" in (rec.decision_reason or "") and rec.sem_similarity is not None:
                trigger += (
                    f"; the semantic check also found it new (cosine {_pct(rec.sem_similarity)} "
                    f"< threshold {_pct(SEMANTIC_SIMILARITY_THRESHOLD)})"
                )

        if decision == "interrupt":
            effect = "so the assistant's speech was stopped"
            if rec.decision_paused_for_check:
                effect += " (it had been paused while the semantic check ran)"
            if rec.decision_interrupted_filler:
                effect += "; the audio cut off was a filler line"
        elif decision == "ignore":
            effect = "so playback was left running" + (
                " (resumed after the brief pause for the check)" if rec.decision_paused_for_check else ""
            )
        else:
            effect = "so no action was taken yet and the guard kept waiting for more speech"

        summary = (
            f'Heard "{heard}" while the assistant was {posture}. {decision.upper()}: {trigger}, {effect}.'
        )

        rows: list[tuple[str, object]] = [
            ("heard", f'"{heard}"'),
            ("bot_speech", rec.decision_posture or "n/a"),
            ("expected_user_speech", rec.decision_expects or "n/a"),
            ("user_filler", rec.decision_user_filler or "none (real content)"),
            ("semantic_check", _guard_semantic_text(rec)),
            ("decision", decision),
            ("reason", rec.decision_reason or "n/a"),
            ("is_final", "n/a" if rec.decision_is_final is None else str(bool(rec.decision_is_final)).lower()),
            ("speech_duration", _ms(rec.decision_duration)),
            ("decision_time", _ms(rec.decision_time)),
            ("summary", summary),
        ]
        return _section("INTERRUPTION GUARD", rows)

    # ---- filler ----
    @staticmethod
    def _filler_block(rec: _Turn) -> str:
        tool_time: dict[str, float] = {}
        for entry in rec.tool_calls.values():
            span = _dt(entry["t_start"], entry["t_end"])
            if span is not None:
                tool_time[entry["name"]] = max(tool_time.get(entry["name"], 0.0), span)

        rows: list[tuple[str, object]] = []
        parts = []
        for f in rec.fillers:
            step = f["step"]
            label = f"filler_{step}"
            rows.append((label, f'"{f["text"]}"'))
            rows.append((f"{label}_fired_at", f"{_ms(f['elapsed'])} into {f['tool']} (limit {_ms(f['limit'])})"))
            parts.append(
                f"{f['tool']} was still running {_ms(f['elapsed'])} after it started, past the "
                f"{_ms(f['limit'])} limit, so filler {step} was spoken"
            )
        tools = sorted({f["tool"] for f in rec.fillers})
        total = max((tool_time[t] for t in tools if t in tool_time), default=None)
        rows.append(("tool_total_time", _ms(total)))
        summary = "; ".join(parts) + "."
        if total is not None:
            summary += f" The tool finished after {_ms(total)} in total."
        if rec.decision_interrupted_filler:
            summary += " The user spoke over the filler and cut it off."
        rows.append(("summary", summary))
        return _section("FILLER", rows)

    # ---- LLM / TTS ----
    @staticmethod
    def _tool_span(rec: _Turn) -> float | None:
        starts = [e["t_start"] for e in rec.tool_calls.values()]
        ends = [e["t_end"] for e in rec.tool_calls.values() if e["t_end"] is not None]
        return _dt(min(starts), max(ends)) if starts and ends else None

    @staticmethod
    def _stage(rec: _Turn, start: float | None, end: float | None, *, llm: bool = False) -> str:
        """A stage duration, or why it could not be measured."""
        value = _dt(start, end)
        if value is not None:
            return _ms(value)
        if llm and rec.event_type == "silence_reprompt":
            return "n/a (scripted line, no LLM call)"
        if rec.reply_cancelled and start is not None:
            return "n/a (reply cancelled before this point)"
        if rec.reply_cancelled:
            return "n/a (reply cancelled before this stage started)"
        if rec.speech_id is None:
            return "n/a (no reply generation was attached to this turn)"
        return "n/a (stage did not report)"

    def _llm_block(self, rec: _Turn) -> str:
        rows: list[tuple[str, object]] = [
            ("ttft", self._stage(rec, rec.t_llm_start, rec.t_llm_first_token, llm=True)),
            ("generation_time", self._stage(rec, rec.t_llm_first_token, rec.t_llm_complete, llm=True)),
        ]
        if rec.tool_calls:
            names = ", ".join(sorted({e["name"] for e in rec.tool_calls.values()}))
            rows.append(("tool_calls", f"{len(rec.tool_calls)} [{names}] took {_ms(self._tool_span(rec))}"))
        return _section("LLM", rows)

    def _tts_block(self, rec: _Turn) -> str:
        return _section(
            "TTS",
            [
                ("ttfb", self._stage(rec, rec.t_tts_start, rec.t_tts_first_audio)),
                ("synthesis_time", self._stage(rec, rec.t_tts_start, rec.t_tts_complete)),
            ],
        )

    # ---- latency ----
    @staticmethod
    def _eot_decision_time(rec: _Turn) -> tuple[str, float] | None:
        """Seconds from the caller's speech end to the end-of-turn model's
        last verdict for this turn, with that verdict."""
        pred = rec.eot_prediction
        if pred is None or rec.t_user_speech_end is None or pred.get("t_verdict") is None:
            return None
        verdict = "complete" if pred.get("complete") else "incomplete"
        return verdict, round(pred["t_verdict"] - rec.t_user_speech_end, 3)

    def _latency_block(self, rec: _Turn) -> str:
        speech_end = rec.t_user_speech_end or rec.t_stt_final
        # Main-reply audio only (filler handles are excluded upstream).
        first_audio_at = rec.t_playback_start or rec.t_tts_first_audio
        first_audio = _dt(speech_end, first_audio_at)

        hook_done = (
            rec.t_user_speech_end + rec.eou_delay + (rec.hook_delay or 0.0)
            if rec.t_user_speech_end is not None and rec.eou_delay is not None
            else rec.t_stt_final
        )
        stages: list[tuple[str, float | None]] = []
        if rec.eou_delay is not None:
            stages.append(("stt_final", rec.transcription_delay))
            eot_decision = self._eot_decision_time(rec)
            if eot_decision is not None:
                verdict, elapsed = eot_decision
                stages.append((f"eot_decision (speech end -> {verdict} verdict)", elapsed))
            stages.append(("endpointing (speech end -> turn commit)", rec.eou_delay))
            stages.append(("turn_hook (guard/veto checks)", rec.hook_delay))
        if rec.t_llm_first_token is not None and hook_done is not None:
            # Anchored to commit (user done speaking), not to when the LLM
            # call was dispatched — dispatch can predate commit entirely
            # under preemptive generation, which isn't a useful "stage" on
            # its own: whether the request that produced this first token
            # was ever abandoned doesn't matter, only whether the FINAL
            # reply's first token was ready by the time the turn committed.
            # A negative value means it was ready early — nothing left to
            # wait on but TTS, so it's reported as zero rather than as a
            # (misleading) negative latency.
            commit_to_first_token = rec.t_llm_first_token - hook_done
            if commit_to_first_token >= 0:
                stages.append(("commit_to_llm_first_token", round(commit_to_first_token, 3)))
            else:
                stages.append(("commit_to_llm_first_token", 0.0))
        if rec.tool_calls:
            stages.append(("tool_calls", self._tool_span(rec)))
        if rec.t_tts_start is not None:
            stages.append(("tts_ttfb", _dt(rec.t_tts_start, rec.t_tts_first_audio)))
        if rec.sem_time is not None:
            stages.append(("semantic_check", rec.sem_time))
        if rec.decision_time is not None:
            stages.append(("guard_decision", rec.decision_time))

        rows: list[tuple[str, object]] = [(name, _ms(value)) for name, value in stages]
        if first_audio is not None:
            slowest = max(
                (s for s in stages if s[1] is not None and not s[0].startswith(("guard", "semantic"))),
                key=lambda s: s[1],
                default=None,
            )
            rows.append(("time_to_first_audio", _ms(first_audio)))
            summary = (
                f"The caller heard the first audio {_ms(first_audio)} after they stopped speaking"
                + (f"; the largest measured stage was {slowest[0].split(' (')[0]} at {_ms(slowest[1])}." if slowest else ".")
            )
            if rec.t_llm_first_token is not None and hook_done is not None and rec.t_llm_first_token < hook_done:
                summary += (
                    f" The reply's first token was already ready {_ms(hook_done - rec.t_llm_first_token)}"
                    " before this turn committed (preemptive generation got ahead of the user);"
                    " commit_to_llm_first_token is 0 because nothing was left to wait on but TTS."
                )
            if rec.fillers:
                summary += " A filler line played earlier in the wait (see FILLER); this figure is the main reply."
        else:
            rows.append(("time_to_first_audio", "n/a"))
            summary = (
                "No reply audio was produced for this turn"
                + (f" ({rec.suppressed})" if rec.suppressed else "")
                + ", so there is no first-audio latency."
            )
        rows.append(("summary", summary))
        return _section("LATENCY", rows)


def attach_turn_logger(
    session,
    session_label: str,
    interruption_guard,
    filler_registry=None,
    *,
    vad_threshold: float | None = None,
    vad_min_silence: float | None = None,
    default_min_delay: float | None = None,
    default_max_delay: float | None = None,
) -> TurnLatencyTracker:
    """Attach the consolidated turn logger to the existing session events.

    ``filler_registry`` is used only to exclude tool-filler SpeechHandles from
    playback-start/end tracking, so ``time_to_first_audio`` always measures the
    main reply. The endpointing min/max shown per turn come from speech tuning
    when available, with ``default_min_delay``/``default_max_delay`` as fallback.
    """
    tracker = TurnLatencyTracker(
        session_label=session_label,
        vad_threshold=vad_threshold,
        vad_min_silence=vad_min_silence,
        default_min_delay=default_min_delay,
        default_max_delay=default_max_delay,
    )

    interruption_guard.add_observer(tracker.record_guard_event)

    def _on_metrics_collected(event) -> None:
        m = getattr(event, "metrics", None)
        if m is None or getattr(m, "type", None) != "eou_metrics":
            return
        tracker.on_eou_metrics(
            end_of_utterance_delay=m.end_of_utterance_delay,
            transcription_delay=m.transcription_delay,
            on_user_turn_completed_delay=m.on_user_turn_completed_delay,
            speech_id=getattr(m, "speech_id", None),
        )

    def _on_agent_state_changed(event) -> None:
        if filler_registry is not None and filler_registry.is_filler(
            session.current_speech
        ):
            return
        if getattr(event, "new_state", None) == "speaking":
            speech = session.current_speech
            tracker.on_playback_start(speech.id if speech is not None else None)
        else:
            tracker.on_playback_end()

    def _on_tool_execution_updated(event) -> None:
        update = getattr(event, "update", None)
        update_type = getattr(update, "type", None)
        if update_type == "tool_call_started":
            fnc_call = update.function_call
            tracker.on_tool_call_start(fnc_call.call_id, fnc_call.name)
        elif update_type == "tool_call_ended":
            tracker.on_tool_call_end(update.call_id, update.status)

    def _on_user_state_changed(event) -> None:
        if getattr(event, "new_state", None) == "speaking":
            tracker.on_user_speech_started()

    session.on("metrics_collected")(_on_metrics_collected)
    session.on("agent_state_changed")(_on_agent_state_changed)
    session.on("user_state_changed")(_on_user_state_changed)
    session.on("tool_execution_updated")(_on_tool_execution_updated)
    session.on("close")(lambda _event: tracker.flush_dangling())

    return tracker
