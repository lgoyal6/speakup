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
if __name__ == '__main__': unittest.main()
