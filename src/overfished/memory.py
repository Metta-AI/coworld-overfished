"""Private, durable policy scratchpads on a shared filesystem."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from pathlib import Path

SCRATCHPAD_MAX_BYTES = 1_000_000


def policy_id(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ScratchpadStore:
    """One namespace per pool. Mount this directory durably for hosted episodes.

    Concurrent appends are merged; replacement requires an unchanged start snapshot.
    Locks and atomic replacement prevent partial writes and lost updates.
    """

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, identifier: str) -> Path:
        digest = identifier.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("scratchpad key must be a SHA-256 policy identifier")
        return self.root / (digest + ".txt")

    def read(self, identifier: str) -> str:
        path = self._path(identifier)
        try:
            with path.open("rb") as handle:
                data = handle.read(SCRATCHPAD_MAX_BYTES + 1)
        except FileNotFoundError:
            return ""
        if len(data) > SCRATCHPAD_MAX_BYTES:
            raise ValueError("scratchpad exceeds 1 MB")
        return data.decode("utf-8")

    def write(self, identifier: str, snapshot: str, text: str, *, append: bool = False) -> None:
        path = self._path(identifier)
        lock_path = path.with_suffix(".lock")
        with lock_path.open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            current = self.read(identifier)
            if not append and current != snapshot:
                raise ValueError("scratchpad changed during this episode; replacement skipped")
            data = (current + text if append else text).encode("utf-8")
            if len(data) > SCRATCHPAD_MAX_BYTES:
                raise ValueError("scratchpad update exceeds 1 MB; previous contents retained")
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix=".scratchpad-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
