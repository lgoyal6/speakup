#!/bin/bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
temp_root="$(mktemp -d "${TMPDIR:-/tmp}/speakup-android-e2e.XXXXXX")"
api_pid=""
worker_pid=""
proxy_pid=""

cleanup() {
  if [[ -n "$worker_pid" ]]; then kill "$worker_pid" 2>/dev/null || true; fi
  if [[ -n "$proxy_pid" ]]; then kill "$proxy_pid" 2>/dev/null || true; fi
  if [[ -n "$api_pid" ]]; then kill "$api_pid" 2>/dev/null || true; fi
  rm -rf "$temp_root"
}
trap cleanup EXIT INT TERM

unset SPEAKUP_SPEECH_ENDPOINT SPEAKUP_SPEECH_TOKEN SPEAKUP_SPEECH_MODEL
export SPEAKUP_DB="$temp_root/server.db"
export SPEAKUP_AUDIO_DIR="$temp_root/audio"
export SPEAKUP_USER_ID="h7-test-user"
export SPEAKUP_API_TOKEN="h7-test-token"
export SPEAKUP_API_TOKEN_SHA256="$(python3 -c 'import hashlib; print(hashlib.sha256(b"h7-test-token").hexdigest())')"
export SPEAKUP_UPLOAD_SECRET="h7-local-e2e-only"
export SPEAKUP_PORT=18081
export SPEAKUP_ALLOW_FIXTURE_PROVIDER=1

cd "$repo_root"
PYTHONPATH=services python3 -m speakup.api >"$temp_root/api.log" 2>&1 &
api_pid=$!
PYTHONPATH=services python3 -m speakup.processor >"$temp_root/worker.log" 2>&1 &
worker_pid=$!
python3 scripts/android_fault_proxy.py >"$temp_root/proxy.log" 2>&1 &
proxy_pid=$!

ready=0
for _ in {1..40}; do
  if curl --silent --fail http://127.0.0.1:18081/healthz >/dev/null && curl --silent --fail http://127.0.0.1:18080/__fault_state >/dev/null; then ready=1; break; fi
  sleep 0.25
done
if [[ "$ready" != 1 ]]; then
  cat "$temp_root/api.log" "$temp_root/worker.log"
  echo "local SpeakUp API did not become ready" >&2
  exit 1
fi

ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}" gradle --offline -p android :app:assembleDebug :app:testDebugUnitTest :app:connectedDebugAndroidTest
