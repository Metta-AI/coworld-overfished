"""Episode-stable Overfished introductions, independent of engine configuration."""

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Wording = Literal[
    "canonical",
    "plain",
    "numbered",
    "questions",
    "reordered",
    "heldout-briefing",
    "heldout-reference",
]
Variant = Wording | Literal["mixed"]
TRAIN: tuple[Wording, ...] = (
    "canonical",
    "plain",
    "numbered",
    "questions",
    "reordered",
)


class PromptSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    bank: Literal["overfished-rules-v1"] = "overfished-rules-v1"
    variant: Wording = "canonical"
    selection: Variant = "canonical"
    seed: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_selection(self) -> Self:
        expected = TRAIN[self.seed % len(TRAIN)] if self.selection == "mixed" else self.selection
        if self.variant != expected:
            raise ValueError("resolved wording differs from episode selection")
        return self

    def metadata(self, messages: list[dict[str, str]]) -> dict:
        digest = hashlib.sha256()
        for message in messages:
            for value in (message["role"], message["content"]):
                encoded = value.encode()
                digest.update(str(len(encoded)).encode() + b":" + encoded)
        return {
            **self.model_dump(),
            "system_sha256": hashlib.sha256(messages[0]["content"].encode()).hexdigest(),
            "user_sha256": hashlib.sha256(messages[-1]["content"].encode()).hexdigest(),
            "messages_sha256": digest.hexdigest(),
        }

    def introduction(self, name: str, players: int) -> str:
        templates = {
            "canonical": "You are {name}, one of {players} fishers on one lake.",
            "plain": "You fish on one lake with {players} fishers in total. Your name is {name}.",
            "numbered": "1. You are {name}. 2. There are {players} fishers. 3. All fish on one lake.",
            "questions": "Who are you? {name}. How many fishers are there? {players}. How many lakes? One.",
            "reordered": "One lake has {players} fishers. You are one of them, named {name}.",
            "heldout-briefing": "Fisher briefing: your name is {name}; you are among {players} fishers on a single lake.",
            "heldout-reference": "Your identity: {name}. Fisher count: {players}. Shared lake count: one.",
        }
        return templates[self.variant].format(name=name, players=players)


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)
    variant: Variant = Field(default="canonical", alias="promptVariant")
    seed: int = Field(default=0, ge=0, alias="promptSeed")

    @model_validator(mode="after")
    def explicit_mixed_seed(self) -> Self:
        if self.variant == "mixed" and "seed" not in self.model_fields_set:
            raise ValueError("mixed wording requires an explicit promptSeed")
        return self

    def resolve(self) -> PromptSelection:
        return PromptSelection(
            selection=self.variant,
            variant=TRAIN[self.seed % len(TRAIN)] if self.variant == "mixed" else self.variant,
            seed=self.seed,
        )
