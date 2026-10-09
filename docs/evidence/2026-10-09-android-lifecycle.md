# Android lifecycle evidence

The Android client persists a `LOCAL_DRAFT` row before `MediaRecorder.start`, promotes it to `LOCAL_READY` only after a non-empty file is closed, and enqueues unique WorkManager upload work with a connected-network constraint. A recorder released while active is marked `INTERRUPTED`, preserving metadata for user review instead of deleting it silently. `UploadWorker` keeps the stable recording idempotency key across retries.

Verification:

```sh
ANDROID_HOME=/Users/lakshgoyal/Library/Android/sdk gradle --offline -p android assembleDebug
PYTHONPATH=services python3 -m unittest discover -s tests -p 'test*.py' -q
```

Both commands passed on 2026-10-09. The Gradle result is a debug APK build, not physical-device or live VoiceOS provider validation. Provider validation still requires the configured endpoint and credentials.
