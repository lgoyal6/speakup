#!/usr/bin/env python3
"""Real container persistence gate. All test resources are uniquely owned and removed."""
import hashlib
import http.client
import json
import subprocess
import time
import uuid

name = "speakup-gate-" + uuid.uuid4().hex[:10]
volume = name + "-data"
image = name + ":test"

def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()

def request(port, method, path, body=None, authenticated=True):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    headers = {"Authorization": "Bearer gate-token"} if authenticated else {}
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    try:
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()

def wait(port):
    for _ in range(30):
        try:
            if request(port, "GET", "/healthz")[0] == 200:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(1)
    raise RuntimeError("container did not become healthy")

try:
    subprocess.run(["docker", "build", "-f", "infra/docker/Dockerfile", "-t", image, "."], check=True)
    docker("volume", "create", volume)
    docker("run", "-d", "--name", name, "--memory", "256m", "--cpus", "1",
           "-p", "127.0.0.1::8080", "-v", volume + ":/data",
           "-e", "SPEAKUP_DB=/data/state.db", "-e", "SPEAKUP_AUDIO_DIR=/data/audio",
           "-e", "SPEAKUP_HOST=0.0.0.0", "-e", "SPEAKUP_USER_ID=gate-user",
           "-e", "SPEAKUP_API_TOKEN_SHA256=" + hashlib.sha256(b"gate-token").hexdigest(),
           "-e", "SPEAKUP_UPLOAD_SECRET=gate-only-secret", image, "python", "-m", "speakup.api")
    port = int(docker("port", name, "8080/tcp").rsplit(":", 1)[1])
    wait(port)
    assert request(port, "POST", "/v1/recordings", {}, False)[0] == 401
    status, record = request(port, "POST", "/v1/recordings", {})
    assert status == 201
    rid = record["id"]
    status, upload = request(port, "POST", f"/v1/recordings/{rid}/upload-url", {})
    assert status == 200
    assert request(port, "PUT", upload["url"], b"container audio")[0] == 200
    assert request(port, "POST", f"/v1/recordings/{rid}/complete", {})[0] == 200
    docker("restart", name)
    port = int(docker("port", name, "8080/tcp").rsplit(":", 1)[1])
    wait(port)
    status, persisted = request(port, "GET", f"/v1/recordings/{rid}")
    assert status == 200 and persisted["state"] == "UPLOADED"
    assert persisted["audio_sha256"] == hashlib.sha256(b"container audio").hexdigest()
    assert docker("exec", name, "python", "-c", "from pathlib import Path; assert Path(" + repr(persisted["local_path"]) + ").read_bytes() == b'container audio'") == ""
    print("Container verified: authentication, signed upload, restart persistence")
except Exception:
    subprocess.run(["docker", "logs", name], check=False)
    raise
finally:
    for command in (("rm", "-f", name), ("volume", "rm", volume), ("image", "rm", image)):
        subprocess.run(["docker", *command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
