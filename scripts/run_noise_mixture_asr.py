"""Run the preregistered local CPU Whisper WER comparison on frozen mixtures."""
import argparse
import array
import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from speakup.metrics import word_edits  # noqa: E402
from prepare_noise_mixture_study import file_sha  # noqa: E402

MODEL_SHA256 = "a03779c86df3323075f5e796cb2ce5029f00ec8869eee3fdfb897afe36c6d002"


def read_pcm_wav(path: Path) -> array.array:
    with wave.open(str(path), "rb") as stream:
        if stream.getnchannels() != 1 or stream.getframerate() != 16000 or stream.getsampwidth() != 2:
            raise ValueError(f"unsupported audio for ASR: {path}")
        samples = array.array("h", stream.readframes(stream.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def write_pcm_wav(path: Path, pcm: array.array) -> None:
    data = array.array("h", pcm)
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(data.tobytes())


def make_arm(case: dict, arm: str, tmp: Path, frame_cli: Path) -> Path:
    source = Path(case["mixture_path"])
    if arm == "passthrough":
        return source
    pcm = read_pcm_wav(source)
    if arm == "afftdn":
        output = tmp / "afftdn.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                        "-af", "afftdn=nr=12:nf=-45:tn=1", "-c:a", "pcm_s16le", str(output)],
                       check=True, capture_output=True)
        return output
    raw = array.array("h", pcm)
    if sys.byteorder != "little":
        raw.byteswap()
    filtered = subprocess.run([str(frame_cli), "gate"], input=raw.tobytes(), capture_output=True, check=True)
    samples = array.array("h", filtered.stdout)
    if len(samples) != len(pcm):
        raise ValueError(f"frame gate changed length for {case['case_id']}")
    output = tmp / "frame-gate.wav"
    write_pcm_wav(output, samples)
    return output


def transcribe(cli: Path, model: Path, wav: Path, prefix: Path) -> tuple[str, float]:
    result = subprocess.run([str(cli), "-ng", "-t", "4", "-l", "en", "-bo", "1", "-bs", "1",
                             "-nf", "-np", "-nt", "-m", str(model), "-f", str(wav),
                             "-otxt", "-of", str(prefix)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"whisper failed for {wav.name}: {result.stderr[-1000:]}")
    transcript = Path(str(prefix) + ".txt")
    if not transcript.is_file():
        raise RuntimeError(f"whisper did not create transcript for {wav.name}")
    text = transcript.read_text().strip()
    return text, file_sha(transcript)


def run(manifest_path: Path, quality_rows_path: Path, asr_protocol_path: Path,
        model: Path, cli: Path, frame_cli: Path, out: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    protocol = json.loads(asr_protocol_path.read_text())
    cases = {row["case_id"]: row for row in manifest["cases"]}
    quality_rows = [json.loads(line) for line in quality_rows_path.read_text().splitlines()]
    if len(cases) != 160 or len(quality_rows) != len(cases):
        raise ValueError("ASR requires all 160 rows in the frozen mixture cohort")
    if file_sha(model) != MODEL_SHA256 or protocol["model_sha256_expected"] != MODEL_SHA256:
        raise ValueError("ASR model hash differs from the frozen official model")
    if protocol["mixture_raw_rows_sha256"] != file_sha(quality_rows_path):
        raise ValueError("quality raw rows changed after ASR protocol freeze")
    if protocol["manifest_sha256"] != file_sha(manifest_path):
        raise ValueError("mixture manifest changed after ASR protocol freeze")
    if out.exists():
        raise FileExistsError(out)
    out.mkdir(parents=True)
    transcript_dir = out / "transcripts"
    transcript_dir.mkdir()
    rows_path = out / "asr_rows.jsonl"
    cli_hash, model_hash, runner_hash = file_sha(cli), file_sha(model), file_sha(Path(__file__))
    arms = ("passthrough", "afftdn", "frame_gate")
    total = len(quality_rows) * len(arms)
    done = 0
    with rows_path.open("x") as raw:
        for quality in quality_rows:
            case = cases[quality["case_id"]]
            if case["mixture_sha256"] != quality["mixture_sha256"] or case["target_pcm_sha256"] != quality["target_pcm_sha256"]:
                raise ValueError(f"input hash drift for {quality['case_id']}")
            for arm in arms:
                with TemporaryDirectory(prefix="speakup-asr-") as directory:
                    tmp = Path(directory)
                    audio = make_arm(case, arm, tmp, frame_cli)
                    prefix = transcript_dir / f"{quality['case_id']}.{arm}"
                    transcript, transcript_sha = transcribe(cli, model, audio, prefix)
                    row = {"case_id": quality["case_id"], "target_id": case["target_id"],
                        "target_speaker_id": case["target_speaker_id"], "noise_type": case["noise_type"],
                        "snr_target_db": case["snr_target_db"], "arm": arm,
                        "reference": case["target_reference"], "hypothesis": transcript,
                        "job": {**word_edits(case["target_reference"], transcript)},
                        "mixture_sha256": case["mixture_sha256"], "target_pcm_sha256": case["target_pcm_sha256"],
                        "input_audio_sha256": file_sha(audio), "transcript_sha256": transcript_sha,
                        "runner_sha256": runner_hash, "whisper_cli_sha256": cli_hash,
                        "model_sha256": model_hash, "frame_cli_sha256": file_sha(frame_cli)}
                    raw.write(json.dumps(row, allow_nan=False) + "\n")
                    raw.flush()
                done += 1
                print(f"{done}/{total} {quality['case_id']} {arm}", flush=True)
    return {"inferences": done, "raw_rows_sha256": file_sha(rows_path), "runner_sha256": runner_hash,
            "whisper_cli_sha256": cli_hash, "model_sha256": model_hash,
            "frame_cli_sha256": file_sha(frame_cli), "raw_rows": str(rows_path),
            "transcripts": str(transcript_dir)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--quality-rows", required=True, type=Path)
    parser.add_argument("--asr-protocol", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--whisper-cli", required=True, type=Path)
    parser.add_argument("--frame-cli", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest, args.quality_rows, args.asr_protocol, args.model,
                         args.whisper_cli, args.frame_cli, args.out), indent=2))


if __name__ == "__main__":
    main()
