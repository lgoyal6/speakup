"""Prepare a deterministic small LibriSpeech test-clean diagnostic, before inference.

Requires pyarrow and ffmpeg. Inputs and generated audio belong outside the repo.
"""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

REVISION = "71cacbfb7e2354c4226d01e70d77d5fca3d04ba1"
PARQUET_SHA256 = "7113aa4c3cf963fb54697145719a7725f984c8836d1c494a554cbb9f1a017df0"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(parquet, output):
    import pyarrow.parquet as pq

    if sha(parquet) != PARQUET_SHA256:
        raise ValueError("expected the pinned openslr clean/test parquet")
    groups = {}
    for row in pq.read_table(parquet).to_pylist():
        audio = row["audio"]["bytes"]
        # FLAC STREAMINFO: sample rate is the upper 20 bits, sample count the lower 36.
        if audio[:4] != b"fLaC" or audio[4] & 127 != 0 or len(audio) < 42:
            raise ValueError("expected FLAC with leading STREAMINFO")
        packed = int.from_bytes(audio[18:26], "big")
        duration = (packed & ((1 << 36) - 1)) / (packed >> 44)
        if 3 <= duration <= 12:
            row["duration_s"] = duration
            groups.setdefault(row["speaker_id"], []).append(row)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    utterances = []
    for speaker, candidates in sorted(groups.items()):
        candidates.sort(key=lambda r: hashlib.sha256(("speakup-human-v1:" + r["id"]).encode()).hexdigest())
        for row in candidates[:2]:
            flac = output / (row["id"] + ".flac")
            flac.write_bytes(row["audio"]["bytes"])
            m4a = flac.with_suffix(".m4a")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(flac), "-c:a", "aac", "-b:a", "64k", str(m4a)], check=True)
            utterances.append({"id": row["id"], "speaker_id": str(speaker), "reference": row["text"],
                               "duration_s": row["duration_s"], "audio_path": str(m4a.resolve()),
                               "audio_sha256": sha(m4a), "original_flac_sha256": sha(flac)})
    manifest = {"dataset": "openslr/librispeech_asr", "revision": REVISION, "split": "clean/test",
                "parquet_sha256": PARQUET_SHA256, "license": "CC-BY-4.0",
                "selection": "All speakers with eligible clips; two smallest SHA256(speakup-human-v1:ID) per speaker among 3..12 second clips, independent of text and outputs",
                "utterances": utterances}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    prepare(args.parquet, args.out)
