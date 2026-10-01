"""Score WAV files with Smart Turn: P(turn complete) per file.

    python eval_turns.py clip1.wav clip2.wav ...
    python eval_turns.py --cut 0.6 --phone clip.wav

--cut SECONDS  also score each clip with its last SECONDS removed (a
               mid-sentence cut, which should score as incomplete).
--phone        also score an 8kHz round-trip copy (what a phone call sounds like).

Each clip is scored the way the live detector scores a turn: whole clip, last
8s kept, zero-padded at the front.
"""

from __future__ import annotations

import argparse
import time
import wave

import numpy as np
from livekit import rtc

from smart_turn import SAMPLE_RATE, DEFAULT_THRESHOLD, predict_probability, warm_model


def _read_wav(path: str) -> tuple[np.ndarray, int]:
    with wave.open(path, "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{path}: only 16-bit PCM WAV is supported (got {width * 8}-bit)")
    pcm = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
    return pcm, rate


def _resample(pcm: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return pcm
    resampler = rtc.AudioResampler(input_rate=src, output_rate=dst, num_channels=1)
    frames = resampler.push(rtc.AudioFrame(pcm.tobytes(), src, 1, len(pcm)))
    frames += resampler.flush()
    return np.concatenate([np.frombuffer(f.data, dtype=np.int16) for f in frames])


def _score(label: str, pcm16k: np.ndarray, threshold: float) -> None:
    audio = pcm16k.astype(np.float32) / 32768.0
    started = time.perf_counter()
    p = predict_probability(audio)
    ms = (time.perf_counter() - started) * 1000
    verdict = "complete" if p >= threshold else "incomplete"
    print(f"{label:<60} p={p:.3f} {verdict:<10} {len(audio) / SAMPLE_RATE:5.2f}s {ms:5.0f}ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("wavs", nargs="+")
    parser.add_argument("--cut", type=float, default=0.0)
    parser.add_argument("--phone", action="store_true")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args()

    warm_model()
    for path in args.wavs:
        pcm, rate = _read_wav(path)
        name = path.rsplit("/", 1)[-1]
        variants = [("", _resample(pcm, rate, SAMPLE_RATE))]
        if args.phone:
            variants.append((" [8kHz]", _resample(_resample(pcm, rate, 8000), 8000, SAMPLE_RATE)))
        for suffix, audio in variants:
            _score(f"{name}{suffix}", audio, args.threshold)
            if args.cut > 0:
                cut = audio[: max(0, len(audio) - int(args.cut * SAMPLE_RATE))]
                _score(f"{name}{suffix} [cut -{args.cut:.1f}s]", cut, args.threshold)


if __name__ == "__main__":
    main()
