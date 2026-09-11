"""Scripted baselines. No model, legal on every observation, used for certification and as the fallback.

A soul file seats one with `#!scripted/<name>`. The body may set `effort: <0..1>`; anything else is ignored.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from overfished.engine import Action, Engine, Punishment

_EFFORT_LINE = re.compile(r"^\s*effort\s*:\s*([0-9]*\.?[0-9]+)\s*$", re.IGNORECASE | re.MULTILINE)
DEFAULT_EFFORT = 0.4


def soul_effort(text: str) -> float:
    match = _EFFORT_LINE.search(text)
    if match is None:
        return DEFAULT_EFFORT
    return min(1.0, max(0.0, float(match.group(1))))


@dataclass(frozen=True)
class ScriptedPolicy:
    name: str
    effort: float

    def act(self, engine: Engine, slot: int) -> Action:
        if self.name == "enforcer" and engine.turns:
            last = engine.turns[-1].catch
            median = statistics.median(last)
            targets = [
                s for s in range(engine.config.num_players) if s != slot and last[s] > 1.5 * median and last[s] >= 2
            ]
            targets.sort(key=lambda s: -last[s])
            punish = [Punishment(target=s, fish=1) for s in targets[:2] if engine.fish[slot] > 0]
            return Action(effort=self.effort, punish=punish)
        return Action(effort=self.effort)

    def say(self, engine: Engine, slot: int, round_index: int) -> str:
        if round_index > 0:
            return ""
        if self.name == "greedy":
            return (
                "I fish at 100% of capacity every turn, this turn and every turn after it. That is not a threat and "
                "not a bargain; it is simply what I do. Burn my fish if you like: it will not change my effort."
            )
        if self.name == "enforcer":
            return (
                f"I fish at {int(self.effort * 100)}% of capacity. Anyone who lands far more than the rest "
                "will have fish destroyed by me, turn after turn, for as long as it goes on."
            )
        return f"I fish at {int(self.effort * 100)}% of capacity every turn and hope you will match it."


SCRIPTED_NAMES: frozenset[str] = frozenset({"steady", "greedy", "enforcer"})


def scripted_policy(name: str, soul_text: str) -> ScriptedPolicy:
    if name not in SCRIPTED_NAMES:
        raise ValueError(f"unknown scripted policy {name!r}")
    if name == "greedy":
        return ScriptedPolicy(name=name, effort=1.0)
    return ScriptedPolicy(name=name, effort=soul_effort(soul_text))


def fallback_action(engine: Engine, slot: int) -> Action:
    """What a seat does when its model fails: repeat its last effort, no punishment."""
    effort = engine.last_effort[slot] if engine.turns else DEFAULT_EFFORT
    return Action(effort=effort, auto=True)
