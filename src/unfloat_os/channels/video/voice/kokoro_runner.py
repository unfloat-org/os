"""Run by `uvx` inside the Kokoro environment (mlx-audio, misaki). Never imported by `uo`.

    python kokoro_runner.py --request request.json --out folder

`request.json`: {"model", "voice", "lang_code", "speed", "texts": [...]}. Loads the model once, writes
`folder/<index>.wav` (24 kHz mono 16-bit) for each text, prints one JSON line of timings on stdout. A failure
prints one line starting with `error:` on stderr and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import wave


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    with open(args.request, encoding="utf-8") as handle:
        request = json.load(handle)
    try:
        import mlx.core as mx
        import numpy as np
        from mlx_audio.tts.utils import load_model

        started = time.perf_counter()
        model = load_model(request["model"])
        loaded = time.perf_counter()
        rate = 24000
        seconds = 0.0
        for index, text in enumerate(request["texts"]):
            chunks = []
            for result in model.generate(
                text=text, voice=request["voice"], speed=request["speed"], lang_code=request["lang_code"]
            ):
                rate = result.sample_rate
                chunks.append(np.array(result.audio, dtype=np.float32).reshape(-1))
            if not chunks:
                raise RuntimeError(f"no audio for sentence {index + 1}")
            samples = np.clip(np.concatenate(chunks), -1.0, 1.0)
            pcm = (samples * 32767).astype("<i2")
            with wave.open(f"{args.out}/{index}.wav", "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(pcm.tobytes())
            seconds += len(pcm) / rate
        done = time.perf_counter()
        print(
            json.dumps(
                {
                    "sentences": len(request["texts"]),
                    "audio_seconds": round(seconds, 3),
                    "load_seconds": round(loaded - started, 3),
                    "synthesis_seconds": round(done - loaded, 3),
                    "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 3),
                }
            )
        )
    except Exception as exc:  # one line for the parent to show
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
