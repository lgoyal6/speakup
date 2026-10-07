# SpeakUp

SpeakUp is an offline-first audio-to-reviewed-transcript workflow. The repository contains a dependency-light local backend, durable SQLite state, a deterministic processor adapter, API contracts, and an Android client skeleton that documents the production boundary.

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
curl -s -X POST "$base/recordings/$id/transcript" -H "$auth" -H 'content-type: application/json' -d '{"text":"reviewed transcript"}'
curl -s -X POST "$base/recordings/$id/export" -H "$auth" -H 'content-type: application/json' -d '{"format":"txt"}'
```

The included processor turns UTF-8 fixture bytes into deterministic text. Bearer authentication, signed uploads, and SQLite plus local audio persistence are implemented. Real speech recognition, managed blob storage, production identity management, and Android device validation remain deployment work and are explicitly documented as adapter boundaries.

## Deployment security checks

`python3 scripts/verify_container.py` builds the container, checks unauthenticated rejection, uploads through a signed URL, restarts the service, and verifies both audio bytes and metadata persist on the mounted volume. It removes its container, volume, and image on exit. The image runs with a 256 MiB memory limit during this gate.

For more than one local account, configure `SPEAKUP_API_TOKENS` as a JSON map of SHA-256 token digests to user IDs. Tokens select the user; `X-User-Id` has no effect. The reference Compose deployment uses one configured account and binds its host port to loopback. Put HTTPS and an identity provider in front of a public deployment. `/process` queues a job but does not run a background transcription service.
