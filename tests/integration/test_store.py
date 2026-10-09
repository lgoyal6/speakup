import unittest
from speakup.store import Store
from speakup.domain import RecordingState
class StoreTests(unittest.TestCase):
    def test_duplicate_sync_and_revision_preservation(self):
        s=Store(); self.addCleanup(s.db.close); r=s.create_recording("u"); rid=r["id"]; op={"device_id":"d1","local_operation_id":1,"idempotency_key":"k1","kind":"create","payload":{"id":rid}}
        self.assertTrue(s.apply_sync(op)["applied"]); self.assertTrue(s.apply_sync(op)["duplicate"])
        for state in (RecordingState.LOCAL_READY,RecordingState.UPLOADING,RecordingState.UPLOADED,RecordingState.PROCESSING): s.transition("u",rid,state)
        s.save_transcript("u",rid,"raw", "provider", "m1"); s.save_transcript("u",rid,"edited", "user_edit", None)
        self.assertEqual(s.get_transcript("u",rid)["text"],"edited"); self.assertEqual(s.export("u",rid)["content"],"edited")
    def test_tenant_isolation(self):
        s=Store(); self.addCleanup(s.db.close); rid=s.create_recording("u1")["id"]
        with self.assertRaises(KeyError): s.get_recording("u2",rid)
    def test_sync_rejects_out_of_order_operations(self):
        s=Store(); self.addCleanup(s.db.close); s.apply_sync({"device_id":"d","local_operation_id":2,"idempotency_key":"k2","kind":"edit"})
        result=s.apply_sync({"device_id":"d","local_operation_id":1,"idempotency_key":"k1","kind":"edit"})
        self.assertEqual(result["conflict"], "out_of_order")
    def test_job_claim_is_restart_safe(self):
        s=Store(); self.addCleanup(s.db.close); rid=s.create_recording("u")["id"]
        for state in (RecordingState.LOCAL_READY,RecordingState.UPLOADING,RecordingState.UPLOADED): s.transition("u",rid,state)
        job=s.enqueue_job("u",rid)
        claimed=s.claim_job(job["id"])
        self.assertEqual(claimed["status"], "RUNNING")
        self.assertIsNone(s.claim_job(job["id"]))
        self.assertEqual(s.finish_job(job["id"], False, "timeout", lease_token=claimed["lease_token"])["status"], "RETRYING")
    def test_deleted_data_is_purgeable_after_retention(self):
        s=Store(); self.addCleanup(s.db.close); rid=s.create_recording("u")["id"]; s.delete("u",rid)
        s.db.execute("UPDATE recordings SET deleted_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,)); s.db.commit()
        self.assertEqual(s.purge_deleted(1), 1)
if __name__ == '__main__': unittest.main()
