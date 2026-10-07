import unittest
from speakup.domain import Recording, RecordingState, InvalidTransition
class DomainTests(unittest.TestCase):
    def test_recording_lifecycle_and_idempotence(self):
        r=Recording("r","u",RecordingState.LOCAL_DRAFT); self.assertIs(r.transition(RecordingState.LOCAL_DRAFT),r)
        r=r.transition(RecordingState.LOCAL_READY).transition(RecordingState.UPLOADING).transition(RecordingState.UPLOADED); self.assertEqual(r.state,RecordingState.UPLOADED)
    def test_invalid_transition_rejected(self):
        with self.assertRaises(InvalidTransition): Recording("r","u",RecordingState.LOCAL_DRAFT).transition(RecordingState.EXPORTED)
if __name__ == '__main__': unittest.main()
