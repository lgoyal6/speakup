import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("live", Path(__file__).resolve().parents[2] / "public-demo/live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
BASE = {"chunks": 4, "chunk_bytes": 256, "seed": 7, "retry_chunk": 0, "restart_after": 1, "corrupt": False}


class PublicUploadTests(unittest.TestCase):
    def test_actual_resume_retry_restart_and_digest(self):
        result = live.run_upload(BASE)
        self.assertTrue(result["completed"])
        self.assertEqual(result["expected_sha256"], result["recorded_sha256"])
        self.assertTrue(any(row["event"].startswith("Retry refused") for row in result["timeline"]))
        self.assertTrue(any(row["event"].startswith("Reopened") for row in result["timeline"]))
        changed = live.run_upload({**BASE, "seed": 12, "chunks": 8})
        self.assertNotEqual(changed["total_bytes"], result["total_bytes"])
        self.assertNotEqual(changed["recorded_sha256"], result["recorded_sha256"])

    def test_digest_rejection_and_final_retry(self):
        rejected = live.run_upload({**BASE, "corrupt": True})
        self.assertFalse(rejected["completed"])
        self.assertIsNone(rejected["recorded_sha256"])
        self.assertEqual(rejected["timeline"][-1]["offset"], 0)
        final = live.run_upload({**BASE, "retry_chunk": 3})
        self.assertTrue(any(row["event"] == "Retry acknowledged without appending" for row in final["timeline"]))

    def test_bounds(self):
        for patch in [{"chunks": 13}, {"chunks": True}, {"seed": -1}, {"chunk_bytes": 2048}, {"retry_chunk": 4}, {"corrupt": 1}, {"url": "http://localhost"}]:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                live.run_upload({**BASE, **patch})
