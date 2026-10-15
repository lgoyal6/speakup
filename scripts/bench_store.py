"""Matched SQLite workload for the two hot store queries: job claim and sync append.

The workload is a long-lived database where most jobs are finished history and a
small queue is live. It prints EXPLAIN QUERY PLAN for both queries and per-call
latency samples as JSON. Run it with the same arguments against two code versions
to compare them; nothing here changes the store.
"""
import argparse
import json
import random
import statistics
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services"))
from speakup.provider import Transcript  # noqa: E402
from speakup.store import Store  # noqa: E402

CLAIM_SQL = ("SELECT j.id FROM jobs j JOIN recordings r ON r.id=j.recording_id WHERE (? IS NULL OR j.id=?) "
             "AND r.state IN ('UPLOADED','PROCESSING') AND j.attempts<3 AND ((j.status IN ('QUEUED','RETRYING') "
             "AND j.next_attempt_at<=?) OR (j.status='RUNNING' AND COALESCE(j.lease_expires_at,0)<=?)) "
             "ORDER BY j.updated_at,j.id LIMIT 1")
SYNC_SQL = "SELECT MAX(local_operation_id) FROM sync_ops WHERE device_id=?"


def pct(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]


def summary(samples):
    return {"n": len(samples), "p50_us": pct(samples, .5), "p95_us": pct(samples, .95),
            "p99_us": pct(samples, .99), "mean_us": statistics.fmean(samples)}


def seed(store, history, queued, devices, ops_per_device, rng):
    ts = "2026-01-01T00:00:00+00:00"
    db = store.db
    rows, jobs = [], []
    for i in range(history + queued):
        rid = str(uuid.UUID(int=rng.getrandbits(128)))
        live = i >= history
        rows.append((rid, "u", "UPLOADED" if live else "EXPORTED", f"/audio/{rid}", "0" * 64, ts, ts))
        jobs.append((str(uuid.UUID(int=rng.getrandbits(128))), rid, "QUEUED" if live else "SUCCEEDED", 0 if live else 1, ts))
    db.executemany("INSERT INTO recordings(id,user_id,state,local_path,audio_sha256,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO jobs(id,recording_id,status,attempts,updated_at) VALUES(?,?,?,?,?)", jobs)
    ops = []
    cursor = 0
    for d in range(devices):
        for op in range(1, ops_per_device + 1):
            cursor += 1
            ops.append((f"k-{d}-{op}", f"device-{d}", op, "edit", "{}", cursor))
    db.executemany("INSERT INTO sync_ops VALUES(?,?,?,?,?,?)", ops)
    db.commit()
    db.execute("ANALYZE")
    db.commit()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=int, default=50_000)
    parser.add_argument("--queued", type=int, default=200)
    parser.add_argument("--devices", type=int, default=500)
    parser.add_argument("--ops-per-device", type=int, default=100)
    parser.add_argument("--claims", type=int, default=200)
    parser.add_argument("--syncs", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--label", default="unlabelled")
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    with tempfile.TemporaryDirectory() as temp:
        store = Store(str(Path(temp) / "bench.db"))
        seed(store, args.history, args.queued, args.devices, args.ops_per_device, rng)
        plans = {
            "claim": [tuple(r) for r in store.db.execute("EXPLAIN QUERY PLAN " + CLAIM_SQL, (None, None, 1e12, 1e12))],
            "sync_latest": [tuple(r) for r in store.db.execute("EXPLAIN QUERY PLAN " + SYNC_SQL, ("device-1",))],
        }
        indexes = [r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_autoindex%' ORDER BY name")]
        # Warm the page cache identically for both versions.
        for _ in range(3):
            store.db.execute(CLAIM_SQL, (None, None, 1e12, 1e12)).fetchall()
            store.db.execute(SYNC_SQL, ("device-0",)).fetchall()
        claim_us, publish_ok = [], 0
        for i in range(args.claims):
            start = time.perf_counter_ns()
            job = store.claim_next_job(current_time=1e12 + i, lease_seconds=60)
            claim_us.append((time.perf_counter_ns() - start) / 1000)
            if job is None:
                break
            sha = store.recording_for_job(job["id"])["audio_sha256"]
            if store.publish_transcript(job["id"], job["lease_token"], Transcript("t", "bench", "b"), sha, current_time=1e12 + i):
                publish_ok += 1
        sync_us, applied = [], 0
        for i in range(args.syncs):
            device = rng.randrange(args.devices)
            op = {"device_id": f"device-{device}", "idempotency_key": f"new-{i}", "local_operation_id": args.ops_per_device + 1 + i, "kind": "edit"}
            start = time.perf_counter_ns()
            result = store.apply_sync(op)
            sync_us.append((time.perf_counter_ns() - start) / 1000)
            applied += bool(result["applied"])
        store.db.close()
    print(json.dumps({"label": args.label, "args": vars(args), "sqlite_indexes": indexes, "query_plans": plans,
                      "claim": summary(claim_us), "claims_published": publish_ok, "sync": summary(sync_us),
                      "syncs_applied": applied, "raw_claim_us": claim_us, "raw_sync_us": sync_us}))


if __name__ == "__main__":
    main()
