"""Rebuild the report from frozen raw rows without rerunning audio/model work."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quantiles(values):
    ordered = sorted(values)
    return {"p50": ordered[int((len(ordered) - 1) * .50)],
            "p95": ordered[int((len(ordered) - 1) * .95)],
            "p99": ordered[int((len(ordered) - 1) * .99)],
            "max": ordered[-1]}


def summarize(manifest_path: Path, rows_path: Path, out: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    if len(rows) != len(manifest["cases"]) or not rows:
        raise ValueError("raw result row count differs from the frozen input manifest")
    frozen_cases = {row["case_id"]: row for row in manifest["cases"]}
    if len(frozen_cases) != len(manifest["cases"]) or {row["case_id"] for row in rows} != set(frozen_cases):
        raise ValueError("raw case IDs differ from the frozen input manifest")
    runner_hashes = {row["runner_sha256"] for row in rows}
    cli_hashes = {row["frame_cli_sha256"] for row in rows}
    if len(runner_hashes) != 1 or len(cli_hashes) != 1:
        raise ValueError("rows do not share one runner and C++ CLI build")
    for row in rows:
        expected = frozen_cases[row["case_id"]]
        for key in ("mixture_sha256", "target_pcm_sha256", "target_flac_sha256", "noise_type", "snr_target_db"):
            if row.get(key) != expected.get(key):
                raise ValueError(f"raw row disagrees with frozen manifest on {row['case_id']}: {key}")
        if row["arms"]["frame_gate"]["samples"] != row["arms"]["passthrough"]["samples"]:
            raise ValueError(f"native frame stream changed sample count for {row['case_id']}")
        if row["arms"]["frame_gate"]["native_frame_stats"]["overruns"] != 0:
            raise ValueError(f"native frame overrun in {row['case_id']}")

    groups = {}
    for kind in manifest["noise_types"]:
        snrs = [None] if kind == "clean" else manifest["snrs_db"]
        for snr in snrs:
            subset = [row for row in rows if row["noise_type"] == kind and row["snr_target_db"] == snr]
            if not subset:
                continue
            key = f"{kind}:{snr if snr is not None else 'clean'}"
            groups[key] = {arm: {metric: statistics.mean(row["arms"][arm][metric] for row in subset)
                for metric in ("snr_db", "si_sdr_db", "speech_rms_ratio_db", "noise_removal_delta_snr_db")}
                for arm in ("passthrough", "afftdn", "frame_gate")}

    frame_metrics = {field: quantiles([row["arms"]["frame_gate"]["native_frame_stats"][field]
                                       for row in rows])
                     for field in ("frame_us_p50", "frame_us_p95", "frame_us_p99", "frame_us_max")}
    host_metrics = {arm: quantiles([row["host_seconds_per_arm"][arm] for row in rows])
                    for arm in ("passthrough", "afftdn", "frame_gate")}
    result = {"protocol_version": manifest["protocol_version"],
        "frozen_manifest_sha256": sha(manifest_path), "raw_rows_sha256": sha(rows_path),
        "executed_runner_sha256": next(iter(runner_hashes)), "compiled_frame_cli_sha256": next(iter(cli_hashes)),
        "dataset_revision": manifest["dataset_revision"], "dataset_file_sha256": manifest["dataset_file_sha256"],
        "target_speaker_ids": manifest["speaker_split"]["target_speakers"],
        "masker_speaker_ids": manifest["speaker_split"]["masker_speakers"],
        "target_utterances": len({row["target_id"] for row in rows}), "mixture_cases": len(rows),
        "case_counts": {kind: sum(row["noise_type"] == kind for row in rows) for kind in manifest["noise_types"]},
        "asr_wer": {"status": "not_run", "reason": "No pre-existing local Whisper model was found; no ASR weights were downloaded."},
        "quality_metrics": {"groups": groups,
            "interpretation": "SI-SDR is scale invariant, so clean/noisy SNR and speech RMS change are also reported. Mixture clip guard is shared across speech, masker and mixture. This audiobook plus synthetic noise study is not field intelligibility evidence."},
        "latency": {"native_per_frame_integer_microseconds_across_utterances": frame_metrics,
            "per_recording_host_processing_seconds": host_metrics,
            "timing_note": "Frame engine uses integer microseconds. Values below one microsecond quantize to 0; host wall processing includes process startup and file decode/encode and is descriptive on this host."},
        "overruns": sum(row["arms"]["frame_gate"]["native_frame_stats"]["overruns"] for row in rows),
        "sample_count_mismatches": 0}
    out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--rows", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.manifest, args.rows, args.out), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
