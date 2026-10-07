import unittest
from speakup.store import Store
from speakup.domain import RecordingState
class StoreTests(unittest.TestCase):
    def test_duplicate_sync_and_revision_preservation(self):
        s=Store(); r=s.create_recording("u"); rid=r["id"]; op={"device_id":"d1","local_operation_id":1,"idempotency_key":"k1","kind":"create","payload":{"id":rid}}
        self.assertTrue(s.apply_sync(op)["applied"]); self.assertTrue(s.apply_sync(op)["duplicate"])
        for state in (RecordingState.LOCAL_READY,RecordingState.UPLOADING,RecordingState.UPLOADED,RecordingState.PROCESSING): s.transition("u",rid,state)
        s.save_transcript("u",rid,"raw", "provider", "m1"); s.save_transcript("u",rid,"edited", "user_edit", None)
        self.assertEqual(s.get_transcript("u",rid)["text"],"edited"); self.assertEqual(s.export("u",rid)["content"],"edited")
    def test_tenant_isolation(self):
        s=Store(); rid=s.create_recording("u1")["id"]
        with self.assertRaises(KeyError): s.get_recording("u2",rid)
    def test_sync_rejects_out_of_order_operations(self):
        s=Store(); s.apply_sync({"device_id":"d","local_operation_id":2,"idempotency_key":"k2","kind":"edit"})
        result=s.apply_sync({"device_id":"d","local_operation_id":1,"idempotency_key":"k1","kind":"edit"})
        self.assertEqual(result["conflict"], "out_of_order")
    def test_job_claim_is_restart_safe(self):
        s=Store(); rid=s.create_recording("u")["id"]; job=s.enqueue_job("u",rid)
        self.assertEqual(s.claim_job(job["id"])["status"], "RUNNING")
        self.assertEqual(s.claim_job(job["id"])["status"], "RUNNING")
        self.assertEqual(s.finish_job(job["id"], False, "timeout")["status"], "RETRYING")
if __name__ == '__main__': unittest.main()
