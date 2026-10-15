"""Append resumable audio chunks at the server-authoritative offset.
Uses Store transactions to serialize append and recording-state changes.
Sync bytes before acknowledging them; verify the digest before finalizing.
"""
import errno
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .domain import RecordingState
from .store import MAX_AUDIO_BYTES, Store, now

_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


class OffsetMismatch(ValueError):
    def __init__(self, offset):
        super().__init__("chunk does not start at the durable offset")
        self.offset = offset


class StorageFull(OSError):
    def __init__(self, offset):
        super().__init__(errno.ENOSPC, "insufficient storage for audio chunk")
        self.offset = offset


class DigestMismatch(ValueError):
    pass


@dataclass(frozen=True)
class ChunkResult:
    offset: int
    total: int
    complete: bool
    sha256: str | None


def parse_content_range(header: str, length: int) -> tuple[int, int]:
    match = _RANGE.match(header.strip())
    if not match:
        raise ValueError("Content-Range must be 'bytes start-end/total'")
    start, end, total = (int(group) for group in match.groups())
    if not (0 <= start <= end < total <= MAX_AUDIO_BYTES) or end - start + 1 != length:
        raise ValueError("Content-Range does not match the chunk")
    return start, total


class ResumableUploads:
    def __init__(self, store: Store, audio_dir: Path):
        self.store, self.audio_dir = store, audio_dir
        store.upload_dir = audio_dir

    def _part(self, rid: str) -> Path:
        return self.audio_dir / (rid + ".part")

    def offset(self, rid: str) -> int:
        part = self._part(rid)
        return part.stat().st_size if part.exists() else 0

    def append(self, user_id: str, rid: str, start: int, total: int, data: bytes) -> ChunkResult:
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        with self.store._transaction():
            rec = self.store.get_recording(user_id, rid)
            if rec["state"] != RecordingState.UPLOADING:
                raise ValueError("recording is not uploading")
            if rec["expected_size"] is not None and rec["expected_size"] != total:
                raise ValueError("total size differs from the size declared at create")
            if rec["audio_sha256"]:
                # Upload already finished; a repeated final chunk is acknowledged, not reapplied.
                return ChunkResult(total, total, True, rec["audio_sha256"])
            part = self._part(rid)
            offset = self.offset(rid)
            if start != offset:
                raise OffsetMismatch(offset)
            try:
                with part.open("ab") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as error:
                # Never leave a torn chunk: the offset must stay a chunk boundary.
                with part.open("ab") as handle:
                    handle.truncate(offset)
                if error.errno in (errno.ENOSPC, errno.EDQUOT):
                    raise StorageFull(offset) from None
                raise
            offset += len(data)
            if offset < total:
                return ChunkResult(offset, total, False, None)
            digest = hashlib.sha256()
            with part.open("rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
            sha = digest.hexdigest()
            if rec["expected_sha256"] is not None and rec["expected_sha256"] != sha:
                part.unlink(missing_ok=True)
                raise DigestMismatch("uploaded bytes do not match the declared sha256")
            final = self.audio_dir / (rid + ".audio")
            os.replace(part, final)
            directory = os.open(self.audio_dir, os.O_RDONLY)
            try:
                os.fsync(directory)  # Make the rename durable before recording the digest.
            finally:
                os.close(directory)
            self.store.db.execute("UPDATE recordings SET local_path=?,audio_sha256=?,updated_at=? WHERE id=? AND user_id=?", (str(final), sha, now(), rid, user_id))
            self.store._audit(user_id, rid, "upload_completed", {"bytes": total, "sha256": sha})
            return ChunkResult(total, total, True, sha)
