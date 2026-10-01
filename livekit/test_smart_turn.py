"""Unit tests for smart_turn.py. Run from livekit/: python -m pytest test_smart_turn.py"""

import asyncio

import numpy as np
import pytest
from livekit import rtc
from livekit.agents.voice.turn import _StreamingTurnDetector, _StreamingTurnDetectorStream

import smart_turn
from smart_turn import SAMPLE_RATE, SmartTurnDetector, parse_thresholds


def _frame(seconds: float, rate: int, value: int = 1000, channels: int = 1) -> rtc.AudioFrame:
    n = int(seconds * rate)
    data = np.full(n * channels, value, dtype=np.int16)
    return rtc.AudioFrame(data.tobytes(), rate, channels, n)


def _run(coro):
    return asyncio.run(coro)


def test_satisfies_livekit_streaming_protocols():
    async def body():
        detector = SmartTurnDetector()
        stream = detector.stream()
        # AgentActivity/AudioRecognition route on these runtime isinstance checks.
        assert isinstance(detector, _StreamingTurnDetector)
        assert isinstance(stream, _StreamingTurnDetectorStream)
        await stream.aclose()

    _run(body())


@pytest.mark.parametrize("rate", [8000, 16000, 48000])
def test_push_audio_resamples_to_16k(rate):
    async def body():
        stream = SmartTurnDetector().stream()
        for _ in range(10):
            stream.push_audio(_frame(0.1, rate))
        got = stream._len
        # QUICK resampler holds back a few ms of latency until flushed.
        assert abs(got - SAMPLE_RATE) <= 0.02 * SAMPLE_RATE
        await stream.aclose()

    _run(body())


def test_format_change_mid_stream_is_dropped():
    async def body():
        stream = SmartTurnDetector().stream()
        stream.push_audio(_frame(0.5, 16000))
        before = stream._len
        stream.push_audio(_frame(0.5, 8000))
        assert stream._len == before
        await stream.aclose()

    _run(body())


def test_buffer_keeps_only_the_model_window_plus_margin():
    async def body():
        stream = SmartTurnDetector().stream()
        for _ in range(12):
            stream.push_audio(_frame(1.0, 16000))
        assert stream._len == stream._capacity
        assert len(stream._turn_audio()) == int(smart_turn.MAX_SECONDS * SAMPLE_RATE)
        await stream.aclose()

    _run(body())


def test_turn_audio_starts_at_speech_onset_minus_margin():
    async def body():
        detector = SmartTurnDetector()
        stream = detector.stream()
        stream.push_audio(_frame(3.0, 16000, value=0))  # silence before the turn
        detector.on_user_speech_started()
        stream.push_audio(_frame(1.0, 16000))
        # A later onset in the same turn (caller resumed) must not move the start.
        detector.on_user_speech_started()
        stream.push_audio(_frame(1.0, 16000))
        expected = int((2.0 + smart_turn.PRE_SPEECH_SECONDS) * SAMPLE_RATE)
        assert len(stream._turn_audio()) == expected
        await stream.aclose()

    _run(body())


def test_flush_empties_buffer_and_resets_turn_start():
    async def body():
        detector = SmartTurnDetector()
        stream = detector.stream()
        detector.on_user_speech_started()
        stream.push_audio(_frame(1.0, 16000))
        stream.flush(reason="turn committed")
        assert stream._len == 0
        assert stream._turn_start is None
        await stream.aclose()

    _run(body())


def test_predict_resolves_with_probability(monkeypatch):
    seen = []

    async def body():
        detector = SmartTurnDetector(on_prediction=seen.append)
        stream = detector.stream()
        stream.push_audio(_frame(1.0, 16000))
        event = await asyncio.wait_for(stream.predict(), timeout=10)
        assert 0.0 <= event.end_of_turn_probability <= 1.0
        assert event.inference_duration is not None
        await stream.aclose()

    _run(body())
    assert len(seen) == 1 and 0.0 <= seen[0]["probability"] <= 1.0


def test_cancel_resolves_pending_with_zero(monkeypatch):
    monkeypatch.setattr(smart_turn, "predict_probability", lambda audio: (_sleep(0.3), 0.9)[1])

    async def body():
        stream = SmartTurnDetector().stream()
        stream.push_audio(_frame(0.5, 16000))
        fut = stream.predict()
        stream.cancel_inference()
        event = await fut
        assert event.end_of_turn_probability == 0.0
        await stream.aclose()

    _run(body())


def test_superseded_prediction_does_not_resolve_new_future(monkeypatch):
    calls = []

    def fake(audio):
        calls.append(len(audio))
        _sleep(0.2 if len(calls) == 1 else 0.0)
        return 0.1 if len(calls) == 1 else 0.8

    monkeypatch.setattr(smart_turn, "predict_probability", fake)

    async def body():
        stream = SmartTurnDetector().stream()
        stream.push_audio(_frame(0.5, 16000))
        first = stream.predict()
        second = stream.predict()
        assert (await first).end_of_turn_probability == 0.0  # superseded -> cancelled
        assert (await second).end_of_turn_probability == 0.8
        await asyncio.sleep(0.3)  # let the stale first inference finish
        assert (await second).end_of_turn_probability == 0.8
        await stream.aclose()

    _run(body())


def test_inference_failure_resolves_as_complete(monkeypatch):
    def boom(audio):
        raise RuntimeError("onnx failed")

    monkeypatch.setattr(smart_turn, "predict_probability", boom)

    async def body():
        stream = SmartTurnDetector().stream()
        stream.push_audio(_frame(0.5, 16000))
        event = await stream.predict()
        assert event.end_of_turn_probability == 1.0
        await stream.aclose()

    _run(body())


def test_parse_thresholds():
    assert parse_thresholds(None) == (0.5, {})
    assert parse_thresholds("0.4") == (0.4, {})
    assert parse_thresholds("hi=0.45,en=0.5") == (0.5, {"hi": 0.45, "en": 0.5})
    assert parse_thresholds("0.6, hi=0.45") == (0.6, {"hi": 0.45})


def test_threshold_and_language_lookup():
    async def body():
        detector = SmartTurnDetector(threshold="0.5,hi=0.4")
        assert await detector.unlikely_threshold("hi") == 0.4
        assert await detector.unlikely_threshold("hi-IN") == 0.4
        assert await detector.unlikely_threshold("en") == 0.5
        assert await detector.unlikely_threshold(None) == 0.5
        assert await detector.supports_language("hi")
        assert await detector.supports_language(None)
        assert not await detector.supports_language("fr")

    _run(body())


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)
