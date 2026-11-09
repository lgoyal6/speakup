"""Validate frozen local-ASR rows and summarize pooled WER with paired speaker bootstrap."""
import argparse
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path

ARMS = ("passthrough", "afftdn", "frame_gate")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pool(rows):
    total = {key: 0 for key in ("reference_words", "edits", "substitutions", "deletions", "insertions")}
    for row in rows:
        for key in total:
            total[key] += row["job"][key]
    total["utterances"] = len(rows)
    total["wer"] = total["edits"] / total["reference_words"] if total["reference_words"] else None
    return total


def summarize(rows, repetitions, seed):
    by_condition = defaultdict(list)
    by_speaker_arm = defaultdict(list)
    speakers = set()
    for row in rows:
        condition = (row["noise_type"], row["snr_target_db"])
        by_condition[(*condition, row["arm"])].append(row)
        by_speaker_arm[(row["target_speaker_id"], row["arm"])].append(row)
        speakers.add(row["target_speaker_id"])
    conditions = {}
    for condition in sorted({key[:2] for key in by_condition}, key=lambda x: (str(x[0]), str(x[1]))):
        name = f"{condition[0]}:{condition[1]}"
        conditions[name] = {arm: pool(by_condition[(*condition, arm)]) for arm in ARMS}
    # Each bootstrap draw resamples speakers, preserving the within-speaker pairing.
    speaker_ids = sorted(speakers)
    rng = random.Random(seed)
    paired = {}
    for arm in ARMS[1:]:
        deltas = []
        for _ in range(repetitions):
            sampled = [rng.choice(speaker_ids) for __ in speaker_ids]
            aggregate = {}
            for selected_arm in ("passthrough", arm):
                subset = [row for speaker in sampled for row in by_speaker_arm[(speaker, selected_arm)]
                          if row["noise_type"] != "clean"]
                aggregate[selected_arm] = pool(subset)["wer"]
            deltas.append(aggregate[arm] - aggregate["passthrough"])
        observed = {a: pool([r for r in rows if r["arm"] == a and r["noise_type"] != "clean"])
                    for a in ("passthrough", arm)}
        paired[arm] = {
            "comparison": "noisy conditions pooled; WER arm minus passthrough (negative favors arm)",
            "passthrough_wer": observed["passthrough"]["wer"], "arm_wer": observed[arm]["wer"],
            "delta_wer": observed[arm]["wer"] - observed["passthrough"]["wer"],
            "speaker_count": len(speaker_ids), "bootstrap_repetitions": repetitions,
            "bootstrap_seed": seed,
            "percentile_95_ci": [sorted(deltas)[int(.025 * repetitions)], sorted(deltas)[min(repetitions - 1, int(.975 * repetitions))]],
        }
    return {"inferences": len(rows), "speakers": speaker_ids, "pooled_wer_by_noise_snr_arm": conditions,
            "paired_speaker_bootstrap_noisy_pooled": paired}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--protocol", required=True, type=Path)
    p.add_argument("--rows", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--bootstrap-repetitions", type=int, default=10000)
    p.add_argument("--seed", type=int, default=6042026)
    a = p.parse_args()
    manifest = json.loads(a.manifest.read_text())
    protocol = json.loads(a.protocol.read_text())
    if sha(a.manifest) != protocol["manifest_sha256"]:
        raise SystemExit("manifest hash mismatch")
    rows = [json.loads(line) for line in a.rows.read_text().splitlines() if line]
    expected_cases = {case["case_id"]: case for case in manifest["cases"]}
    expected = {(case_id, arm) for case_id in expected_cases for arm in ARMS}
    actual = {(row["case_id"], row["arm"]) for row in rows}
    if len(rows) != len(expected) or actual != expected:
        raise SystemExit(f"incomplete/duplicate ASR rows: got {len(rows)}, expected {len(expected)}")
    for row in rows:
        case = expected_cases[row["case_id"]]
        if row["mixture_sha256"] != case["mixture_sha256"] or row["target_pcm_sha256"] != case["target_pcm_sha256"]:
            raise SystemExit(f"frozen input hash mismatch for {row['case_id']}")
    result = summarize(rows, a.bootstrap_repetitions, a.seed)
    result["provenance"] = {"manifest_sha256": sha(a.manifest), "asr_rows_sha256": sha(a.rows),
                            "asr_protocol_sha256": sha(a.protocol), "summarizer_sha256": sha(Path(__file__))}
    a.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"inferences": result["inferences"], "speakers": len(result["speakers"]),
                      "output": str(a.out), "asr_rows_sha256": result["provenance"]["asr_rows_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
