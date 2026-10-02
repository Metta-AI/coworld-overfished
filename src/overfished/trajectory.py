"""Private engine-owned decision records in the Coworld training wire format."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    attempt_id: str = Field(default_factory=lambda: str(uuid4()))
    platform_call_id: UUID | None = None
    policy: str
    origin: Literal["model", "teacher", "unknown"] = "model"
    inference_mode: Literal["text_action", "speech", "memory"]
    prompt: JsonValue
    request: JsonValue | None = None
    raw_response: JsonValue | None = None
    response: str | None = None
    model: str | None = None
    model_identity: str | None = None
    tokenizer_identity: str | None = None
    chat_template_sha256: str | None = None
    decoder: JsonValue | None = None
    prompt_token_ids: list[int] | None = None
    sampled_token_ids: list[int] | None = None
    behavior_logprobs: list[float] | None = None
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    parsed_action: JsonValue | None = None
    accepted: bool = False
    rejection_reason: str | None = "model call did not produce an applied action"


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    model_config = ConfigDict(extra="forbid")

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
        self.records: list[DecisionRecord | EpisodeRecord] = []
        self.finished = False

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
        if any(
            isinstance(record, DecisionRecord) and record.decision_id == decision_id
            for record in self.records
        ):
            raise ValueError("duplicate decision ID")
        if len({attempt.attempt_id for attempt in attempts}) != len(attempts):
            raise ValueError("duplicate attempt ID")
        selected = [attempt for attempt in attempts if attempt.accepted]
        if fallback_origin is None:
            if len(selected) != 1 or selected[0].parsed_action != executed_action:
                raise ValueError("selected parsed proposal must equal the independently executed action")
        elif selected:
            raise ValueError("fallback cannot retain an accepted model target")
        self.records.append(
            DecisionRecord(
                episode_id=self.episode_id,
                decision_id=decision_id,
                decision_index=len(self.records),
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
        )

    def finish(self, *, outcome: JsonValue, participant_outcomes: JsonValue, completed: bool) -> None:
        if self.finished:
            raise ValueError("episode has already ended")
        self.records.append(
            EpisodeRecord(
                episode_id=self.episode_id,
                seed_family=self.seed_family,
                game_version=self.game_version,
                source_revision=self.source_revision,
                image_digest=self.image_digest,
                status="completed" if completed else "truncated",
                outcome=outcome,
                participant_outcomes=participant_outcomes,
            )
        )
        self.finished = True

    def write(self, destination: Path) -> None:
        if not self.finished:
            raise ValueError("private artifact requires a terminal summary")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            for record in self.records:
                handle.write(record.model_dump_json() + "\n")
