import importlib.util
from pathlib import Path
import unittest
spec = importlib.util.spec_from_file_location("public_workflow", Path(__file__).parents[2] / "public-demo/workflow.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

class RecordingWorkflowTests(unittest.TestCase):
    def test_reviewed_export_uses_edit_without_destroying_provider_revision(self):
        r = demo.run_workflow({"step": "export", "text": "Corrected morning recording."})
        self.assertEqual(r["export_content"], "Corrected morning recording.")
        self.assertIn("warning", r["original_text"])
        self.assertEqual((r["state"], r["revision"]), ("EXPORTED", 2))
        self.assertGreater(r["recovered_offset"], 0)
        self.assertEqual(r["audio_sha256"], "cdecf106f064c3bded6d40bb92fd69029e893d517b664084f472d0e5987b2355")
    def test_public_input_cannot_choose_files_or_unbounded_text(self):
        for payload in ({"step":"export","text":"x"*4001}, {"step":"export","text":"x","path":"/etc/passwd"}):
            with self.assertRaises(ValueError):demo.run_workflow(payload)
