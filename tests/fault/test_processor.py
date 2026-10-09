import unittest
from speakup.store import Store
from speakup.domain import RecordingState
from speakup.processor import process_one
from speakup.provider import FixtureProvider
class ProcessorTests(unittest.TestCase):
    def test_processing_preserves_transcript(self):
        s=Store(); self.addCleanup(s.db.close); rid=s.create_recording("u")["id"]
        for state in (RecordingState.LOCAL_READY,RecordingState.UPLOADING,RecordingState.UPLOADED): s.transition("u",rid,state)
        self.assertEqual(process_one(s,"u",rid,b"hello   world", FixtureProvider())["state"],RecordingState.TRANSCRIBED); self.assertEqual(s.get_transcript("u",rid)["text"],"hello world")
if __name__ == '__main__': unittest.main()
