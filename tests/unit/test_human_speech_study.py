"""Frozen inputs and paired speaker uncertainty must survive the study boundary."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("human_study", Path(__file__).parents[2] / "scripts/human_speech_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


class HumanStudyTests(unittest.TestCase):
    def rows(self):
        return [{"id": str(i), "speaker_id": str(i), "reference": "one two", "hypothesis": "one two",
                 "duration_s": 2, "audio_sha256": "abc", "job_status": "SUCCEEDED"} for i in range(3)]

    def test_paired_difference_and_reordering(self):
        left, right = self.rows(), self.rows()[::-1]
        for row in left:
            row["hypothesis"] = "one"
        result = study.compare(left, right)
        self.assertEqual(result["wer_left_minus_right"], 0.5)
        self.assertEqual(result["speaker_bootstrap_95_percentile"], [0.5, 0.5])

    def test_wrong_pairs_and_failed_jobs_rejected(self):
        for key, value in [("id", "wrong"), ("reference", "different"), ("speaker_id", "wrong"),
                           ("audio_sha256", "wrong"), ("duration_s", 3), ("job_status", "RETRY")]:
            left, right = self.rows(), self.rows()
            right[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                study.compare(left, right)
        with self.assertRaises(ValueError):
            study.compare(self.rows(), self.rows() + self.rows()[:1])

    def test_audio_hash_checked_before_call_or_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            audio, manifest = root / "clip.m4a", root / "manifest.json"
            audio.write_bytes(b"audio")
            rows = self.rows()
            for row in rows:
                row.update(audio_path=str(audio), audio_sha256=study.digest(audio))
            manifest.write_text(json.dumps({"utterances": rows}))
            self.assertEqual(len(study.load_manifest(manifest)["utterances"]), 3)
            audio.write_bytes(b"mutated")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                study.run(manifest, "http://127.0.0.1:1/inference", "test", root / "result")
            self.assertFalse((root / "result").exists())

    def test_remote_endpoint_rejected(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            study.run("missing.json", "https://paid.example/transcriptions", "test", "unused")


if __name__ == "__main__":
    unittest.main()
