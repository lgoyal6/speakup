"""Evaluate a frozen, hash-bound human-speech manifest through the real worker.

The manifest is prepared before inference. Audio, publisher references and speaker
IDs stay outside the repository. Only loopback providers are accepted.
"""
import argparse
import hashlib
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from speakup.domain import RecordingState  # noqa: E402
from speakup.metrics import corpus_wer, normalize_words  # noqa: E402
from speakup.processor import process_next  # noqa: E402
from speakup.provider import WhisperHTTPProvider  # noqa: E402
from speakup.store import Store  # noqa: E402


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    rows = manifest["utterances"]
    if not rows or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("manifest needs nonempty, unique utterance IDs")
    for row in rows:
        if (not row["speaker_id"] or not normalize_words(row["reference"])
                or not math.isfinite(row["duration_s"]) or row["duration_s"] <= 0):
            raise ValueError("manifest needs speakers, nonempty references and positive durations")
        if digest(row["audio_path"]) != row["audio_sha256"]:
            raise ValueError("manifest audio hash mismatch")
    return manifest


def wer(rows):
    return corpus_wer((row["reference"], row["hypothesis"]) for row in rows)["wer"]


def speaker_interval(left, right=None, draws=2000):
    """Percentile bootstrap of pooled WER, paired by speaker for a difference."""
    groups = {}
    for row in left:
        groups.setdefault(row["speaker_id"], []).append(row)
    speakers = sorted(groups)
    if len(speakers) < 2:
        raise ValueError("at least two speakers required for uncertainty")
    other = {row["id"]: row for row in right} if right is not None else None
    totals = {}
    for speaker, rows in groups.items():
        left_total = corpus_wer((r["reference"], r["hypothesis"]) for r in rows)
        right_total = (corpus_wer((other[r["id"]]["reference"], other[r["id"]]["hypothesis"]) for r in rows)
                       if other is not None else {"edits": 0})
        totals[speaker] = (left_total["edits"], left_total["reference_words"], right_total["edits"])
    rng = random.Random(20261031)
    samples = []
    for _ in range(draws):
        selected = [totals[rng.choice(speakers)] for _ in speakers]
        value = (sum(r[0] for r in selected) - sum(r[2] for r in selected)) / sum(r[1] for r in selected)
        samples.append(value)
    samples.sort()
    return [samples[int((draws - 1) * p)] for p in (0.025, 0.975)]


def compare(left, right):
    if not left or len({row["id"] for row in left}) != len(left):
        raise ValueError("left arm needs unique utterances")
    by_id = {row["id"]: row for row in right}
    if len(by_id) != len(right) or set(by_id) != {row["id"] for row in left}:
        raise ValueError("arms must have the same unique utterances")
    for row in left:
        other = by_id[row["id"]]
        for key in ("speaker_id", "reference", "audio_sha256", "duration_s"):
            if row[key] != other[key]:
                raise ValueError("arms disagree on frozen input")
        if row["job_status"] != "SUCCEEDED" or other["job_status"] != "SUCCEEDED":
            raise ValueError("incomplete jobs cannot enter an accuracy comparison")
    return {"utterances": len(left), "speakers": len({r["speaker_id"] for r in left}),
            "wer_left_minus_right": wer(left) - wer(right),
            "speaker_bootstrap_95_percentile": speaker_interval(left, right),
            "bootstrap_draws": 2000, "seed": 20261031}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def run(manifest_path, endpoint, model, out):
    if urlparse(endpoint).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("human speech study requires a loopback provider")
    manifest = load_manifest(manifest_path)
    # Validate all inputs before creating a result directory or making a request.
    if len({r["speaker_id"] for r in manifest["utterances"]}) < 2:
        raise ValueError("study needs at least two speakers")
    provider = WhisperHTTPProvider(endpoint, "local-no-auth", model, timeout=120)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    write_json(out / "manifest.json", manifest)
    write_json(out / "run.json", {"manifest_sha256": digest(manifest_path),
                                "runner_sha256": digest(__file__), "model_label": model,
                                "endpoint": endpoint})
    store = Store(str(out / "worker.db"))
    rows = []
    try:
        with (out / "rows.jsonl").open("x") as raw:
            for item in manifest["utterances"]:
                rid = store.create_recording("human-study", item["audio_path"], item["audio_sha256"])["id"]
                for state in (RecordingState.LOCAL_READY, RecordingState.UPLOADING, RecordingState.UPLOADED):
                    store.transition("human-study", rid, state)
                store.enqueue_job("human-study", rid)
                start = time.perf_counter()
                job = process_next(store, provider)
                elapsed = time.perf_counter() - start
                transcript = store.get_transcript("human-study", rid) or {}
                row = {**item, "hypothesis": transcript.get("text", ""),
                       "job_status": job["status"] if job else None,
                       "provider": transcript.get("source"), "model": transcript.get("model_version"),
                       "latency_s": elapsed, "real_time_factor": elapsed / item["duration_s"]}
                rows.append(row)
                raw.write(json.dumps(row, allow_nan=False) + "\n")
                raw.flush()
                print(f"{model}: {len(rows)}/{len(manifest['utterances'])} {row['job_status']}", flush=True)
    finally:
        store.db.close()
    succeeded = sum(row["job_status"] == "SUCCEEDED" for row in rows)
    summary = {"utterances": len(rows), "jobs_succeeded": succeeded,
               "speakers": len({row["speaker_id"] for row in rows}),
               "audio_s": sum(row["duration_s"] for row in rows),
               "latency_s_p50": statistics.median(row["latency_s"] for row in rows),
               "pooled_real_time_factor": sum(row["latency_s"] for row in rows) / sum(row["duration_s"] for row in rows)}
    if succeeded == len(rows):
        summary.update(overall=corpus_wer((r["reference"], r["hypothesis"]) for r in rows),
                       speaker_bootstrap_95_percentile=speaker_interval(rows))
    write_json(out / "summary.json", summary)
    return 0 if succeeded == len(rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    study = sub.add_parser("run")
    for flag in ("manifest", "endpoint", "model-label", "out"):
        study.add_argument("--" + flag, required=True)
    paired = sub.add_parser("compare")
    for flag in ("left", "right", "out"):
        paired.add_argument("--" + flag, required=True)
    args = parser.parse_args()
    if args.command == "run":
        return run(args.manifest, args.endpoint, args.model_label, args.out)
    rows = lambda path: [json.loads(line) for line in Path(path).read_text().splitlines()]
    result = compare(rows(args.left), rows(args.right))
    with Path(args.out).open("x") as output:
        output.write(json.dumps({**result, "left_sha256": digest(args.left),
                                 "right_sha256": digest(args.right)}, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
