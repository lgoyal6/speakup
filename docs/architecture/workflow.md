# SpeakUp workflow

The primary path is: create a local draft, mark it ready, obtain a short-lived upload URL, complete the upload, enqueue processing, save the provider transcript, edit it, export it, and delete it. Every transition is validated by `services/speakup/domain.py`; repeated transitions and sync operations are idempotent.

The HTTP adapter is intentionally thin. Domain transitions and persistence live in `services/speakup`, so Android, CLI, and future transports can share them. A production deployment should replace the demo upload endpoint and provider adapter while retaining the contracts and audit trail.
