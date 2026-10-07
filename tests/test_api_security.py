import hashlib
import time
import unittest
from unittest.mock import patch

from speakup.api import authenticate, signed_upload_path, verify_upload_signature


class ApiSecurityTests(unittest.TestCase):
    def test_bearer_digest_is_constant_time_and_header_is_required(self):
        token = "test-token"
        digest = hashlib.sha256(token.encode()).hexdigest()
        self.assertTrue(authenticate("Bearer " + token, digest))
        self.assertFalse(authenticate("Bearer wrong", digest))
        self.assertFalse(authenticate("", digest))
        self.assertFalse(authenticate("Bearer " + token, "short"))

    def test_upload_signature_is_scoped_and_expires(self):
        expires = int(time.time()) + 60
        with patch.dict("os.environ", {"SPEAKUP_UPLOAD_SECRET": "local-secret"}):
            path = signed_upload_path("recording", "user-a", expires)
            signature = path.split("sig=", 1)[1]
            self.assertTrue(verify_upload_signature("recording", "user-a", expires, signature))
            self.assertFalse(verify_upload_signature("recording", "user-b", expires, signature))
            self.assertFalse(verify_upload_signature("recording", "user-a", expires - 120, signature))

    def test_upload_signing_refuses_missing_secret(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                signed_upload_path("recording", "user-a", int(time.time()) + 60)


if __name__ == "__main__":
    unittest.main()
