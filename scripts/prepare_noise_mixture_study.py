"""Freeze a speaker-disjoint LibriSpeech test-other enhancement cohort.

The competing-talker masker uses separate LibriSpeech speakers under CC BY 4.0.
White and pink noise are generated deterministically and have no external source.
All audio and manifests belong in a new directory outside the repository.
"""
import argparse
import array
import hashlib
import io
import json
import math
import random
import re
import subprocess
import urllib.request
import wave
from pathlib import Path

REVISION = "71cacbfb7e2354c4226d01e70d77d5fca3d04ba1"
PARQUET_SHA256 = "38e0c86a8104585c577badd707ca4331e20fa2c645f46180af0b7bcdecff9249"
PARQUET_URL = f"https://huggingface.co/datasets/openslr/librispeech_asr/resolve/{REVISION}/other/test/0000.parquet?download=true"
SAMPLE_RATE = 16000
SNRS = (-5, 0, 5)
SOURCE_PREFIX = "speakup-enhancement-other-v1:"
MAX_AUDIO_COLUMN_BYTES = 230_000_000
MAX_RANGE_BYTES = 250_000_000


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duration_from_flac(data: bytes) -> float:
    if len(data) < 42 or data[:4] != b"fLaC" or data[4] & 127 != 0:
        raise ValueError("expected FLAC with leading STREAMINFO")
    packed = int.from_bytes(data[18:26], "big")
    rate, samples = packed >> 44, packed & ((1 << 36) - 1)
    if rate != SAMPLE_RATE:
        raise ValueError(f"expected {SAMPLE_RATE} Hz FLAC, got {rate}")
    return samples / rate


def decode_flac(data: bytes, path: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "flac", "-i", "pipe:0",
                    "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", "-f", "wav", str(path)],
                   input=data, check=True, capture_output=True)


def read_pcm(path: Path) -> array.array:
    with wave.open(str(path), "rb") as source:
        if source.getnchannels() != 1 or source.getframerate() != SAMPLE_RATE or source.getsampwidth() != 2:
            raise ValueError(f"unexpected WAV format: {path}")
        samples = array.array("h", source.readframes(source.getnframes()))
    if samples.itemsize != 2:
        raise ValueError("host short size is not 16 bits")
    if __import__("sys").byteorder != "little":
        samples.byteswap()
    return samples


def write_wav(path: Path, samples: list[float] | array.array) -> None:
    pcm = array.array("h", (max(-32768, min(32767, int(round(sample * 32767))))
                            for sample in samples))
    if __import__("sys").byteorder != "little":
        pcm.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(pcm.tobytes())


def normalized(samples: array.array) -> list[float]:
    return [sample / 32768.0 for sample in samples]


def rms(samples: list[float]) -> float:
    return math.sqrt(sum(value * value for value in samples) / max(1, len(samples)))


def noise_samples(kind: str, count: int, seed: int) -> list[float]:
    rng = random.Random(seed)
    white = [rng.gauss(0.0, 1.0) for _ in range(count)]
    if kind == "white":
        return white
    if kind == "pink":
        b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
        result = []
        for value in white:
            b0 = 0.99886 * b0 + value * 0.0555179
            b1 = 0.99332 * b1 + value * 0.0750759
            b2 = 0.96900 * b2 + value * 0.1538520
            b3 = 0.86650 * b3 + value * 0.3104856
            b4 = 0.55000 * b4 + value * 0.5329522
            b5 = -0.7616 * b5 - value * 0.0168980
            result.append(b0 + b1 + b2 + b3 + b4 + b5 + b6 + value * 0.5362)
            b6 = value * 0.115926
        return result
    raise ValueError(f"unsupported generated noise: {kind}")


def scaled_noise(target: list[float], source: list[float], snr_db: int) -> list[float]:
    if not source:
        raise ValueError("empty noise source")
    noise = [source[i % len(source)] for i in range(len(target))]
    trms, nrms = rms(target), rms(noise)
    if trms <= 1e-9 or nrms <= 1e-9:
        raise ValueError("target or noise has near-zero RMS")
    scale = trms / (nrms * (10.0 ** (snr_db / 20.0)))
    return [value * scale for value in noise]


def mix_case(target: list[float], noise: list[float]) -> tuple[list[float], list[float], float, float]:
    peak = max((abs(a + b) for a, b in zip(target, noise)), default=0.0)
    scale = min(1.0, 0.98 / max(peak, 1e-9))
    reference = [value * scale for value in target]
    mixture = [(a + b) * scale for a, b in zip(target, noise)]
    measured = 20 * math.log10(max(rms(target), 1e-12) / max(rms(noise), 1e-12))
    return reference, mixture, measured, scale


class RangeFile(io.RawIOBase):
    """Read pinned Parquet byte ranges without downloading its full 333 MB file."""
    size = 332_873_172

    def __init__(self):
        self.position = 0
        self.range_bytes = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.position = offset
        elif whence == io.SEEK_CUR:
            self.position += offset
        elif whence == io.SEEK_END:
            self.position = self.size + offset
        else:
            raise ValueError(f"unsupported seek origin {whence}")
        return self.position

    def read(self, count=-1):
        count = self.size - self.position if count < 0 else min(count, self.size - self.position)
        if count <= 0:
            return b""
        if self.position < 0 or self.position + count > self.size:
            raise ValueError("Parquet range is outside the frozen source")
        if self.range_bytes + count > MAX_RANGE_BYTES:
            raise ValueError(f"source range limit exceeded: {self.range_bytes + count} bytes")
        start, end = self.position, self.position + count - 1
        request = urllib.request.Request(PARQUET_URL, headers={"Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            content_range = response.headers.get("Content-Range", "")
        if len(data) != count or not content_range.startswith(f"bytes {start}-{end}/"):
            raise IOError(f"unexpected remote range response: {content_range}, {len(data)} bytes")
        self.position += count
        self.range_bytes += count
        return data

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def load_remote_rows(target_speakers: int):
    import pyarrow.parquet as pq

    source = RangeFile()
    parquet = pq.ParquetFile(source)
    metadata = parquet.metadata
    schema = metadata.schema.names
    if schema != ["file", "bytes", "path", "text", "speaker_id", "chapter_id", "id"]:
        raise ValueError("unexpected pinned LibriSpeech Parquet schema")
    audio_column = schema.index("bytes")
    group_speakers = {}
    for index in range(metadata.num_row_groups):
        table = parquet.read_row_group(index, columns=["speaker_id"])
        group_speakers[index] = {str(value) for value in table.column("speaker_id").to_pylist()}

    order = sorted(range(metadata.num_row_groups), key=lambda index: hashlib.sha256(
        (SOURCE_PREFIX + f"rowgroup:{index}").encode()).hexdigest())
    selected_groups, covered_speakers, estimated_audio_bytes = [], set(), 0
    coverage_target = target_speakers * 2 + 2
    for index in order:
        size = metadata.row_group(index).column(audio_column).total_compressed_size
        if estimated_audio_bytes + size > MAX_AUDIO_COLUMN_BYTES:
            continue
        selected_groups.append(index)
        estimated_audio_bytes += size
        covered_speakers.update(group_speakers[index])
        if len(covered_speakers) >= coverage_target:
            break
    if len(covered_speakers) < coverage_target:
        raise ValueError(f"limited source slice covers {len(covered_speakers)} speakers; need {coverage_target}")

    rows = []
    for index in selected_groups:
        table = parquet.read_row_group(index, columns=["id", "speaker_id", "text", "audio.bytes"])
        rows.extend(table.to_pylist())
    if source.range_bytes > MAX_RANGE_BYTES:
        raise ValueError("downloaded Parquet ranges exceeded the frozen transfer bound")
    provenance = {"mode": "pinned HTTP byte ranges; no complete parquet download",
        "dataset_revision": REVISION, "dataset_file": "other/test/0000.parquet",
        "dataset_file_sha256": PARQUET_SHA256, "selected_row_groups": selected_groups,
        "selected_row_group_audio_column_bytes": estimated_audio_bytes,
        "downloaded_http_range_bytes": source.range_bytes,
        "full_file_hash_basis": "Hugging Face LFS SHA-256 at the immutable revision; only selected row-group columns were downloaded"}
    return rows, provenance


def prepare_rows(rows, output: Path, target_speakers: int, source_selection: dict) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        audio = row["audio"]["bytes"]
        duration = duration_from_flac(audio)
        if 3.0 <= duration <= 12.0:
            row["duration_s"] = duration
            groups.setdefault(str(row["speaker_id"]), []).append(row)
    candidates = {}
    for speaker, rows in groups.items():
        rows.sort(key=lambda row: hashlib.sha256((SOURCE_PREFIX + "utterance:" + row["id"]).encode()).hexdigest())
        if len(rows) >= 2:
            candidates[speaker] = rows[:2]
    speakers = sorted(candidates, key=lambda speaker: hashlib.sha256(
        (SOURCE_PREFIX + "speaker:" + speaker).encode()).hexdigest())
    if target_speakers <= 0 or len(speakers) < target_speakers + 2:
        raise ValueError(f"need {target_speakers} target speakers plus two masker speakers; found {len(speakers)}")
    targets = speakers[:target_speakers]
    maskers = speakers[target_speakers:]
    output.mkdir(parents=True, exist_ok=False)
    (output / "targets").mkdir()
    (output / "maskers").mkdir()
    (output / "mixtures").mkdir()
    selected: dict[str, dict] = {}
    for role, speaker_ids in (("target", targets), ("masker", maskers)):
        for speaker in speaker_ids:
            for row in candidates[speaker]:
                selected[row["id"]] = row
                safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", row["id"])
                destination = output / ("targets" if role == "target" else "maskers") / f"{safe_id}.wav"
                decode_flac(row["audio"]["bytes"], destination)

    target_speaker_ids = set(targets)
    masker_rows = [row for row in selected.values() if str(row["speaker_id"]) in set(maskers)]
    cases = []
    for speaker in targets:
        for target_row in candidates[speaker]:
            target_id = target_row["id"]
            target_path = output / "targets" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', target_id)}.wav"
            target_pcm = normalized(read_pcm(target_path))
            masker_candidates = [row for row in masker_rows if row["duration_s"] >= target_row["duration_s"]]
            if not masker_candidates:
                masker_candidates = masker_rows
            masker_row = min(masker_candidates, key=lambda row: hashlib.sha256(
                (SOURCE_PREFIX + "masker:" + target_id + ":" + row["id"]).encode()).hexdigest())
            if str(masker_row["speaker_id"]) in target_speaker_ids:
                raise ValueError("target and masker speaker sets overlap")
            masker_id = masker_row["id"]
            masker_path = output / "maskers" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', masker_id)}.wav"
            masker_pcm = normalized(read_pcm(masker_path))
            clean_case_id = f"{target_id}-clean"
            clean_path = output / "mixtures" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', clean_case_id)}.wav"
            clean_reference_path = clean_path.with_name(clean_path.stem + ".reference.wav")
            clean_reference_path.write_bytes(target_path.read_bytes())
            clean_path.write_bytes(target_path.read_bytes())
            cases.append({"case_id": clean_case_id, "target_id": target_id,
                "target_speaker_id": speaker, "target_reference": target_row["text"],
                "target_duration_s": target_row["duration_s"], "target_flac_sha256": sha256(target_row["audio"]["bytes"]),
                "target_pcm_path": str(clean_reference_path.resolve()), "target_pcm_sha256": file_sha(clean_reference_path),
                "masker_id": None, "masker_speaker_id": None, "masker_flac_sha256": None,
                "masker_pcm_path": None, "masker_pcm_sha256": None,
                "noise_type": "clean", "noise_license": "CC-BY-4.0 source speech",
                "snr_target_db": None, "snr_measured_db_before_peak_guard": None,
                "seed": None, "mixture_path": str(clean_path.resolve()), "mixture_sha256": file_sha(clean_path),
                "noise_path": None, "noise_sha256": None,
                "sample_count": len(target_pcm), "sample_rate_hz": SAMPLE_RATE,
                "clip_guard_scale": 1.0})
            for kind in ("speech", "white", "pink"):
                seed = int(hashlib.sha256((SOURCE_PREFIX + f"{target_id}:{kind}").encode()).hexdigest()[:8], 16)
                raw_noise = masker_pcm if kind == "speech" else noise_samples(kind, len(target_pcm), seed)
                scaled = scaled_noise(target_pcm, raw_noise, 0)
                for snr in SNRS:
                    snr_noise = [value * (10.0 ** (-snr / 20.0)) for value in scaled]
                    reference, mixture, measured, clip_scale = mix_case(target_pcm, snr_noise)
                    case_id = f"{target_id}-{kind}-{snr:+d}db"
                    mix_path = output / "mixtures" / f"{re.sub(r'[^A-Za-z0-9_-]', '_', case_id)}.wav"
                    reference_path = mix_path.with_name(mix_path.stem + ".reference.wav")
                    noise_path = mix_path.with_name(mix_path.stem + ".noise.wav")
                    write_wav(reference_path, reference)
                    write_wav(mix_path, mixture)
                    write_wav(noise_path, [value * clip_scale for value in snr_noise])
                    cases.append({"case_id": case_id, "target_id": target_id,
                        "target_speaker_id": speaker, "target_reference": target_row["text"],
                        "target_duration_s": target_row["duration_s"], "target_flac_sha256": sha256(target_row["audio"]["bytes"]),
                        "target_pcm_path": str(reference_path.resolve()), "target_pcm_sha256": file_sha(reference_path),
                        "masker_id": masker_id if kind == "speech" else None,
                        "masker_speaker_id": str(masker_row["speaker_id"]) if kind == "speech" else None,
                        "masker_flac_sha256": sha256(masker_row["audio"]["bytes"]) if kind == "speech" else None,
                        "masker_pcm_path": str(masker_path.resolve()) if kind == "speech" else None,
                        "masker_pcm_sha256": file_sha(masker_path) if kind == "speech" else None,
                        "noise_type": kind, "noise_license": "CC-BY-4.0" if kind == "speech" else "synthetic deterministic PRNG",
                        "snr_target_db": snr, "snr_measured_db_before_peak_guard": measured,
                        "seed": seed, "mixture_path": str(mix_path.resolve()), "mixture_sha256": file_sha(mix_path),
                        "noise_path": str(noise_path.resolve()), "noise_sha256": file_sha(noise_path),
                        "sample_count": len(mixture), "sample_rate_hz": SAMPLE_RATE,
                        "clip_guard_scale": clip_scale})

    manifest = {"protocol_version": 1, "dataset": "openslr/librispeech_asr",
        "dataset_revision": REVISION, "dataset_file": "other/test/0000.parquet",
        "dataset_file_sha256": PARQUET_SHA256, "license": "CC-BY-4.0",
        "attribution": "Panayotov et al., LibriSpeech: An ASR corpus based on public domain audio books, ICASSP 2015",
        "source_selection": source_selection,
        "unused_clean_test_cohort": "test-clean speakers/utterances are excluded; this is the disjoint test-other split",
        "selection": "Two duration-eligible utterances per speaker, ordered by SHA256 of speakup-enhancement-other-v1:utterance:<id>",
        "speaker_split": {"rule": "stable SHA256 speaker order; first N speakers are targets and remaining speakers are only competing-talker maskers",
                          "target_speakers": targets, "masker_speakers": maskers},
        "snrs_db": list(SNRS), "noise_types": ["clean", "speech", "white", "pink"],
        "noise_method": "For each target, deterministic speaker-specific masker; synthetic white and Paul-Kellet pink noise use case seed derived from target ID and noise type; scale to requested RMS SNR; common peak guard applied to target, masker and mixture.",
        "cases": cases}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")
    (output / "cases.jsonl").write_text("".join(json.dumps(case, allow_nan=False) + "\n" for case in cases))
    return {"target_speakers": len(targets), "masker_speakers": len(maskers), "target_utterances": len(targets) * 2,
            "mixture_cases": len(cases), "manifest_sha256": file_sha(output / "manifest.json"), "output": str(output)}


def prepare_remote(output: Path, target_speakers: int) -> dict:
    rows, source_selection = load_remote_rows(target_speakers)
    return prepare_rows(rows, output, target_speakers, source_selection)


def prepare(parquet: Path, output: Path, target_speakers: int) -> dict:
    import pyarrow.parquet as pq
    if file_sha(parquet) != PARQUET_SHA256:
        raise ValueError("input is not the frozen LibriSpeech test-other parquet")
    source_selection = {"mode": "local full-file verification", "file_sha256_verified": True}
    return prepare_rows(pq.read_table(parquet).to_pylist(), output, target_speakers, source_selection)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parquet", type=Path)
    parser.add_argument("--remote-pinned-subset", action="store_true",
                         help="fetch a hash-bound set of pinned test-other row-group ranges under 250 MB")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--target-speakers", type=int, default=8)
    args = parser.parse_args()
    if args.remote_pinned_subset:
        if args.parquet is not None:
            parser.error("use --parquet or --remote-pinned-subset, not both")
        result = prepare_remote(args.out, args.target_speakers)
    else:
        if args.parquet is None:
            parser.error("--parquet or --remote-pinned-subset is required")
        result = prepare(args.parquet, args.out, args.target_speakers)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
