"""Private append-only policy memory for local and hosted episodes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCRATCHPAD_MAX_BYTES = 32768
SCRATCHPAD_NOTE_BYTES = 2048


def policy_id(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ScratchpadStore:
    """Local append-only history. Hosted history and compaction belong to the platform."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, identifier: str) -> Path:
        digest = identifier.removeprefix("sha256:")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("scratchpad key must be a SHA-256 policy identifier")
        return self.root / (digest + ".jsonl")

    def read(self, identifier: str) -> str:
        path = self._path(identifier)
        if not path.exists():
            return ""
        with path.open("rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            # JSON escaping can expand a 2 KiB contribution sixfold. Read a bounded tail.
            start = max(0, path.stat().st_size - 20 * (6 * SCRATCHPAD_NOTE_BYTES + 3))
            handle.seek(start)
            if start:
                handle.readline()
            notes = [json.loads(line) for line in handle.readlines()[-20:]]
        selected = []
        remaining = SCRATCHPAD_MAX_BYTES
        for note in reversed(notes):
            size = len(note.encode("utf-8"))
            if size > remaining:
                break
            selected.append(note)
            remaining -= size
        return "".join(reversed(selected))

    def append(self, identifier: str, text: str) -> None:
        if len(text.encode("utf-8")) > SCRATCHPAD_NOTE_BYTES:
            raise ValueError("episode memory contribution exceeds 2048 UTF-8 bytes")
        fd = os.open(self._path(identifier), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(text, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


class MemoryView(BaseModel):
    summary: str
    notes: list[str] = Field(max_length=20)


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["append-v1"]
    namespace: str
    policies: dict[str, MemoryView]

    @field_validator("policies")
    @classmethod
    def bounded_views(cls, policies: dict[str, MemoryView]) -> dict[str, MemoryView]:
        for view in policies.values():
            if sum(len(text.encode()) for text in [view.summary, *view.notes]) > 32768:
                raise ValueError("memory input exceeds 32 KiB per policy")
        return policies


class HostedScratchpadStore:
    """The platform owns persistence. An episode reads a snapshot and publishes only new notes."""

    def __init__(self, input_uri: str, output_uri: str):
        self.snapshot = MemoryInput.model_validate_json(Path(urlparse(input_uri).path).read_bytes())
        self.output_uri = output_uri
        self.notes: dict[str, str] = {}

    def read(self, identifier: str) -> str:
        view = self.snapshot.policies[identifier]
        return json.dumps(view.model_dump(), ensure_ascii=False)

    def append(self, identifier: str, text: str) -> None:
        if identifier not in self.snapshot.policies:
            raise ValueError("policy is absent from episode memory snapshot")
        previous = self.notes[identifier] if identifier in self.notes else ""
        combined = previous + text
        if len(combined.encode("utf-8")) > 2048:
            raise ValueError("episode memory contribution exceeds 2048 UTF-8 bytes")
        self.notes[identifier] = combined

    def flush(self) -> None:
        Path(urlparse(self.output_uri).path).write_text(
            json.dumps({"protocol": "append-v1", "notes": self.notes}), encoding="utf-8"
        )
