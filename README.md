# SpeakUp

SpeakUp is an offline-first audio-to-reviewed-transcript workflow. The repository contains a dependency-light local backend, durable SQLite state, a deterministic processor adapter, API contracts, and an Android client skeleton that documents the production boundary.

## Quickstart

```sh
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -e .
PYTHONPATH=services python3 -m unittest discover -s tests -p 'test*.py' -v
PYTHONPATH=services SPEAKUP_DB=/tmp/speakup.db python -m speakup.api
```

Create metadata, advance the upload state, process deterministic audio, edit, export, and delete:

```sh
base=http://127.0.0.1:8080/v1
id=$(curl -s -X POST "$base/recordings" -H 'content-type: application/json' -d '{}' | python -c 'import json,sys; print(json.load(sys.stdin)["id"])')
for state in LOCAL_READY UPLOADING UPLOADED; do curl -s -X POST "$base/recordings/$id/transition" -H 'content-type: application/json' -d "{\"state\":\"$state\"}"; done
curl -s -X POST "$base/recordings/$id/process"
curl -s -X POST "$base/recordings/$id/transcript" -H 'content-type: application/json' -d '{"text":"reviewed transcript"}'
curl -s -X POST "$base/recordings/$id/export" -H 'content-type: application/json' -d '{"format":"txt"}'
```

The included processor turns UTF-8 fixture bytes into deterministic text. Real speech recognition, verified authentication, blob storage, and Android Gradle builds remain deployment work and are explicitly documented as adapter boundaries.
