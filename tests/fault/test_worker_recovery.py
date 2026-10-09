import hashlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from speakup.domain import RecordingState
from speakup.processor import process_next, process_one, run_worker
from speakup.provider import FixtureProvider, ProviderError, SpeechProvider, Transcript
from speakup.store import Store


class WorkerRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.stores = []
        self.s = self.open_store()
        self.audio = b"test audio"
        self.sha = hashlib.sha256(self.audio).hexdigest()
        audio_path = self.path / "recording.m4a"
        audio_path.write_bytes(self.audio)
        self.rid = self.s.create_recording("u", str(audio_path), self.sha)["id"]
        for state in (RecordingState.LOCAL_READY, RecordingState.UPLOADING, RecordingState.UPLOADED):
            self.s.transition("u", self.rid, state)
        self.job = self.s.enqueue_job("u", self.rid)

    def open_store(self):
        s = Store(str(self.path / "state.db"))
        self.stores.append(s)
        return s

    def tearDown(self):
        for store in self.stores:
            store.db.close()
        self.temp.cleanup()

    def test_distinct_connections_cannot_claim_the_same_attempt(self):
        other = self.open_store()
        start = threading.Barrier(2)
        def claim(s):
            start.wait()
            return s.claim_next_job(current_time=100, lease_seconds=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = list(pool.map(claim, (self.s, other)))
        self.assertEqual(sum(job is not None for job in jobs), 1)
        self.assertEqual(self.s.get_job(self.job["id"])["attempts"], 1)

    def test_expired_worker_cannot_publish_after_restart(self):
        old = self.s.claim_next_job(current_time=100, lease_seconds=10)
        new = self.open_store().claim_next_job(current_time=110, lease_seconds=10)
        result = Transcript("raw", "test", "v1")
        self.assertIsNone(self.s.publish_transcript(old["id"], old["lease_token"], result, self.sha, current_time=111))
        self.assertIsNone(self.s.finish_job(old["id"], False, "stale", lease_token=old["lease_token"], current_time=111))
        self.assertEqual(self.s.publish_transcript(new["id"], new["lease_token"], result, self.sha, current_time=111)["status"], "SUCCEEDED")
        self.assertEqual(self.s.get_transcript("u", self.rid)["text"], "raw")
        self.assertEqual(self.open_store().get_job(new["id"])["status"], "SUCCEEDED")

    def test_failure_backoff_and_attempt_budget_prevent_hot_retry_loop(self):
        for attempt, instant in enumerate((100, 102, 105), 1):
            job = self.s.claim_next_job(current_time=instant, lease_seconds=10)
            self.assertEqual(job["attempts"], attempt)
            failed = self.s.finish_job(job["id"], False, "timeout", lease_token=job["lease_token"], current_time=instant)
            self.assertEqual(failed["status"], "FAILED" if attempt == 3 else "RETRYING")
            self.assertIsNone(self.s.claim_next_job(current_time=instant))
        self.assertIsNone(self.s.claim_next_job(current_time=999))
        self.assertEqual(self.s.get_recording("u", self.rid)["state"], RecordingState.FAILED)

    def test_crashing_final_attempt_becomes_terminal(self):
        for instant in (100, 110, 120):
            self.assertIsNotNone(self.s.claim_next_job(current_time=instant, lease_seconds=10))
        self.assertIsNone(self.open_store().claim_next_job(current_time=130))
        self.assertEqual(self.s.get_job(self.job["id"])["status"], "FAILED")

    def test_edit_survives_late_provider_output_and_export_uses_edit(self):
        job = self.s.claim_next_job(current_time=100)
        self.s.save_transcript("u", self.rid, "previous raw", "test", "v1")
        self.s.save_transcript("u", self.rid, "reviewed", "user_edit", None)
        self.s.publish_transcript(job["id"], job["lease_token"], Transcript("late raw", "test", "v1"), self.sha, current_time=101)
        self.assertEqual(self.s.get_transcript("u", self.rid)["text"], "reviewed")
        self.assertEqual(self.s.export("u", self.rid)["content"], "reviewed")
        self.assertEqual(self.s.get_recording("u", self.rid)["state"], RecordingState.EXPORTED)
        self.assertEqual(self.s.db.execute("SELECT text FROM transcript_revisions ORDER BY revision DESC LIMIT 1").fetchone()[0], "late raw")

    def test_deletion_fences_inflight_provider_and_cancels_job(self):
        job = self.s.claim_next_job(current_time=100)
        self.open_store().delete("u", self.rid)
        self.assertIsNone(self.s.publish_transcript(job["id"], job["lease_token"], Transcript("late", "test", "v1"), self.sha, current_time=101))
        self.assertEqual(self.s.get_job(job["id"])["status"], "CANCELLED")
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM transcript_revisions").fetchone()[0], 0)
        with self.assertRaises(KeyError):
            self.s.get_transcript("u", self.rid)

    def test_publication_failure_rolls_back_revision_state_and_success(self):
        job = self.s.claim_next_job(current_time=100)
        self.s.db.execute("CREATE TRIGGER reject_success BEFORE UPDATE ON jobs WHEN NEW.status='SUCCEEDED' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        self.s.db.commit()
        with self.assertRaises(Exception):
            self.s.publish_transcript(job["id"], job["lease_token"], Transcript("raw", "test", "v1"), self.sha, current_time=101)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM transcript_revisions").fetchone()[0], 0)
        self.assertEqual(self.s.get_job(job["id"])["status"], "RUNNING")
        self.assertEqual(self.s.get_recording("u", self.rid)["state"], RecordingState.PROCESSING)

    def test_fixture_mode_is_never_selected_implicitly(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            process_one(self.s, "u", self.rid, self.audio)
        self.assertEqual(process_next(self.s, FixtureProvider())["status"], "SUCCEEDED")

    def test_permanent_provider_failure_is_terminal_and_redacts_message(self):
        class Fails(SpeechProvider):
            def transcribe(self, audio, digest):
                raise ProviderError("secret token", retryable=False)
        self.assertEqual(process_next(self.s, Fails())["status"], "FAILED")
        self.assertNotIn("secret", self.s.get_job(self.job["id"])["last_error"])

    def test_idle_worker_waits_and_obeys_shutdown(self):
        self.s.delete("u", self.rid)
        stop = threading.Event()
        worker = threading.Thread(target=run_worker, args=(self.s, FixtureProvider(), stop, 0.01))
        worker.start()
        stop.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
