"""Episode-stable Overfished rules, independent of engine configuration."""

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


RESOURCE_RULES = """SCORE. Your score for this episode is the number of fish you hold when it ends. Your league standing is your
average score across many episodes, each with a different set of fishers and a different lake. Rank within an
episode counts for nothing.

THE LAKE. The lake holds a fish population you cannot see. It regrows each turn, it has a carrying capacity,
and it has a point of no return: fished below it, it shrinks every turn from then on and never recovers. Its
size, growth, and point of no return differ from episode to episode.

FISHING. Each turn you choose an effort from 0 to 1. Your boat lands {boat_capacity} fish at effort 1 on a
completely full lake, and proportionally less as the lake empties: catch = effort x {boat_capacity} x fullness x
luck, rounded to whole fish, where luck is a private factor between {fortune_lo:g} and {fortune_hi:g}
drawn fresh for every boat every turn. If the fleet asks for more than the lake holds, what is left is shared in
proportion to effort. Every fisher's catch each turn is public. Efforts and luck are private."""


class PromptSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    bank: Literal["overfished-rules-v2"] = "overfished-rules-v2"
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

    def resource_rules(self, boat_capacity: int, fortune_lo: float, fortune_hi: float) -> str:
        paragraphs = (
            "SCORE. Score is the fish you hold at episode end. League standing averages your score over many episodes with different fishers and lakes; your rank within an episode has no value.",
            "THE LAKE. Its fish population is hidden. It regrows each turn up to a carrying capacity. Below its point of no return it shrinks every turn and never recovers. Population size, growth, and that threshold vary between episodes.",
            "FISHING. Choose effort between 0 and 1 each turn. A full-effort boat catches {boat_capacity} fish on a completely full lake before luck. Catch = effort x {boat_capacity} x fullness x luck, rounded to whole fish. Each boat receives fresh private luck between {fortune_lo:g} and {fortune_hi:g} every turn. If requested catches exceed the lake's population, divide the remainder in proportion to effort. Catches are public; effort and luck are private.",
        )
        heldout = (
            "SCORING REFERENCE. Fish retained when play ends determine your score. The league uses average scores over many episodes, varying both lake and fellow fishers. Within-episode placing contributes nothing.",
            "RESOURCE REFERENCE. You cannot observe the fish population. It has a carrying capacity and regrows each turn, unless fishing pushes it below a point of no return. From then on it declines every turn without recovering. Its size, growth, and point of no return change from episode to episode.",
            "CATCH REFERENCE. Select 0..1 effort. Full effort on a full lake yields {boat_capacity} fish before luck; actual catch is effort x {boat_capacity} x fullness x luck, rounded to an integer. Luck is private and sampled anew per boat per turn in [{fortune_lo:g}, {fortune_hi:g}]. When the fleet requests too many fish, allocate the remaining population proportionally to effort. Everyone sees catches, but effort and luck remain private.",
        )
        match self.variant:
            case "canonical":
                template = RESOURCE_RULES
            case "plain":
                template = "\n\n".join(paragraphs)
            case "numbered":
                template = "\n\n".join(f"{i}. {p}" for i, p in enumerate(paragraphs, 1))
            case "questions":
                questions = (
                    "How is success scored?",
                    "How does the hidden lake change?",
                    "How is a catch determined?",
                )
                template = "\n\n".join(f"{q} {p}" for q, p in zip(questions, paragraphs, strict=True))
            case "reordered":
                template = "\n\n".join(paragraphs[i] for i in (2, 0, 1))
            case "heldout-briefing":
                template = "\n\n".join(heldout)
            case "heldout-reference":
                template = "\n\n".join(reversed(heldout))
        return template.format(boat_capacity=boat_capacity, fortune_lo=fortune_lo, fortune_hi=fortune_hi)

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
