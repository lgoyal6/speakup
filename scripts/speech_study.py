"""Locked synthetic-speech WER study through the real SpeakUp worker path.

Protocol (fixed before any result was seen):
  speakers  four macOS TTS voices (synthetic, not human speech)
  sentences eight reference sentences written for this repo
  noise     clean, then seeded white noise at 20, 10 and 5 dB SNR
  audio     16 kHz mono PCM from `say`, noise mixed in Python, AAC/M4A via ffmpeg
            (the format the Android recorder uploads)
  path      Store -> enqueue_job -> process_next(WhisperHTTPProvider) -> published
            transcript, so the real lease/hash/publish path is exercised
  metric    pooled WER (speakup.metrics.corpus_wer) by voice, by SNR, overall
No configuration is tuned on these results. The endpoint must be loopback unless
--allow-remote is passed, so a hosted (paid) provider is never called by accident.
"""
import argparse
import array
import hashlib
import json
import math
import random
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from speakup.domain import RecordingState  # noqa: E402
from speakup.metrics import corpus_wer, word_edits  # noqa: E402
from speakup.processor import process_next  # noqa: E402
from speakup.provider import WhisperHTTPProvider  # noqa: E402
from speakup.store import Store  # noqa: E402

VOICES = ("Daniel", "Eddy (English (US))", "Flo (English (UK))", "Fred")
SNRS = (None, 20, 10, 5)
SENTENCES = (
    "please move the team standup to thursday at ten thirty",
    "the delivery truck left the warehouse before the storm arrived",
    "remind me to call the pharmacy about my refill tomorrow morning",
    "our quarterly budget review found three duplicate invoices",
    "turn left after the second bridge and park near the library",
    "she recorded the lecture offline and uploaded it later that night",
    "the battery dropped to twelve percent during the long flight",
    "send the final draft to the editor once the figures are checked",
)


def synth(voice, text, out: Path):
    subprocess.run(["say", "-v", voice, "--data-format=LEI16@16000", "-o", str(out), text], check=True)


def mix_noise(src: Path, dst: Path, snr_db, seed):
    with wave.open(str(src)) as w:
        params, frames = w.getparams(), w.readframes(w.getnframes())
    samples = array.array("h", frames)
    if snr_db is not None:
        power = sum(s * s for s in samples) / max(1, len(samples))
        sigma = math.sqrt(power / (10 ** (snr_db / 10)))
        rng = random.Random(seed)
        samples = array.array("h", (max(-32768, min(32767, int(s + rng.gauss(0, sigma)))) for s in samples))
    with wave.open(str(dst), "wb") as w:
        w.setparams(params)
        w.writeframes(samples.tobytes())


def to_m4a(src: Path, dst: Path):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-c:a", "aac", "-b:a", "64k", str(dst)], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model-label", required=True)
    parser.add_argument("--raw-out", required=True)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args(argv)
    if urlparse(args.endpoint).hostname not in {"127.0.0.1", "localhost", "::1"} and not args.allow_remote:
        raise SystemExit("refusing a non-loopback endpoint without --allow-remote")
    provider = WhisperHTTPProvider(args.endpoint, "local-no-auth", args.model_label, timeout=120)
    rows = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        store = Store(str(root / "study.db"))
        for v, voice in enumerate(VOICES):
            for s, sentence in enumerate(SENTENCES):
                clean = root / f"v{v}s{s}.wav"
                synth(voice, sentence, clean)
                for snr in SNRS:
                    noisy, m4a = root / f"v{v}s{s}n{snr}.wav", root / f"v{v}s{s}n{snr}.m4a"
                    mix_noise(clean, noisy, snr, seed=v * 1000 + s * 10 + (snr or 0))
                    to_m4a(noisy, m4a)
                    audio = m4a.read_bytes()
                    sha = hashlib.sha256(audio).hexdigest()
                    rid = store.create_recording("study", str(m4a), sha)["id"]
                    for state in (RecordingState.LOCAL_READY, RecordingState.UPLOADING, RecordingState.UPLOADED):
                        store.transition("study", rid, state)
                    store.enqueue_job("study", rid)
                    started = time.perf_counter()
                    job = process_next(store, provider)
                    latency = time.perf_counter() - started
                    transcript = store.get_transcript("study", rid) or {}
                    hypothesis = transcript.get("text", "")
                    rows.append({"voice": voice, "sentence_id": s, "snr_db": snr, "reference": sentence, "hypothesis": hypothesis,
                                 "job_status": job["status"] if job else None, "provider": transcript.get("source"),
                                 "model": transcript.get("model_version"), "audio_sha256": sha, "audio_bytes": len(audio),
                                 "latency_s": round(latency, 3), **word_edits(sentence, hypothesis)})
        store.db.close()
    Path(args.raw_out).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def group(key):
        return {str(k): corpus_wer([(r["reference"], r["hypothesis"]) for r in rows if r[key] == k])
                for k in dict.fromkeys(r[key] for r in rows)}
    latencies = sorted(r["latency_s"] for r in rows)
    print(json.dumps({"utterances": len(rows), "jobs_succeeded": sum(r["job_status"] == "SUCCEEDED" for r in rows),
                      "overall": corpus_wer([(r["reference"], r["hypothesis"]) for r in rows]),
                      "by_voice": group("voice"), "by_snr_db": group("snr_db"),
                      "latency_s_p50": latencies[len(latencies) // 2], "latency_s_max": latencies[-1]}, indent=1))


if __name__ == "__main__":
    main()
