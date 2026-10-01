"""Smart Turn v3.2 as LiveKit's end-of-turn decision maker.

VAD is only the trigger: once it has seen 200ms of silence
(audio_recognition.py, MIN_SILENCE_DURATION_MS) the framework calls
predict() on this stream. Smart Turn scores the audio of the current
utterance and returns P(turn complete). The framework then compares that
against unlikely_threshold():

  P >= threshold -> endpointing min_delay (short wait, just enough for the
                    Deepgram final) -> commit.
  P <  threshold -> endpointing max_delay (the "incomplete" ceiling). If the
                    caller resumes inside it, the pending commit is cancelled
                    and the transcript keeps growing; if they stay silent the
                    turn is committed anyway, so a wrong "incomplete" costs a
                    pause, never dead air.

This plugs into the same _StreamingTurnDetector slot LiveKit's own
turn-detector-v1-mini uses, so VAD triggering, cancellation on resumed
speech, the prediction timeout and the flush on commit all come from the
framework. The protocol is underscore-private (livekit-agents 1.6.10,
voice/turn.py) — re-check it when upgrading.

Scoring matches pipecat's LocalSmartTurnAnalyzerV3/BaseSmartTurn: audio from
the start of the current turn's first speech (minus a pre-speech margin) up
to now, last 8s kept, zero-padded at the front, Whisper log-mel, sigmoid
output. The turn start comes from the session's user_state_changed event
(see attach_smart_turn) — without it the whole buffer since the last commit
is scored instead.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import weakref
from pathlib import Path
from typing import Callable

import numpy as np
import onnxruntime as ort
from livekit import rtc
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.voice.turn import TurnDetectionEvent

from _whisper_features import compute_whisper_log_mel_features
from app_logger import applog

MODEL_PATH = Path(__file__).parent / "models" / "smart-turn-v3.2-cpu.onnx"
MODEL_NAME = "smart-turn-v3.2"
SAMPLE_RATE = 16000
# The model's input window; anything older is dropped.
MAX_SECONDS = 8.0
# Audio kept before the turn's first speech onset. pipecat uses 500ms plus the
# VAD's own start latency; the onset we get from user_state_changed already
# lags the real onset by about min_speech_duration, so 0.5 + that is covered
# by PRE_SPEECH_SECONDS.
PRE_SPEECH_SECONDS = 0.7
DEFAULT_THRESHOLD = 0.5
# How long the framework waits for a prediction before committing without
# one (it then uses min_delay, i.e. treats the turn as complete).
PREDICTION_TIMEOUT = 0.35
_SUPPORTED_LANGUAGES = frozenset({"hi", "en"})

_session: ort.InferenceSession | None = None
_session_lock = threading.Lock()


def warm_model() -> ort.InferenceSession:
    """Load the ONNX session once per process (called from _prewarm)."""
    global _session
    with _session_lock:
        if _session is None:
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.intra_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _session = ort.InferenceSession(str(MODEL_PATH), sess_options=so)
        return _session


def predict_probability(audio: np.ndarray) -> float:
    """P(turn complete) for float32 16kHz mono audio in [-1, 1]."""
    session = warm_model()
    max_samples = int(MAX_SECONDS * SAMPLE_RATE)
    if len(audio) > max_samples:
        audio = audio[-max_samples:]
    elif len(audio) < max_samples:
        audio = np.pad(audio, (max_samples - len(audio), 0))
    features = compute_whisper_log_mel_features(audio, do_normalize=True)
    outputs = session.run(None, {"input_features": features[np.newaxis, ...]})
    return float(np.asarray(outputs[0]).reshape(-1)[0])


def parse_thresholds(raw: str | None) -> tuple[float, dict[str, float]]:
    """SMART_TURN_THRESHOLD: "0.5" or "hi=0.45,en=0.5" (optionally with a
    bare default among them, e.g. "0.5,hi=0.45")."""
    default = DEFAULT_THRESHOLD
    per_language: dict[str, float] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            lang, value = part.split("=", 1)
            per_language[lang.strip().lower()] = float(value)
        else:
            default = float(part)
    return default, per_language


def _language_key(language: object) -> str | None:
    if language is None:
        return None
    base = getattr(language, "language", None)
    return str(base if base is not None else language).split("-")[0].lower() or None


class SmartTurnDetector:
    """Satisfies livekit.agents.voice.turn._StreamingTurnDetector."""

    def __init__(
        self,
        *,
        threshold: str | None = None,
        on_prediction: Callable[[dict], None] | None = None,
        session_label: str = "session",
    ) -> None:
        self._default_threshold, self._thresholds = parse_thresholds(
            threshold if threshold is not None else os.environ.get("SMART_TURN_THRESHOLD")
        )
        self.on_prediction = on_prediction
        self.session_label = session_label
        self._latest_stream_ref: weakref.ReferenceType[SmartTurnStream] | None = None

    @property
    def model(self) -> str:
        return MODEL_NAME

    @property
    def provider(self) -> str:
        return "local"

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "SmartTurnStream":
        stream = SmartTurnStream(detector=self)
        self._latest_stream_ref = weakref.ref(stream)
        return stream

    def threshold_for(self, language: object) -> float:
        key = _language_key(language)
        return self._thresholds.get(key, self._default_threshold) if key else self._default_threshold

    async def unlikely_threshold(self, language: object) -> float | None:
        return self.threshold_for(language)

    async def backchannel_threshold(self, language: object) -> float | None:
        return None

    async def supports_language(self, language: object) -> bool:
        key = _language_key(language)
        return key is None or key in _SUPPORTED_LANGUAGES

    def on_user_speech_started(self) -> None:
        stream = self._latest_stream_ref() if self._latest_stream_ref is not None else None
        if stream is not None:
            stream.mark_speech_start()


class SmartTurnStream:
    """Satisfies livekit.agents.voice.turn._StreamingTurnDetectorStream."""

    def __init__(self, *, detector: SmartTurnDetector) -> None:
        self._detector = detector
        self._max_samples = int(MAX_SECONDS * SAMPLE_RATE)
        # Kept a little longer than the model window so the pre-speech margin
        # is still there when the turn itself is close to 8s long.
        self._capacity = self._max_samples + int(PRE_SPEECH_SECONDS * SAMPLE_RATE)
        self._buf = np.zeros(self._capacity, dtype=np.float32)
        self._len = 0
        # Samples ever written (monotonic), so a turn start survives the
        # ring dropping old audio.
        self._written = 0
        self._turn_start: int | None = None
        self._input_rate: int | None = None
        self._input_channels: int | None = None
        self._resampler: rtc.AudioResampler | None = None
        self._closed = False
        self._request_id = 0
        self._request_fut: asyncio.Future[TurnDetectionEvent] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    # region: protocol properties

    @property
    def model(self) -> str:
        return MODEL_NAME

    @property
    def provider(self) -> str:
        return "local"

    @property
    def is_fallback(self) -> bool:
        return False

    @property
    def prediction_timeout(self) -> float:
        return PREDICTION_TIMEOUT

    async def unlikely_threshold(self, language: object) -> float | None:
        return await self._detector.unlikely_threshold(language)

    async def backchannel_threshold(self, language: object) -> float | None:
        return None

    async def supports_language(self, language: object) -> bool:
        return await self._detector.supports_language(language)

    # endregion

    # region: audio

    def push_audio(self, frame: rtc.AudioFrame) -> None:
        if self._closed:
            return
        if self._input_rate is None:
            self._input_rate = frame.sample_rate
            self._input_channels = frame.num_channels
            if frame.sample_rate != SAMPLE_RATE:
                self._resampler = rtc.AudioResampler(
                    input_rate=frame.sample_rate,
                    output_rate=SAMPLE_RATE,
                    num_channels=frame.num_channels,
                    quality=rtc.AudioResamplerQuality.QUICK,
                )
        elif frame.sample_rate != self._input_rate or frame.num_channels != self._input_channels:
            applog.error(
                f"[SMART TURN][{self._detector.session_label}] audio format changed "
                f"{self._input_rate}Hz/{self._input_channels}ch -> "
                f"{frame.sample_rate}Hz/{frame.num_channels}ch, frame dropped"
            )
            return
        frames = self._resampler.push(frame) if self._resampler is not None else [frame]
        for out in frames:
            self._append(out)

    def _append(self, frame: rtc.AudioFrame) -> None:
        pcm = np.frombuffer(frame.data, dtype=np.int16)
        if frame.num_channels > 1:
            pcm = pcm.reshape(-1, frame.num_channels).mean(axis=1)
        samples = pcm.astype(np.float32) / 32768.0
        n = len(samples)
        if n == 0:
            return
        self._written += n
        if n >= self._capacity:
            self._buf[:] = samples[-self._capacity :]
            self._len = self._capacity
            return
        overflow = self._len + n - self._capacity
        if overflow > 0:
            self._buf[: self._len - overflow] = self._buf[overflow : self._len]
            self._len -= overflow
        self._buf[self._len : self._len + n] = samples
        self._len += n

    def mark_speech_start(self) -> None:
        """Start of the current turn's audio. Later onsets inside the same
        turn (the caller resuming after an "incomplete" pause) are ignored,
        so the model keeps seeing the whole turn, as in pipecat."""
        if self._turn_start is None:
            self._turn_start = self._written

    def _turn_audio(self) -> np.ndarray:
        audio = self._buf[: self._len]
        if self._turn_start is not None:
            start = self._turn_start - int(PRE_SPEECH_SECONDS * SAMPLE_RATE)
            oldest = self._written - self._len
            audio = audio[max(0, start - oldest) :]
        return audio[-self._max_samples :].copy()

    def flush(self, reason: str | None = None) -> None:
        """Turn committed / cleared: the next turn is scored on its own audio."""
        if self._resampler is not None:
            for out in self._resampler.flush():
                self._append(out)
            self._resampler = None
            self._input_rate = None
            self._input_channels = None
        self._len = 0
        self._turn_start = None
        self.cancel_inference()

    # endregion

    # region: inference

    def predict(self) -> asyncio.Future[TurnDetectionEvent]:
        loop = asyncio.get_running_loop()
        self.cancel_inference()  # supersede any previous request
        fut: asyncio.Future[TurnDetectionEvent] = loop.create_future()
        if self._closed:
            fut.set_result(_event(1.0))
            return fut
        self._request_id += 1
        self._request_fut = fut
        audio = self._turn_audio()
        # Monotonic, same clock as turn_logger, so the logger can place the
        # request and the verdict on the speech-end -> commit timeline.
        requested_at = time.monotonic()
        task = asyncio.create_task(self._run_inference(self._request_id, audio, requested_at))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return fut

    async def _run_inference(
        self, request_id: int, audio: np.ndarray, requested_at: float
    ) -> None:
        started = time.perf_counter()
        probability = 0.0
        failed = False
        try:
            probability = await asyncio.to_thread(predict_probability, audio)
        except Exception:
            failed = True
            applog.exception(f"[SMART TURN][{self._detector.session_label}] inference failed")
        inference_duration = time.perf_counter() - started
        if request_id != self._request_id or self._request_fut is None:
            return  # superseded or cancelled; the future was already resolved
        fut, self._request_fut = self._request_fut, None
        if failed:
            # No verdict: resolve as complete so the framework takes the short
            # path instead of leaving the caller waiting on the ceiling.
            probability = 1.0
        if not fut.done():
            fut.set_result(_event(probability, inference_duration=inference_duration))
        threshold = self._detector._default_threshold
        info = {
            "probability": probability,
            "threshold": threshold,
            "complete": probability >= threshold,
            "inference_ms": inference_duration * 1000,
            "audio_seconds": len(audio) / SAMPLE_RATE,
            "failed": failed,
            "t_requested": requested_at,
            "t_verdict": time.monotonic(),
        }
        applog.info(
            f"[SMART TURN][{self._detector.session_label}] p={probability:.3f} "
            f"threshold={threshold:.2f} verdict={'complete' if info['complete'] else 'incomplete'} "
            f"inference={info['inference_ms']:.0f}ms audio={info['audio_seconds']:.2f}s"
            f"{' (inference failed)' if failed else ''}"
        )
        if self._detector.on_prediction is not None:
            try:
                self._detector.on_prediction(info)
            except Exception:
                applog.exception(f"[SMART TURN][{self._detector.session_label}] on_prediction failed")

    def cancel_inference(self, *, timed_out: bool = False) -> None:
        fut, self._request_fut = self._request_fut, None
        self._request_id += 1  # any in-flight result is now stale
        if fut is not None and not fut.done():
            fut.set_result(_event(0.0))
        if timed_out:
            applog.warning(
                f"[SMART TURN][{self._detector.session_label}] prediction timed out "
                f"after {PREDICTION_TIMEOUT:.2f}s, committed without a verdict"
            )

    # endregion

    # region: teardown

    def end_input(self) -> None:
        self.flush()
        self._closed = True

    async def aclose(self) -> None:
        self.end_input()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # endregion


def _event(probability: float, *, inference_duration: float | None = None) -> TurnDetectionEvent:
    return TurnDetectionEvent(
        type="eot_prediction",
        last_speaking_time=time.time(),
        end_of_turn_probability=probability,
        inference_duration=inference_duration,
    )


def attach_smart_turn(session, detector: SmartTurnDetector) -> None:
    """Feed the detector each turn's speech onset (see _turn_audio)."""

    @session.on("user_state_changed")
    def _on_user_state(event) -> None:
        if getattr(event, "new_state", None) == "speaking":
            detector.on_user_speech_started()
