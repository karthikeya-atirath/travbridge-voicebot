import unittest
from types import SimpleNamespace
from unittest.mock import patch

from interruption_guard import (
    AgentPosture,
    InterruptDecision,
    InterruptionGuard,
    classify_assistant_sentence,
    classify_interruption,
    infer_agent_posture,
    normalize_text,
)


class InterruptionPolicyTests(unittest.TestCase):
    def decide(self, text, *, final=True, posture=AgentPosture.EXPLAINING, duration=0.5):
        return classify_interruption(
            transcript=text,
            is_final=final,
            posture=posture,
            speech_duration=duration,
        )[0]

    def test_normalizes_mixed_language_punctuation(self):
        self.assertEqual(normalize_text("  रुको... Okay!  "), "रुको okay")

    def test_classifies_final_clause_as_question(self):
        text = "Paris is a good option. What is your budget?"
        self.assertEqual(infer_agent_posture(text), AgentPosture.AWAITING_ANSWER)

    def test_explanation_is_not_misclassified_by_earlier_question(self):
        text = "Why Paris? It has direct flights and better availability."
        self.assertEqual(infer_agent_posture(text), AgentPosture.EXPLAINING)

    def test_confirmation_ending_requires_a_whole_word(self):
        # Regression: CONFIRMATION_ENDING_PATTERN used to substring-match
        # the tail of an unrelated word ("look"/"book" end in "ok",
        # "alright" ends in "right", "incorrect" ends in "correct"),
        # misreading an ordinary statement as a yes/no question.
        for text in (
            "Let us have a look.",
            "I will book that for you.",
            "That sounds great, alright.",
            "That does not sound incorrect.",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_assistant_sentence(text).posture, AgentPosture.EXPLAINING
                )
        for text in (
            "You want to continue, right?",
            "That is correct?",
            "Ye theek hai na?",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    classify_assistant_sentence(text).posture,
                    AgentPosture.AWAITING_CONFIRMATION,
                )

    def test_commands_interrupt_immediately(self):
        self.assertEqual(self.decide("रुको", final=False, duration=0.1), InterruptDecision.INTERRUPT)

    def test_vocal_fillers_do_not_interrupt(self):
        for text in ("hmm", "hmm hmm", "uh huh", "uh-huh", "mm hmm", "mm-hmm", "हम्म"):
            with self.subTest(text=text):
                self.assertEqual(self.decide(text), InterruptDecision.IGNORE)

    def test_vocal_backchannels_can_acknowledge_a_confirmation_question(self):
        for text in ("hmm", "uh huh", "uh-huh", "mm hmm", "mm-hmm", "हम्म"):
            with self.subTest(text=text):
                self.assertEqual(
                    self.decide(text, posture=AgentPosture.AWAITING_CONFIRMATION),
                    InterruptDecision.INTERRUPT,
                )

    def test_acknowledgement_is_protected_during_explanation(self):
        self.assertEqual(self.decide("okay"), InterruptDecision.IGNORE)

    def test_mixed_acknowledgements_are_protected_during_explanation(self):
        for text in ("yeah okay", "hmm yeah", "uh huh yeah", "got it okay", "i see hmm"):
            with self.subTest(text=text):
                self.assertEqual(self.decide(text), InterruptDecision.IGNORE)

    def test_acknowledgement_plus_meaningful_speech_interrupts(self):
        for text in ("yeah change the date", "hmm actually no", "okay four members"):
            with self.subTest(text=text):
                self.assertEqual(self.decide(text), InterruptDecision.INTERRUPT)

    def test_acknowledgement_answers_a_question(self):
        self.assertEqual(
            self.decide("okay", posture=AgentPosture.AWAITING_CONFIRMATION),
            InterruptDecision.INTERRUPT,
        )

    def test_how_many_question_expects_information(self):
        self.assertEqual(
            infer_agent_posture("How many members will be travelling?"),
            AgentPosture.AWAITING_ANSWER,
        )
        self.assertEqual(
            self.decide("yeah", posture=AgentPosture.AWAITING_ANSWER),
            InterruptDecision.IGNORE,
        )

    def test_acknowledgement_does_not_answer_information_question(self):
        for text in (
            "yeah", "yes", "okay", "uh huh", "mm hmm",
            "yeah okay", "hmm yeah", "uh huh yeah", "got it okay",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    self.decide(text, posture=AgentPosture.AWAITING_ANSWER),
                    InterruptDecision.IGNORE,
                )

    def test_clarification_request_has_separate_posture(self):
        self.assertEqual(
            infer_agent_posture("Could you repeat that?"),
            AgentPosture.AWAITING_CLARIFICATION,
        )

    def test_filler_does_not_interrupt_clarification(self):
        for text in ("hmm", "okay", "uh huh", "yeah okay"):
            with self.subTest(text=text):
                self.assertEqual(
                    self.decide(text, posture=AgentPosture.AWAITING_CLARIFICATION),
                    InterruptDecision.IGNORE,
                )

    def test_meaningful_clarification_interrupts(self):
        self.assertEqual(
            self.decide("I said December", posture=AgentPosture.AWAITING_CLARIFICATION),
            InterruptDecision.INTERRUPT,
        )

    def test_value_answers_information_question(self):
        self.assertEqual(
            self.decide("four", posture=AgentPosture.AWAITING_ANSWER),
            InterruptDecision.INTERRUPT,
        )

    def test_detects_confirmation_question(self):
        self.assertEqual(
            infer_agent_posture("Would you like to continue?"),
            AgentPosture.AWAITING_CONFIRMATION,
        )

    def test_information_request_is_not_confirmation(self):
        self.assertEqual(
            infer_agent_posture(
                "Could you please tell me which month and year you are planning to travel?"
            ),
            AgentPosture.AWAITING_ANSWER,
        )

    def test_confirmation_after_context_prefix(self):
        self.assertEqual(
            infer_agent_posture(
                "Since Paris is an international destination, do you have a valid passport?"
            ),
            AgentPosture.AWAITING_CONFIRMATION,
        )

    def test_non_question_auxiliary_phrase_remains_explanation(self):
        self.assertEqual(
            infer_agent_posture("Have a nice day."),
            AgentPosture.EXPLAINING,
        )

    def test_choice_question_expects_a_value(self):
        self.assertEqual(
            infer_agent_posture("Would you prefer Standard or Value?"),
            AgentPosture.AWAITING_ANSWER,
        )

    def test_common_question_structures(self):
        cases = (
            ("What is your budget?", AgentPosture.AWAITING_ANSWER),
            ("Can you share your departure city?", AgentPosture.AWAITING_ANSWER),
            ("Please tell me your preferred date.", AgentPosture.AWAITING_ANSWER),
            ("Do you already have a visa?", AgentPosture.AWAITING_CONFIRMATION),
            ("May I continue?", AgentPosture.AWAITING_CONFIRMATION),
            ("Please confirm whether your passport is valid?", AgentPosture.AWAITING_CONFIRMATION),
            ("You have a valid passport, right?", AgentPosture.AWAITING_CONFIRMATION),
            ("क्या आपके पास पासपोर्ट है?", AgentPosture.AWAITING_CONFIRMATION),
            ("The package has breakfast included.", AgentPosture.EXPLAINING),
            ("Will Smith is an actor.", AgentPosture.EXPLAINING),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(infer_agent_posture(text), expected)

    def test_wh_question_with_aux_inversion_is_not_a_confirmation(self):
        # English WH-questions routinely invert to "<question word> ... are
        # you / will you ...", which is the same shape a yes/no confirmation
        # opens with. A WH-word appearing anywhere rules confirmation out.
        cases = (
            "First, which city are you departing from?",
            "How many people are you traveling with?",
            "What date will you be leaving?",
            "Could you please tell me when you are planning to travel?",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(infer_agent_posture(text), AgentPosture.AWAITING_ANSWER)

    def test_hindi_kis_word_anywhere_is_not_a_confirmation(self):
        # Regression: a polite "क्या आप ... चाहेंगे" opener reads like a
        # yes/no confirmation, but "किस city से" ("which city") embedded in
        # it means the sentence actually wants a value. Seen live: the bot
        # asked exactly this and a bare "Ok" was accepted as a full answer
        # because posture was misread as AWAITING_CONFIRMATION.
        self.assertEqual(
            infer_agent_posture(
                "क्या आप बताना चाहेंगे कि आप किस city से travel करेंगे, "
                "आपका departure city क्या है?"
            ),
            AgentPosture.AWAITING_ANSWER,
        )

    def test_non_answer_for_current_posture_covers_information_questions(self):
        guard = InterruptionGuard(object())
        guard.state.posture = AgentPosture.AWAITING_ANSWER
        self.assertTrue(guard.is_non_answer_for_current_posture("ok"))
        self.assertTrue(guard.is_non_answer_for_current_posture("understood"))
        self.assertFalse(guard.is_non_answer_for_current_posture("Mumbai"))

    def test_non_answer_for_current_posture_excludes_confirmation(self):
        guard = InterruptionGuard(object())
        guard.state.posture = AgentPosture.AWAITING_CONFIRMATION
        self.assertFalse(guard.is_non_answer_for_current_posture("ok"))

    def test_transcript_starts_clock_when_user_state_event_is_missing(self):
        guard = InterruptionGuard(object())
        guard.state.agent_speaking = True
        event = SimpleNamespace(transcript="four", is_final=False)
        with patch("interruption_guard.time.monotonic", return_value=12.3):
            guard.on_user_input_transcribed(event)
        self.assertEqual(guard.state.user_speech_started_at, 12.3)

    def test_unstable_single_word_waits(self):
        self.assertEqual(self.decide("actually", final=False, duration=0.2), InterruptDecision.WAIT)

    def test_final_non_filler_takes_turn(self):
        self.assertEqual(self.decide("need visa"), InterruptDecision.INTERRUPT)

    def test_single_word_final_waits_during_explanation(self):
        # Deepgram's is_final marks a settled chunk (gated by endpointing_ms,
        # as low as 75-250ms of silence), not a settled utterance — it can
        # fire mid-sentence on an ordinary breathing pause. A lone word from
        # such a chunk gets the same two-word floor as an interim one instead
        # of cutting the agent off outright.
        self.assertEqual(self.decide("visa"), InterruptDecision.WAIT)

    def test_stable_multiword_partial_takes_turn(self):
        self.assertEqual(
            self.decide("actually no", final=False, duration=0.20),
            InterruptDecision.INTERRUPT,
        )

    def test_interim_replies_use_posture_thresholds(self):
        cases = (
            (AgentPosture.EXPLAINING, "actually no", 0.19, InterruptDecision.WAIT),
            (AgentPosture.EXPLAINING, "actually no", 0.20, InterruptDecision.INTERRUPT),
            (AgentPosture.AWAITING_ANSWER, "four adults", 0.09, InterruptDecision.WAIT),
            (AgentPosture.AWAITING_ANSWER, "four adults", 0.10, InterruptDecision.INTERRUPT),
            (AgentPosture.AWAITING_CONFIRMATION, "yeah sure", 0.09, InterruptDecision.WAIT),
            (AgentPosture.AWAITING_CONFIRMATION, "yeah sure", 0.10, InterruptDecision.INTERRUPT),
        )
        for posture, text, duration, expected in cases:
            with self.subTest(posture=posture, duration=duration):
                self.assertEqual(
                    self.decide(text, final=False, posture=posture, duration=duration),
                    expected,
                )

    def test_interim_single_word_never_interrupts_answer_or_confirmation(self):
        # A lone interim word (e.g. STT still mid-utterance on "four...teen")
        # must not be enough to silence the agent — only a final transcript,
        # or at least two stable words, should take the turn.
        for posture in (AgentPosture.AWAITING_ANSWER, AgentPosture.AWAITING_CONFIRMATION):
            with self.subTest(posture=posture):
                self.assertEqual(
                    self.decide("four", final=False, posture=posture, duration=5.0),
                    InterruptDecision.WAIT,
                )


if __name__ == "__main__":
    unittest.main()
