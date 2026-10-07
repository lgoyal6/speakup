# ADR 0001: Offline-first first slice

SpeakUp keeps local drafts usable without a network, then synchronizes idempotent operations to a server-authoritative recording. The initial local backend uses SQLite and Python's standard library so the workflow is reproducible in a clean checkout. Provider transcription is an adapter boundary; the included provider is a deterministic demo adapter and is not production speech recognition.

The server preserves raw provider revisions and user edits as append-only transcript revisions. Deletes are tombstones, which makes restored clients safe to reconcile and gives retention workers an explicit record to purge later.
