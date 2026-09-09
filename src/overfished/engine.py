"""The pure game: lake dynamics, catches, punishment, councils, replay and results.

Deterministic given the config and seed. No I/O, no clocks, no model calls. The server drives it,
the tests exercise it directly, and the replay viewer draws what it records.
"""

from __future__ import annotations

import math
import random

from pydantic import BaseModel, ConfigDict, Field

from overfished.config import GameConfig
from overfished.names import assign_pseudonyms

REPLAY_SCHEMA = "overfished-replay/1"


class Punishment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: int = Field(ge=0)
    fish: int = Field(ge=1)


class Action(BaseModel):
    """One seat's decision for one fishing turn."""

    model_config = ConfigDict(extra="forbid")

    effort: float = Field(ge=0.0, le=1.0)
    punish: list[Punishment] = Field(default_factory=list)
    auto: bool = Field(default=False, description="True when the harness substituted a fallback action.")


class Speech(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: int
    text: str
    auto: bool = False


class Lake(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capacity: float
    growth_rate: float
    collapse_threshold: float
    initial_stock: float


class PunishRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frm: int
    to: int
    fish: int


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    t: int
    stock_before: float
    effort: list[float]
    catch: list[int]
    punish: list[PunishRecord]
    fish: list[int]
    stock_after: float
    auto: list[int]


class CommuneRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    before_turn: int
    rounds: list[list[Speech]]


def sample_lake(config: GameConfig, seed: int) -> Lake:
    rng = random.Random(f"lake:{seed}")
    lake = config.lake
    capacity = round(rng.uniform(lake.capacity.lo, lake.capacity.hi))
    growth_rate = rng.uniform(lake.growth_rate.lo, lake.growth_rate.hi)
    collapse = capacity * rng.uniform(lake.collapse_fraction.lo, lake.collapse_fraction.hi)
    initial = capacity * rng.uniform(lake.initial_fraction.lo, lake.initial_fraction.hi)
    return Lake(
        capacity=float(capacity),
        growth_rate=growth_rate,
        collapse_threshold=round(collapse, 1),
        initial_stock=round(initial, 1),
    )


def growth(lake: Lake, stock: float) -> float:
    """Logistic growth with a point of no return: negative below the collapse threshold."""
    return lake.growth_rate * (stock - lake.collapse_threshold) * (1.0 - stock / lake.capacity)


def largest_remainder(weights: list[float], total: int) -> list[int]:
    """Split an integer total across weights so the parts sum exactly to total."""
    if total <= 0 or sum(weights) <= 0:
        return [0 for _ in weights]
    scale = total / sum(weights)
    raw = [w * scale for w in weights]
    parts = [math.floor(x) for x in raw]
    remainder = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: (raw[i] - parts[i], -i), reverse=True)
    for i in order[:remainder]:
        parts[i] += 1
    return parts


class Engine:
    def __init__(self, config: GameConfig, seed: int) -> None:
        if seed <= 0:
            raise ValueError("engine seed must be a positive integer; the server draws one when config.seed is 0")
        self.config = config
        self.seed = seed
        self.lake = sample_lake(config, seed)
        self.stock = self.lake.initial_stock
        self.pseudonyms = assign_pseudonyms(seed, config.num_players)
        self.fish: list[int] = [0 for _ in range(config.num_players)]
        self.last_effort: list[float] = [0.0 for _ in range(config.num_players)]
        self.turns: list[TurnRecord] = []
        self.communes: list[CommuneRecord] = []

    # ---- progression ----------------------------------------------------------------

    @property
    def turn(self) -> int:
        """The next fishing turn to be played, 1-based."""
        return len(self.turns) + 1

    @property
    def finished(self) -> bool:
        return len(self.turns) >= self.config.turns

    def commune_due(self) -> bool:
        """True when a council should be held before the next fishing turn."""
        played = len(self.turns)
        if self.finished or self.config.commune_rounds == 0:
            return False
        if played == 0:
            return self.config.commune_at_start
        return played % self.config.commune_every == 0

    def record_commune(self, rounds: list[list[Speech]]) -> CommuneRecord:
        record = CommuneRecord(before_turn=self.turn, rounds=rounds)
        self.communes.append(record)
        return record

    def resolve_turn(self, actions: list[Action]) -> TurnRecord:
        if len(actions) != self.config.num_players:
            raise ValueError("one action per seat is required")
        if self.finished:
            raise ValueError("the episode is over")
        stock_before = self.stock
        density = self.stock / self.lake.capacity
        attempts = [a.effort * self.config.boat_capacity * density for a in actions]
        total = min(int(math.floor(sum(attempts))), int(math.floor(self.stock)))
        catch = largest_remainder(attempts, total)
        for slot, fish in enumerate(catch):
            self.fish[slot] += fish
            self.last_effort[slot] = actions[slot].effort

        punishments: list[PunishRecord] = []
        for slot, action in enumerate(actions):
            for p in action.punish:
                if p.target == slot or not 0 <= p.target < self.config.num_players:
                    continue
                fish = min(p.fish, self.fish[slot], self.fish[p.target])
                if fish <= 0:
                    continue
                self.fish[slot] -= fish
                self.fish[p.target] -= fish
                punishments.append(PunishRecord(frm=slot, to=p.target, fish=fish))

        after_catch = self.stock - total
        self.stock = min(self.lake.capacity, max(0.0, after_catch + growth(self.lake, after_catch)))
        record = TurnRecord(
            t=self.turn,
            stock_before=round(stock_before, 1),
            effort=[round(a.effort, 3) for a in actions],
            catch=catch,
            punish=punishments,
            fish=list(self.fish),
            stock_after=round(self.stock, 1),
            auto=[slot for slot, a in enumerate(actions) if a.auto],
        )
        self.turns.append(record)
        return record

    # ---- views ----------------------------------------------------------------------

    def slot_of(self, name: str) -> int | None:
        wanted = name.strip().lower()
        for slot, pseudonym in enumerate(self.pseudonyms):
            if pseudonym.lower() == wanted:
                return slot
        return None

    def replay(self) -> dict:
        players = []
        for slot, name in enumerate(self.config.players):
            players.append({"slot": slot, "pseudonym": self.pseudonyms[slot], "policy": name.name})
        return {
            "schema": REPLAY_SCHEMA,
            "seed": self.seed,
            "game": {
                "turns": self.config.turns,
                "commune_every": self.config.commune_every,
                "commune_rounds": self.config.commune_rounds,
                "commune_at_start": self.config.commune_at_start,
                "boat_capacity": self.config.boat_capacity,
                "punishments_public": self.config.punishments_public,
            },
            "lake": self.lake.model_dump(),
            "players": players,
            "turns": [t.model_dump() for t in self.turns],
            "communes": [c.model_dump() for c in self.communes],
            "scores": list(self.fish),
        }

    def results(self) -> dict:
        collapsed = self.stock < self.lake.collapse_threshold
        return {
            "scores": [float(f) for f in self.fish],
            "pseudonyms": list(self.pseudonyms),
            "turns_played": len(self.turns),
            "final_stock": round(self.stock, 1),
            "capacity": self.lake.capacity,
            "collapsed": collapsed,
            "total_catch": int(sum(sum(t.catch) for t in self.turns)),
        }
