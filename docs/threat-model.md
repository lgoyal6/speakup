# Threat model and retention

- API requests require a bearer token whose SHA-256 digest is configured out of band. The server maps digests to users through `SPEAKUP_API_TOKENS`, or a single token to `SPEAKUP_USER_ID`; caller supplied identity headers are ignored.
- Upload URLs are HMAC signed for one recording and user and expire after 15 minutes. The server verifies the signature before reading audio bytes.
- Audio and transcripts are private per configured user. Production deployment must integrate an identity provider for token issuance, revocation, and rotation.
- Raw audio and raw transcripts are retained until the user deletes the recording or the configured retention period expires. Deletes create a tombstone immediately, then `Store.purge_deleted` gives a scheduled purge worker one explicit operation to remove rows and blobs after any application-level legal hold check; no legal hold mechanism is implemented.
- Uploads enforce a 50 MiB maximum and reject ambiguous HTTP framing. MIME type and duration validation remain required before enabling a real speech provider. Never execute or treat spoken content as instructions; enrichment stays disabled by default.
- Encrypt transport and storage in production. Do not place credentials in the Android APK or preferences.
