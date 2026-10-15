"""Named upload/edit failure cases plus a valid/invalid control over the fault matrix.

Each test states its oracle. The matrix subset runs every fault kind once on the
resumable protocol (all invariants must hold) and the baseline protocol on the two
kinds it is known to mishandle (the checks must be able to fail).
"""
import errno
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from speakup.domain import RecordingState
from speakup.store import StaleRevision, Store
from speakup.uploads import DigestMismatch, OffsetMismatch, ResumableUploads, StorageFull, parse_content_range

from tests.fault.upload_matrix import KINDS, run_scenario, scenario_spec

AUDIO = bytes(range(256)) * 40  # 10240 bytes


class ChunkSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(str(root / "s.db"))
        self.uploads = ResumableUploads(self.store, root / "audio")
        self.rid = self.store.create_recording("u", expected_size=len(AUDIO), expected_sha256=hashlib.sha256(AUDIO).hexdigest())["id"]
        for state in (RecordingState.LOCAL_READY, RecordingState.UPLOADING):
            self.store.transition("u", self.rid, state)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def put(self, start, end):
        return self.uploads.append("u", self.rid, start, len(AUDIO), AUDIO[start:end])

    def test_resume_from_server_offset_after_client_restart(self):
        # Oracle: offset equals bytes durably received; final digest equals the declared one.
        self.assertEqual(self.put(0, 4096).offset, 4096)
        self.assertEqual(self.uploads.offset(self.rid), 4096)  # what a restarted client is told
        result = self.put(4096, len(AUDIO))
        self.assertTrue(result.complete)
        self.assertEqual(result.sha256, hashlib.sha256(AUDIO).hexdigest())

    def test_wrong_start_is_rejected_with_current_offset(self):
        self.put(0, 4096)
        with self.assertRaises(OffsetMismatch) as ctx:
            self.put(0, 4096)  # duplicate of an acknowledged chunk
        self.assertEqual(ctx.exception.offset, 4096)
        self.assertEqual(self.uploads.offset(self.rid), 4096)

    def test_disk_full_truncates_torn_chunk_and_keeps_offset(self):
        self.put(0, 4096)
        with patch("speakup.uploads.os.fsync", side_effect=OSError(errno.ENOSPC, "full")), self.assertRaises(StorageFull) as ctx:
            self.put(4096, 8192)
        self.assertEqual(ctx.exception.offset, 4096)
        self.assertEqual(self.uploads.offset(self.rid), 4096)
        self.assertTrue(self.put(4096, len(AUDIO)).complete)

    def test_digest_mismatch_discards_partial_and_never_marks_uploaded(self):
        self.put(0, 4096)
        with self.assertRaises(DigestMismatch):
            self.uploads.append("u", self.rid, 4096, len(AUDIO), b"\x00" * (len(AUDIO) - 4096))
        self.assertEqual(self.uploads.offset(self.rid), 0)
        self.assertIsNone(self.store.get_recording("u", self.rid)["audio_sha256"])

    def test_repeated_final_chunk_after_lost_ack_is_acknowledged(self):
        self.put(0, len(AUDIO))
        self.assertTrue(self.put(0, len(AUDIO)).complete)

    def test_content_range_must_match_body(self):
        self.assertEqual(parse_content_range("bytes 0-9/20", 10), (0, 20))
        for header, length in (("bytes 0-9/20", 9), ("bytes 5-4/20", 0), ("bytes 0-20/20", 21), ("0-9/20", 10)):
            with self.assertRaises(ValueError):
                parse_content_range(header, length)


class EditRevisionTests(unittest.TestCase):
    def setUp(self):
        self.store = Store()
        audio = b"audio"
        self.rid = self.store.create_recording("u", "/x", hashlib.sha256(audio).hexdigest())["id"]
        self.store.save_transcript("u", self.rid, "raw", "provider", "m1")

    def tearDown(self):
        self.store.db.close()

    def test_stale_base_revision_conflicts_instead_of_overwriting(self):
        self.store.save_transcript("u", self.rid, "web edit", "user_edit", None, base_revision=1)
        with self.assertRaises(StaleRevision) as ctx:
            self.store.save_transcript("u", self.rid, "offline edit", "user_edit", None, base_revision=1)
        self.assertEqual(ctx.exception.current_revision, 2)
        self.assertEqual(self.store.get_transcript("u", self.rid)["text"], "web edit")

    def test_replayed_edit_key_creates_one_revision(self):
        first = self.store.save_transcript("u", self.rid, "edit", "user_edit", None, base_revision=1, client_key="k1")
        again = self.store.save_transcript("u", self.rid, "edit", "user_edit", None, base_revision=1, client_key="k1")
        self.assertEqual(first["revision"], again["revision"])
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM transcript_revisions WHERE source='user_edit'").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.store.save_transcript("u", self.rid, "different", "user_edit", None, client_key="k1")


class MatrixControlTests(unittest.TestCase):
    def run_kind(self, kind, protocol):
        index = KINDS.index(kind)  # first (short-audio) scenario of this kind
        with tempfile.TemporaryDirectory() as temp:
            return run_scenario(scenario_spec(index), protocol, Path(temp))

    def test_resumable_protocol_holds_every_invariant_for_every_fault_kind(self):
        for kind in KINDS:
            with self.subTest(kind=kind):
                row = self.run_kind(kind, "resumable")
                self.assertTrue(row["converged"], row)
                self.assertFalse(row["lost"])
                self.assertEqual(row["duplicates"], 0)
                self.assertFalse(row["stale_overwrite"])
                if kind not in {"none", "kill_record", "delete_during_transcription", "stale_edit"}:
                    self.assertTrue(row["faults_fired"], "armed fault must actually fire")

    def test_negative_control_baseline_protocol_is_caught(self):
        stale = self.run_kind("stale_edit", "baseline")
        self.assertTrue(stale["stale_overwrite"])
        self.assertFalse(stale["converged"])
        replay = self.run_kind("kill_edit", "baseline")
        self.assertGreater(replay["duplicates"], 0)


if __name__ == "__main__":
    unittest.main()
