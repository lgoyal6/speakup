# SpeakUp

SpeakUp is an offline-first audio-to-reviewed-transcript workflow. The repository contains a dependency-light local backend, durable SQLite state, a deterministic processor adapter, API contracts, and an Android application with Room-backed capture state and WorkManager synchronization.

## Quickstart

```sh
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e .
PYTHONPATH=services python3 -m unittest discover -s tests -p 'test*.py' -v
export SPEAKUP_USER_ID=local-user
export SPEAKUP_API_TOKEN=local-token
export SPEAKUP_API_TOKEN_SHA256=$(printf %s "$SPEAKUP_API_TOKEN" | shasum -a 256 | cut -d ' ' -f 1)
export SPEAKUP_UPLOAD_SECRET=local-upload-secret
PYTHONPATH=services SPEAKUP_DB=/tmp/speakup.db python -m speakup.api
```

Create metadata, upload bytes through a signed URL, enqueue processing, edit, export, and delete:

```sh
base=http://127.0.0.1:8080/v1
auth="Authorization: Bearer $SPEAKUP_API_TOKEN"
id=$(curl -s -X POST "$base/recordings" -H "$auth" -H 'content-type: application/json' -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
upload=$(curl -s -X POST "$base/recordings/$id/upload-url" -H "$auth" | python -c 'import json,sys; print(json.load(sys.stdin)["url"])')
printf 'sample fixture audio' | curl -s -X PUT "http://127.0.0.1:8080$upload" -H "$auth" --data-binary @-
curl -s -X POST "$base/recordings/$id/complete" -H "$auth"
curl -s -X POST "$base/recordings/$id/process" -H "$auth"
# Start the worker with SPEAKUP_ALLOW_FIXTURE_PROVIDER=1 only for this fixture demo.
# Wait for GET /recordings/$id/transcript to return a transcript, then edit it.
curl -s -X POST "$base/recordings/$id/transcript" -H "$auth" -H 'content-type: application/json' -d '{"text":"reviewed transcript"}'
curl -s -X POST "$base/recordings/$id/export" -H "$auth" -H 'content-type: application/json' -d '{"format":"txt"}'
```

The worker has an explicit speech-provider boundary and an expiring database lease. Set `SPEAKUP_SPEECH_ENDPOINT`, `SPEAKUP_SPEECH_TOKEN`, and optionally `SPEAKUP_SPEECH_MODEL` for an OpenAI-compatible multipart `/audio/transcriptions` endpoint. The adapter accepts M4A audio up to 25 MiB. Transient provider failures use delayed retries with a three-attempt budget; permanent failures are terminal and never turn bytes into a fake transcript. The fixture provider is available only with `SPEAKUP_ALLOW_FIXTURE_PROVIDER=1` for offline tests. Bearer authentication, signed uploads, SQLite plus local audio persistence, fenced worker publication, edit preservation, and the Android MediaRecorder path are implemented. Android records become durable drafts before capture and are queued through unique WorkManager work after a successful stop. Managed blob storage, production identity management, and physical-device validation remain deployment work.

Run the connected Android and local-backend integration checks with `bash scripts/test_android_backend.sh`. A booted API 35 emulator or connected Android device and the Android SDK are required. The script starts an isolated local fixture service and removes its temporary database and audio files on exit.

## Speech adapters

Every speech adapter implements the `SpeechProvider` contract in `services/speakup/provider.py`: input size and SHA-256 are checked before any work, provenance comes from configuration rather than the response, and failures carry an explicit retry class without secrets. `tests/unit/test_provider_conformance.py` runs the same checks against the fixture adapter and the OpenAI-compatible adapter (loopback stub). To include a local whisper.cpp server, start `whisper-server -m <ggml model> --convert` and set `SPEAKUP_CONFORMANCE_WHISPER_URL=http://127.0.0.1:<port>/inference` and `SPEAKUP_CONFORMANCE_AUDIO=<m4a file>`; without them that adapter is skipped, not passed. `scripts/speech_study.py` runs a fixed synthetic-speech (macOS TTS) WER study through the real worker path against a loopback endpoint; it is not a human-speech benchmark.

## Deployment security checks

`python3 scripts/verify_container.py` builds the container, checks unauthenticated rejection, uploads through a signed URL, restarts the service, and verifies both audio bytes and metadata persist on the mounted volume. It removes its container, volume, and image on exit. The image runs with a 256 MiB memory limit during this gate.

For more than one local account, configure `SPEAKUP_API_TOKENS` as a JSON map of SHA-256 token digests to user IDs. Tokens select the user; `X-User-Id` has no effect. The reference Compose deployment uses one configured account and binds its host port to loopback. Put HTTPS and an identity provider in front of a public deployment. `/process` queues a job. Run `PYTHONPATH=services SPEAKUP_DB=/tmp/speakup.db python -m speakup.processor` in a separate process with the speech-provider settings. It continuously polls, recovers expired leases, delays retries, and stops on SIGTERM/SIGINT. The debug build and connected API 35 emulator test cover the Room migration and WorkManager upload, edit and delete path against the local fixture service. Live provider quality, physical-device background restrictions and field use remain unverified.
