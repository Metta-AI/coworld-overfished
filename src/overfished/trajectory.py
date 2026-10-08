"""Private engine-owned decision records in the Coworld training wire format."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, StrictInt, model_validator

from overfished.engine import CommuneRecord, Lake, TurnRecord


class Attempt(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_assignment=True, allow_inf_nan=False, hide_input_in_errors=True
    )

    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    platform_call_id: UUID | None = None
    policy: str
    origin: Literal["model", "teacher", "unknown"] = "model"
    inference_mode: Literal["text_action", "speech", "memory"]
    prompt: JsonValue
    request: JsonValue | None = None
    raw_response: str | None = None
    response_body_b64: str | None = None
    response_headers_b64: str | None = None
    response_headers: dict[str, str] | None = None
    provider_request_id: str | None = None
    response_complete: StrictBool | None = None
    response_reader_joined: StrictBool | None = None
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    response: str | None = None
    model: str | None = None
    model_identity: str | None = None
    tokenizer_identity: str | None = None
    chat_template_sha256: str | None = None
    decoder: JsonValue | None = None
    prompt_token_ids: list[StrictInt] | None = None
    sampled_token_ids: list[StrictInt] | None = None
    behavior_logprobs: list[float] | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    parsed_action: JsonValue | None = None
    accepted: bool = False
    rejection_reason: str | None = "model call did not produce an applied action"

    @model_validator(mode="after")
    def actual_received_body(self) -> Attempt:
        if self.response_body_b64 is not None:
            raw = base64.b64decode(self.response_body_b64, validate=True)
            if self.raw_response is not None and raw != self.raw_response.encode("utf-8"):
                raise ValueError("received body differs from exact response text")
        if self.response_headers_b64 is not None:
            base64.b64decode(self.response_headers_b64, validate=True)
        return self


class PendingAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    seat: str
    attempt: Attempt


class AuxiliaryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    schema_version: Literal["1"] = "1"
    episode_id: str
    source_revision: str
    status: Literal["pending_auxiliary", "joined_auxiliary"]
    attempts: list[PendingAttempt]


class EngineEffects(BaseModel):
    """Actual engine transitions, separate from the controls submitted by each policy."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    lake: Lake
    turn_limit: int
    turns: list[TurnRecord]
    councils: list[CommuneRecord]


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    schema_version: Literal["1"] = "1"
    event_type: Literal["decision"] = "decision"
    episode_id: str
    decision_id: str
    decision_index: int
    game: Literal["overfished"] = "overfished"
    game_version: str
    source_revision: str
    image_digest: str | None = None
    seat: str
    visibility: Literal["private"] = "private"
    observation: JsonValue
    prompt: JsonValue
    attempts: list[Attempt]
    selected_attempt_id: str | None
    executed_action: JsonValue
    action_status: Literal["accepted", "fallback"]
    fallback_origin: str | None
    terminal: bool


class EpisodeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    schema_version: Literal["1"] = "1"
    event_type: Literal["episode"] = "episode"
    episode_id: str
    seed_family: str
    game: Literal["overfished"] = "overfished"
    game_version: str
    source_revision: str
    image_digest: str | None = None
    status: Literal["completed", "truncated"]
    outcome: JsonValue
    participant_outcomes: JsonValue


class Trajectory:
    def __init__(
        self,
        *,
        episode_id: str,
        game_version: str,
        source_revision: str,
        seed_family: str,
        image_digest: str | None,
        destination: Path,
    ):
        if not episode_id or not game_version or re.fullmatch(r"[a-f0-9]{40}", source_revision) is None:
            raise ValueError(
                "private trajectories require episode, game version, and full source commit pins"
            )
        self.episode_id = episode_id
        self.game_version = game_version
        self.source_revision = source_revision
        self.seed_family = seed_family
        self.image_digest = image_digest
        self.destination = destination
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pending_path = destination.with_name(destination.name + ".pending")
        self.decisions_path = destination.with_name(destination.name + ".decisions.pending")
        self.header_pairs_path = destination.with_name(destination.name + ".headers.private.jsonl")
        self.spool = os.fdopen(os.open(self.decisions_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w")
        self.decision_count = 0
        self.decision_ids: set[str] = set()
        self.attempt_ids: set[str] = set()
        self.call_ids: set[UUID] = set()
        self.pending: dict[str, PendingAttempt] = {}
        self.recorded_header_pairs: set[str] = set()
        self.episode: EpisodeRecord | None = None
        self.finished = False

    def observe(self, slot: int, attempt: Attempt, pairs: list[tuple[bytes, bytes]]) -> None:
        if self.finished:
            raise ValueError("attempt progress cannot mutate a sealed episode")
        self.pending[attempt.attempt_id] = PendingAttempt(
            seat=str(slot), attempt=attempt.model_copy(deep=True)
        )
        self.persist_pending()
        if pairs and attempt.attempt_id not in self.recorded_header_pairs:
            descriptor = os.open(self.header_pairs_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(
                    {
                        "attempt_id": attempt.attempt_id,
                        "seat": str(slot),
                        "pairs": [
                            [base64.b64encode(name).decode("ascii"), base64.b64encode(value).decode("ascii")]
                            for name, value in pairs
                        ],
                    },
                    handle,
                )
                handle.write("\n")
            self.recorded_header_pairs.add(attempt.attempt_id)

    def persist_pending(self) -> None:
        record = AuxiliaryRecord(
            episode_id=self.episode_id,
            source_revision=self.source_revision,
            status="joined_auxiliary" if self.finished else "pending_auxiliary",
            attempts=list(self.pending.values()),
        )
        temporary = self.pending_path.with_name(self.pending_path.name + f".{uuid4()}.partial")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(record.model_dump_json() + "\n")
        os.replace(temporary, self.pending_path)

    def record(
        self,
        *,
        decision_id: str,
        seat: int,
        observation: JsonValue,
        prompt: JsonValue,
        attempts: list[Attempt],
        executed_action: JsonValue,
        fallback_origin: str | None,
        terminal: bool,
    ) -> None:
        if self.finished:
            raise ValueError("cannot record after episode completion")
        if decision_id in self.decision_ids:
            raise ValueError("duplicate decision ID")
        if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
            raise ValueError("duplicate attempt ID")
        for attempt in attempts:
            if attempt.attempt_id in self.attempt_ids:
                raise ValueError("attempt cannot belong to multiple engine decisions")
            if attempt.platform_call_id is not None and attempt.platform_call_id in self.call_ids:
                raise ValueError("native call cannot belong to multiple attempts")
            if attempt.accepted and (
                attempt.response_reader_joined is False or attempt.response_complete is False
            ):
                raise ValueError("unresolved or incomplete native response cannot be an accepted action")
        selected = [attempt for attempt in attempts if attempt.accepted]
        if fallback_origin is None:
            if len(selected) != 1 or selected[0].parsed_action != executed_action:
                raise ValueError("selected parsed proposal must equal the independently executed action")
        elif selected:
            raise ValueError("fallback cannot retain an accepted model target")
        record = DecisionRecord(
            episode_id=self.episode_id,
            decision_id=decision_id,
            decision_index=self.decision_count,
            game_version=self.game_version,
            source_revision=self.source_revision,
            seat=str(seat),
            image_digest=self.image_digest,
            observation=observation,
            prompt=prompt,
            attempts=attempts,
            selected_attempt_id=selected[0].attempt_id if selected else None,
            executed_action=executed_action,
            action_status="fallback" if fallback_origin else "accepted",
            fallback_origin=fallback_origin,
            terminal=terminal,
        )
        self.spool.write(record.model_dump_json() + "\n")
        self.spool.flush()
        self.decision_count += 1
        self.decision_ids.add(decision_id)
        for attempt in attempts:
            self.attempt_ids.add(attempt.attempt_id)
            if attempt.platform_call_id is not None:
                self.call_ids.add(attempt.platform_call_id)
            self.pending.pop(attempt.attempt_id, None)
        self.persist_pending()

    def finish(self, *, outcome: JsonValue, participant_outcomes: JsonValue, completed: bool) -> None:
        if self.finished:
            raise ValueError("episode has already ended")
        self.episode = EpisodeRecord(
            episode_id=self.episode_id,
            seed_family=self.seed_family,
            game_version=self.game_version,
            source_revision=self.source_revision,
            image_digest=self.image_digest,
            status="completed" if completed else "truncated",
            outcome=outcome,
            participant_outcomes=participant_outcomes,
        )
        self.spool.close()
        self.finished = True
        self.persist_pending()

    def write(self) -> None:
        destination = self.destination
        if not self.finished or self.episode is None:
            raise ValueError("private artifact requires a terminal summary after writer join")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write('{"schema_version":"1","episode":')
            handle.write(self.episode.model_dump_json())
            handle.write(',"decisions":[')
            with self.decisions_path.open() as decisions:
                for index, line in enumerate(decisions):
                    if index:
                        handle.write(",")
                    handle.write(line.rstrip("\n"))
            handle.write("]}\n")
