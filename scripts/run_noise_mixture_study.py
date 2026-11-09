"""Run frozen human/noise mixtures through no-op, FFmpeg afftdn, and SpeakUp gate."""
import argparse
import array
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from prepare_noise_mixture_study import file_sha  # noqa: E402
from speakup.metrics import corpus_wer, normalize_words, word_edits  # noqa: E402


def samples(path: Path) -> list[float]:
    import array
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getframerate() != 16000 or source.getsampwidth() != 2:
            raise ValueError(f"unsupported waveform format: {path}")
        data = array.array("h", source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        data.byteswap()
    return [value / 32768.0 for value in data]


def raw_samples(path: Path) -> list[float]:
    data = array.array("h", path.read_bytes())
    if len(path.read_bytes()) % 2:
        raise ValueError("raw PCM has an incomplete sample")
    if sys.byteorder != "little":
        data.byteswap()
    return [value / 32768.0 for value in data]


def metrics(reference: list[float], degraded: list[float]) -> dict:
    n = min(len(reference), len(degraded))
    if n == 0 or len(reference) != len(degraded):
        raise ValueError(f"sample misalignment: reference={len(reference)} output={len(degraded)}")
    target, output = reference[:n], degraded[:n]
    error = [a - b for a, b in zip(target, output)]
    target_power = sum(x * x for x in target)
    error_power = sum(x * x for x in error)
    alpha = sum(a * b for a, b in zip(target, output)) / max(target_power, 1e-20)
    projected = [alpha * x for x in target]
    residual = [b - p for b, p in zip(output, projected)]
    si_sdr = 10.0 * math.log10(max(sum(x*x for x in projected), 1e-20) /
                               max(sum(x*x for x in residual), 1e-20))
    return {"snr_db": 10.0 * math.log10(max(target_power, 1e-20) / max(error_power, 1e-20)),
            "si_sdr_db": si_sdr,
            "speech_rms_ratio_db": 20.0 * math.log10(max(math.sqrt(target_power/n), 1e-12) /
                                                        max(math.sqrt(sum(x*x for x in output)/n), 1e-12)),
            "samples": n}


def run(manifest_path: Path, cli: Path, out: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if manifest["dataset_file_sha256"] != "38e0c86a8104585c577badd707ca4331e20fa2c645f46180af0b7bcdecff9249":
        raise ValueError("quality runner requires the frozen test-other source hash")
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    runner_sha = file_sha(Path(__file__))
    cli_sha = file_sha(cli)
    rows = []
    host_elapsed = {"passthrough": [], "afftdn": [], "frame_gate": []}
    with tempfile.TemporaryDirectory(prefix="speakup-mixture-") as tmp:
        tmp = Path(tmp)
        for index, case in enumerate(manifest["cases"], start=1):
            mixture_path = Path(case["mixture_path"])
            reference_path = Path(case["target_pcm_path"])
            if file_sha(mixture_path) != case["mixture_sha256"] or file_sha(reference_path) != case["target_pcm_sha256"]:
                raise ValueError(f"frozen input hash mismatch for {case['case_id']}")
            reference = samples(reference_path)
            mixture = samples(mixture_path)
            outputs = {"passthrough": mixture_path}
            details = {}

            started = time.perf_counter()
            nop_path = tmp / "passthrough.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(mixture_path),
                            "-c:a", "pcm_s16le", str(nop_path)], check=True, capture_output=True)
            host_elapsed["passthrough"].append(time.perf_counter() - started)
            outputs["passthrough"] = nop_path

            started = time.perf_counter()
            afftdn_path = tmp / "afftdn.wav"
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(mixture_path),
                            "-af", "afftdn=nr=12:nf=-45:tn=1", "-c:a", "pcm_s16le", str(afftdn_path)],
                           check=True, capture_output=True)
            host_elapsed["afftdn"].append(time.perf_counter() - started)
            outputs["afftdn"] = afftdn_path
            details["afftdn_command"] = "ffmpeg -af afftdn=nr=12:nf=-45:tn=1"

            started = time.perf_counter()
            gate_path = tmp / "frame-gate.pcm"
            raw_pcm = array.array("h", (max(-32768, min(32767, int(round(value * 32768))))
                                       for value in mixture))
            if sys.byteorder != "little":
                raw_pcm.byteswap()
            with gate_path.open("wb") as target:
                subprocess.run([str(cli), "gate"], input=raw_pcm.tobytes(), stdout=target, check=True,
                               stderr=subprocess.PIPE)
            host_elapsed["frame_gate"].append(time.perf_counter() - started)
            # The causal stream's frame latency is emitted by a second pass whose PCM output is discarded.
            timing = subprocess.run([str(cli), "gate"], input=raw_pcm.tobytes(), stdout=subprocess.DEVNULL,
                                    check=True, stderr=subprocess.PIPE, text=False)
            try:
                native_stats = json.loads(timing.stderr.decode().splitlines()[-1])
            except (ValueError, IndexError, UnicodeDecodeError) as error:
                raise RuntimeError(f"native frame CLI omitted timing stats for {case['case_id']}") from error
            if native_stats["samples"] != native_stats["output_samples"] or native_stats["overruns"] != 0:
                raise RuntimeError(f"native engine violated length/overrun contract for {case['case_id']}: {native_stats}")
            outputs["frame_gate"] = gate_path
            details["native_frame_stats"] = native_stats

            row = {**case, "arms": {}, "runner_sha256": runner_sha, "frame_cli_sha256": cli_sha,
                   "host_seconds_per_arm": {key: host_elapsed[key][-1] for key in host_elapsed}}
            for arm, path in outputs.items():
                result = raw_samples(path) if arm == "frame_gate" else samples(path)
                values = metrics(reference, result)
                row["arms"][arm] = {**values, "output_sha256": file_sha(path)}
            row["arms"]["frame_gate"]["native_frame_stats"] = details["native_frame_stats"]
            row["arms"]["passthrough"]["noise_removal_delta_snr_db"] = 0.0
            row["arms"]["afftdn"]["noise_removal_delta_snr_db"] = (
                row["arms"]["afftdn"]["snr_db"] - row["arms"]["passthrough"]["snr_db"])
            row["arms"]["frame_gate"]["noise_removal_delta_snr_db"] = (
                row["arms"]["frame_gate"]["snr_db"] - row["arms"]["passthrough"]["snr_db"])
            if case["noise_type"] == "clean":
                row["clean_speech"] = {
                    arm: {"si_sdr_db": row["arms"][arm]["si_sdr_db"],
                          "snr_db": row["arms"][arm]["snr_db"],
                          "speech_rms_ratio_db": row["arms"][arm]["speech_rms_ratio_db"]}
                    for arm in row["arms"]}
            rows.append(row)
            print(f"{index}/{len(manifest['cases'])} {case['case_id']}", flush=True)

    raw = out / "rows.jsonl"
    raw.write_text("".join(json.dumps(row, allow_nan=False) + "\n" for row in rows))
    groups = {}
    for noise_type in manifest["noise_types"]:
        for snr in ([None] if noise_type == "clean" else manifest["snrs_db"]):
            subset = [row for row in rows if row["noise_type"] == noise_type and row["snr_target_db"] == snr]
            if not subset:
                continue
            key = f"{noise_type}:{snr if snr is not None else 'clean'}"
            groups[key] = {arm: {
                metric: statistics.mean(row["arms"][arm][metric] for row in subset)
                for metric in ("snr_db", "si_sdr_db", "speech_rms_ratio_db", "noise_removal_delta_snr_db")}
                for arm in ("passthrough", "afftdn", "frame_gate")}
    summary = {"protocol_version": manifest["protocol_version"], "manifest_sha256": file_sha(manifest_path),
        "runner_sha256": runner_sha, "frame_cli_sha256": cli_sha,
        "dataset_revision": manifest["dataset_revision"], "dataset_file_sha256": manifest["dataset_file_sha256"],
        "target_speakers": manifest["speaker_split"]["target_speakers"],
        "masker_speakers": manifest["speaker_split"]["masker_speakers"],
        "utterances": len({row["target_id"] for row in rows}), "mixture_cases": len(rows),
        "noise_arm_counts": {kind: sum(row["noise_type"] == kind for row in rows) for kind in manifest["noise_types"]},
        "wer": {"status": "not_run", "reason": "no pre-existing local Whisper model found; no ASR weights downloaded"},
        "metrics_note": "sample-aligned SNR and SI-SDR; SI-SDR scale projection is reported alongside unaligned SNR and RMS change; synthetic noise and read-audiobook speech do not establish field intelligibility",
        "arms": groups,
        "host_processing_seconds_total": {key: sum(values) for key, values in host_elapsed.items()},
        "host_processing_seconds_per_recording_p50": {key: statistics.median(values) for key, values in host_elapsed.items()},
        "frame_engine_latency_us_median_across_case_percentiles": {percentile: statistics.median(
                row["arms"]["frame_gate"]["native_frame_stats"][f"frame_us_{percentile}"]
                for row in rows) for percentile in ("p50", "p95", "p99", "max")},
        "frame_engine_per_case_latency_us": [row["arms"]["frame_gate"]["native_frame_stats"] for row in rows]}
    summary_path = out / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--frame-cli", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    summary = run(args.manifest, args.frame_cli, args.out)
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
