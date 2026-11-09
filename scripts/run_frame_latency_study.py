"""Measure the frozen SpeakUp native frame engine on all frozen test-other cases."""
import argparse
import array
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepare_noise_mixture_study import file_sha, read_pcm  # noqa: E402


def run(manifest_path: Path, quality_rows_path: Path, cli: Path, out: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    quality = [json.loads(line) for line in quality_rows_path.read_text().splitlines()]
    if len(quality) != len(manifest["cases"]):
        raise ValueError("latency cohort row count differs from frozen manifest")
    if out.exists():
        raise FileExistsError(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("x") as raw:
        for index, case in enumerate(quality, 1):
            source = Path(case["mixture_path"])
            if file_sha(source) != case["mixture_sha256"]:
                raise ValueError(f"mixture hash changed: {case['case_id']}")
            pcm = read_pcm(source)
            if sys.byteorder != "little": pcm.byteswap()
            result = subprocess.run([str(cli), "gate"], input=pcm.tobytes(),
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            stats = json.loads(result.stderr.decode().splitlines()[-1])
            if stats["samples"] != stats["output_samples"] or stats["overruns"] != 0:
                raise ValueError(f"frame engine contract violation: {case['case_id']} {stats}")
            row = {"case_id": case["case_id"], "noise_type": case["noise_type"],
                "snr_target_db": case["snr_target_db"], "input_sha256": case["mixture_sha256"],
                "frames": stats["frames"], "samples": stats["samples"],
                "frame_ns_p50": stats["frame_ns_p50"], "frame_ns_p95": stats["frame_ns_p95"],
                "frame_ns_p99": stats["frame_ns_p99"], "frame_ns_max": stats["frame_ns_max"],
                "chunk_processing_us": stats["chunk_processing_us"], "overruns": stats["overruns"],
                "frame_cli_sha256": file_sha(cli)}
            raw.write(json.dumps(row, allow_nan=False) + "\n")
            raw.flush()
            rows.append(row)
            print(f"{index}/{len(quality)} {case['case_id']}", flush=True)
    fields = ("frame_ns_p50", "frame_ns_p95", "frame_ns_p99", "frame_ns_max", "chunk_processing_us")
    summary = {"cases": len(rows), "frames": sum(row["frames"] for row in rows),
        "samples": sum(row["samples"] for row in rows), "overruns": sum(row["overruns"] for row in rows),
        "frame_cli_sha256": file_sha(cli), "frozen_manifest_sha256": file_sha(manifest_path),
        "quality_rows_sha256": file_sha(quality_rows_path), "raw_rows_sha256": file_sha(out),
        "per_utterance_percentiles": {field: {
            "median": statistics.median(row[field] for row in rows),
            "p95": sorted(row[field] for row in rows)[int((len(rows)-1)*.95)],
            "p99": sorted(row[field] for row in rows)[int((len(rows)-1)*.99)],
            "max": max(row[field] for row in rows)} for field in fields},
        "deadline": {"hop_ns": 10_000_000, "max_observed_frame_ns": max(r["frame_ns_max"] for r in rows),
                     "claim": "host native computation only; Android emulator and physical phone are separately evaluated"}}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--quality-rows", required=True, type=Path)
    parser.add_argument("--frame-cli", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = run(args.manifest, args.quality_rows, args.frame_cli, args.out)
    summary_path = args.out.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
