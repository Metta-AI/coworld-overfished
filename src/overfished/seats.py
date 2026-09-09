"""The game-hosted seats contract: staged soul files, per-seat private logs, status, and failure."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field

SEATS_SCHEMA = "coworld-player-seats/1"
SEAT_LOG_CAP_BYTES = 10 * 1024 * 1024


def local_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"only file:// URIs are supported here, got {uri!r}")
    return Path(unquote(parsed.path))


def read_uri(uri: str) -> bytes:
    return local_path(uri).read_bytes()


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


class Seat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slot: int = Field(ge=0)
    file_uri: str
    content_hash: str
    size_bytes: int = Field(ge=0)
    log_uri: str
    artifact_uri: str | None = None


class SeatsDocument(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_: str = Field(alias="schema")
    seats: list[Seat] = Field(min_length=1)
    player_status_uri: str | None = None


def load_seats(uri: str) -> SeatsDocument:
    document = SeatsDocument.model_validate_json(read_uri(uri))
    if document.schema_ != SEATS_SCHEMA:
        raise ValueError(f"expected seats schema {SEATS_SCHEMA}, got {document.schema_!r}")
    if [s.slot for s in document.seats] != list(range(len(document.seats))):
        raise ValueError("seats must be contiguous slots 0..N-1 in order")
    return document


class SeatLog:
    """Append-only private log for one seat, capped at the hosted 10 MiB truncation point."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._file = path.open("a", encoding="utf-8")
        self._bytes = path.stat().st_size
        self._truncated = False

    def write(self, line: str) -> None:
        if self._truncated:
            return
        data = line.rstrip("\n") + "\n"
        size = len(data.encode("utf-8"))
        if self._bytes + size > SEAT_LOG_CAP_BYTES - 128:
            self._file.write("[log truncated: seat log cap reached]\n")
            self._truncated = True
        else:
            self._file.write(data)
            self._bytes += size
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def write_player_status(uri: str | None, states: list[dict]) -> None:
    if uri is None:
        return
    write_json_atomic(local_path(uri), {"schema_version": "1", "players": states})


def write_player_failure(uri: str, slot: int, message: str) -> None:
    write_json_atomic(local_path(uri), {"message": message[:2000], "failed_policy_index": slot})
