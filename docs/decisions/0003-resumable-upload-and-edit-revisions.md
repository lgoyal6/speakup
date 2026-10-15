# 0003 Resumable upload, idempotent completion and edit revisions

Status: accepted for the local build. Device and live-provider validation are still open.

## Problem

The Android client could not finish a sync on its own. `UploadWorker` asked for an upload URL for a recording the server had never seen (no create call), generated a fresh random idempotency key every time the work was enqueued, never advanced the Room row past `LOCAL_READY`, and re-sent the whole file after any interruption. On the server, a repeated `/complete` or `/process` after the recording had moved on returned 400, so a client that died between the server acknowledging a step and recording that acknowledgement locally could never converge. Transcript edits had no base revision, so an offline edit could silently overwrite a newer edit.

## Invariants

1. **No lost recording.** A Room row exists before `MediaRecorder.start`. The row becomes `LOCAL_READY` only after the file is closed, fsynced and non-empty. Upload size and SHA-256 are computed from the app-private file before server create. Interrupted captures are retained as `INTERRUPTED` and are never silently uploaded.
2. **Server facts win for upload progress.** The durable byte offset is the length of the server's `.part` file. The client asks for it (`POST /upload-url` returns `offset`) instead of trusting its own counter, so a process death on either side loses at most one chunk of transfer.
3. **Chunk atomicity.** A `PUT` with `Content-Range: bytes S-E/T` is appended only if every byte of the chunk arrived and `S` equals the durable offset. A short body is rejected without changing the offset. A wrong `S` returns 409 with the current offset. A full disk truncates back to `S` and returns 507 with the offset.
4. **Integrity before completion.** When the last byte arrives, the server compares size and SHA-256 with the values declared at create time. A mismatch discards the partial file and returns 422.
5. **Idempotent steps.** `POST /recordings` with a client id, `/complete` and `/process` return the existing result when repeated, including after the server state has moved further along. A transcript edit with an `idempotency_key` creates at most one revision.
6. **Stable operation keys.** WorkManager stores the operation key in its durable input, so retries reuse it. Edit keys are derived from recording id and `localOperationId`; changing local edit/delete state replaces queued work with a new operation key.
7. **No stale overwrite.** An edit carries `base_revision`. If the visible transcript revision differs, the server returns 409 and the client keeps the local text as `CONFLICT` for review.
8. **Delete fences processing.** A delete cancels the job and the worker's lease check rejects late provider output (unchanged from 0002).
9. **Credential failures preserve local work.** HTTP 401/403 keeps Room and the recording file intact while WorkManager retries with backoff. Saving replacement credentials replaces the queued worker for every resumable row. Permanent 4xx responses stop that worker without deleting the local row.

## Ordering and scope

Idempotency is scoped per recording and per edit operation. Edits are ordered by the server's revision number, not by device clocks. A local delete replaces active per-recording sync work and is retried until the remote tombstone is confirmed. Partial upload files are removed with the recording by `purge_deleted`.

## Compatibility

A `PUT` without `Content-Range` keeps the original whole-body behavior. Create without `size`/`sha256`, and edits without `base_revision`, remain accepted by the server. Room moves from schema 1 to 2 with a migration that adds a defaulted upload offset.
