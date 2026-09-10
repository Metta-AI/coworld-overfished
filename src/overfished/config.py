"""Game configuration: the concrete JSON the runner hands the game at COGAME_CONFIG_URI.

Everything here is public in the sense that it is visible in the Coworld manifest. What players
never see in-game is the *sampled* lake (capacity, growth rate, collapse threshold), which the engine
draws from the ranges below using the episode seed.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Short names a soul file may put on its first line instead of a full OpenRouter slug.
# Values are canonical OpenRouter slugs (vendor/model); the hosted sidecar admits any canonical slug.
DEFAULT_MODEL_ALIASES: dict[str, str] = {
    "opus": "anthropic/claude-opus-5",
    "sonnet": "anthropic/claude-sonnet-5",
    "haiku": "anthropic/claude-haiku-4.5",
    "fable": "anthropic/claude-fable-5.1",
    "kimi": "moonshotai/kimi-k3",
    "sol": "openai/gpt-5.6-sol",
    "luna": "openai/gpt-5.6-luna",
    "terra": "openai/gpt-5.6-terra",
    "gemini": "google/gemini-3.8-flash",
    "grok": "x-ai/grok-4.6",
    "deepseek": "deepseek/deepseek-v4-pro",
    "glm": "z-ai/glm-5.3",
    "qwen": "qwen/qwen3-max-thinking",
    "minimax": "minimax/minimax-m3",
}


class PlayerName(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


class Range(BaseModel):
    """A closed interval the engine samples uniformly from, per episode seed."""

    model_config = ConfigDict(extra="forbid")

    lo: float
    hi: float

    @model_validator(mode="after")
    def ordered(self) -> "Range":
        if self.hi < self.lo:
            raise ValueError("range hi must be >= lo")
        return self


class LakeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capacity: Range = Field(default=Range(lo=600, hi=1400), description="Carrying capacity K in fish.")
    boat_ratio: Range = Field(
        default=Range(lo=38, hi=44),
        description=(
            "K divided by one boat's full-lake catch. Sampled with K so the fleet-to-lake ratio, which decides how "
            "many full-effort boats the lake absorbs, stays inside the tuned band while the absolute numbers vary."
        ),
    )
    growth_rate: Range = Field(default=Range(lo=0.32, hi=0.38), description="Intrinsic growth r per turn.")
    collapse_fraction: Range = Field(
        default=Range(lo=0.23, hi=0.27),
        description=(
            "Point of no return as a fraction of K. Below it the stock shrinks every turn. The maximum sustainable "
            "fleet effort is r(1-sqrt(a))^2 of K per turn, so a and r together set how many full-effort boats the "
            "lake absorbs: with these defaults one among 30% moderates, not three."
        ),
    )
    initial_fraction: Range = Field(default=Range(lo=0.6, hi=0.9), description="Starting stock as a fraction of K.")


class LlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    think_turns: int = Field(default=1, ge=0, le=6, description="Private reasoning replies allowed before each action.")
    timeout_seconds: float = Field(default=60.0, gt=0, description="Per model call. Opus-class replies take 10 to 20s on OpenRouter.")
    decision_seconds: float = Field(
        default=75.0, gt=0, description="Whole decision incl. thinking turns and retries; past it the seat plays the fallback."
    )
    max_output_tokens: int = Field(default=4000, ge=128, le=16000, description="Covers hidden reasoning plus the JSON reply for reasoning models.")
    notebook_max_chars: int = Field(default=1500, ge=0, le=8000, description="Private notes carried across turns.")
    say_max_chars: int = Field(default=500, ge=1, le=4000, description="One council message.")
    max_calls_per_decision: int = Field(default=4, ge=1, le=10, description="Hard cap on calls per decision incl. retries.")
    reasoning: dict[str, object] = Field(
        default_factory=lambda: {"effort": "low"},
        description=(
            "OpenRouter `reasoning` parameter sent with every call. Bounds a reasoning model's hidden thinking so it "
            "cannot spend the whole output budget before writing its JSON; the seat's visible `thinking` field is "
            "where deliberation is meant to go. An empty object sends nothing."
        ),
    )


class GameConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: list[str] = Field(min_length=2, max_length=16, description="Runner-injected, one per seat.")
    players: list[PlayerName] = Field(min_length=2, max_length=16, description="One display name per seat.")
    seed: int = Field(default=0, ge=0, description="0 means draw a fresh random seed at startup.")
    turns: Range = Field(
        default=Range(lo=45, hi=75),
        description=(
            "Episode length in fishing turns, sampled per episode from this closed range. Seats are told the range, "
            "never the draw, so no turn is known to be the last."
        ),
    )
    commune_every: int = Field(default=5, ge=1, description="Hold a council after every N fishing turns.")
    commune_rounds: int = Field(default=2, ge=0, le=6, description="Speaking rounds per council, one fisher at a time; 0 disables talk.")
    commune_at_start: bool = Field(default=True, description="Hold an opening council before turn 1.")
    punish_ratio: int = Field(default=4, ge=1, description="Fish destroyed on the target for each fish the punisher burns.")
    history_turns: int = Field(default=10, ge=1, le=100, description="Recent turns shown in every observation.")
    punishments_public: bool = Field(default=True, description="Whether the ledger names who punished whom.")
    reveal_models: bool = Field(default=True, description="Put each seat's model in results and replay.")
    episode_wall_seconds: float = Field(default=1800.0, gt=0, description="LLM wall budget; past it seats go scripted.")
    lake: LakeConfig = Field(default_factory=LakeConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    model_aliases: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_MODEL_ALIASES))

    @model_validator(mode="after")
    def roster_sizes_match(self) -> "GameConfig":
        if len(self.tokens) != len(self.players):
            raise ValueError("tokens and players must have the same length")
        return self

    @property
    def num_players(self) -> int:
        return len(self.players)
