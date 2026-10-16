import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("live", Path(__file__).resolve().parents[2] / "public-demo/live.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)
BASE = {"chunks": 4, "chunk_bytes": 256, "seed": 7, "retry_chunk": 0, "restart_after": 1, "corrupt": False}


def test_actual_resume_retry_restart_and_digest():
    result = live.run_upload(BASE)
    assert result["completed"]
    assert result["expected_sha256"] == result["recorded_sha256"]
    assert any(row["event"].startswith("Retry refused") for row in result["timeline"])
    assert any(row["event"].startswith("Reopened") for row in result["timeline"])
    changed = live.run_upload({**BASE, "seed": 12, "chunks": 8})
    assert changed["total_bytes"] != result["total_bytes"]
    assert changed["recorded_sha256"] != result["recorded_sha256"]


def test_digest_rejection_and_final_retry():
    rejected = live.run_upload({**BASE, "corrupt": True})
    assert not rejected["completed"] and rejected["recorded_sha256"] is None
    assert rejected["timeline"][-1]["offset"] == 0
    final = live.run_upload({**BASE, "retry_chunk": 3})
    assert any(row["event"] == "Retry acknowledged without appending" for row in final["timeline"])


@pytest.mark.parametrize("patch", [{"chunks": 13}, {"chunks": True}, {"seed": -1}, {"chunk_bytes": 2048}, {"retry_chunk": 4}, {"corrupt": 1}, {"url": "http://localhost"}])
def test_bounds(patch):
    with pytest.raises(ValueError):
        live.run_upload({**BASE, **patch})
