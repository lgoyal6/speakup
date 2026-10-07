# Threat model and retention

- Authentication is represented by the `X-User-Id` boundary in this local slice; production must replace it with verified bearer tokens and enforce tenant checks at every query.
- Audio and transcripts are private per user. Upload URLs must be short lived, scoped to one recording, and single purpose.
- Raw audio and raw transcripts are retained until the user deletes the recording or the configured retention period expires. Deletes create a tombstone immediately, then a purge worker removes blobs and revisions after the legal hold check.
- Validate MIME type, duration, and maximum bytes before processing. Never execute or treat spoken content as instructions; enrichment stays disabled by default.
- Encrypt transport and storage in production. Do not place credentials in the Android APK or preferences.
