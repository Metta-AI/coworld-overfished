"""Headless Overfished decisions through the shared Coworld JSONL protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from overfished.config import GameConfig
from overfished.engine import Action, Engine, Gift, Punishment, Speech
from overfished.llm import (
    council_observation,
    extract_json,
    mechanics_block,
    parse_action,
    turn_observation,
)
from overfished.scripted import ScriptedPolicy

ROOT = Path(__file__).resolve().parents[1]
EFFORTS = (0.0, 0.25, 0.4, 0.75, 1.0)
SOCIAL = ("none", "punish_high", "gift_low")
MAX_SEATS = 16
CHOICES = tuple(
    {"choice": index, "effort": effort, "social": social}
    for index, (effort, social) in enumerate((effort, social) for effort in EFFORTS for social in SOCIAL)
)


def compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


class TrainingSession:
    def __init__(self, variant: str, mode: str, turns: int | None):
        manifest = json.loads((ROOT / "coworld_manifest_template.json").read_text())
        self.base_config = (
            manifest["certification"]["game_config"]
            if variant == "certification"
            else next(entry["game_config"] for entry in manifest["variants"] if entry["id"] == variant)
        )
        self.mode = mode
        self.turns = turns
        self.decision_id = 0

    def reset(self, request: dict[str, object]) -> dict[str, object]:
        players = int(request["players"])
        if players != len(self.base_config["players"]):
            raise ValueError(f"Variant requires {len(self.base_config['players'])} seats")
        seed = int.from_bytes(hashlib.sha256(str(request["seed"]).encode()).digest()[:8], "big") or 1
        config = dict(self.base_config)
        config.update(tokens=[f"training-{seat}" for seat in range(players)], seed=seed)
        if self.turns is not None:
            config["turns"] = {"lo": self.turns, "hi": self.turns}
        self.config = GameConfig.model_validate(config)
        self.engine = Engine(self.config, seed)
        self.decision_id = 0
        self.seat = 0
        self.actions: list[Action] = []
        self.earlier: list[list[Speech]] = []
        self.so_far: list[Speech] = []
        self.round_index = 0
        self.speaker_index = 0
        self.order = self.engine.council_order()
        self.phase = "council" if self.engine.commune_due() else "fishing"
        return self.observation()

    def view(self) -> dict[str, object]:
        engine = self.engine
        last = engine.turns[-1] if engine.turns else None
        return {
            "seat": self.seat,
            "pseudonym": engine.pseudonyms[self.seat],
            "turn": engine.turn,
            "fish": list(engine.fish),
            "last_catch": list(last.catch) if last else [0] * self.config.num_players,
            "own_last_effort": engine.last_effort[self.seat],
            "boat_capacity": engine.lake.boat_capacity,
            "turns_range": [int(self.config.turns.lo), int(self.config.turns.hi)],
            "commune_round": self.round_index if self.phase == "council" else None,
        }

    def messages(self) -> list[dict[str, str]]:
        system = mechanics_block(
            self.config,
            self.engine.pseudonyms[self.seat],
            self.config.num_players,
            self.engine.lake.boat_capacity,
        )
        if self.phase == "council":
            user = council_observation(
                self.engine, self.seat, "", self.round_index, self.order, self.earlier, self.so_far
            )
        else:
            user = turn_observation(self.engine, self.seat, "")
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def observation(self) -> dict[str, object]:
        if self.engine.finished:
            scores = {seat: float(score) for seat, score in enumerate(self.engine.results()["scores"])}
            scale = self.engine.lake.boat_capacity * self.config.turns.hi / self.config.num_players
            return {
                "kind": "terminal",
                "scores": scores,
                "utilities": {seat: score / (scale + score) for seat, score in scores.items()},
            }
        view = self.view()
        messages = self.messages()
        common = {
            "game": "overfished",
            "decision_id": self.decision_id,
            "seat": self.seat,
            "engine_seat": self.seat,
            "turn": self.engine.turn,
            "semantic_view": {**view, "observation": messages[1]["content"]},
            "inbox": [],
            "messages": messages,
            "speech_messages": messages if self.phase == "council" else [],
        }
        if self.phase == "council":
            return {"kind": "speech_turn", **common}
        if self.mode == "choice":
            question = {
                "state": common["semantic_view"],
                "instructions": "Choose fishing effort and one optional public social action.",
                "candidates": {
                    str(candidate["choice"]): {
                        "decision": {"choice": candidate["choice"]},
                        "criterion": {"effort": candidate["effort"], "social": candidate["social"]},
                    }
                    for candidate in CHOICES
                },
            }
            schema = {
                "type": "object",
                "properties": {"choice": {"type": "integer", "minimum": 0, "maximum": len(CHOICES) - 1}},
                "required": ["choice"],
            }
        else:
            question = None
            schema = {
                "type": "object",
                "properties": {
                    "effort": {"type": "number", "minimum": 0, "maximum": 1},
                    "punish": {"type": "array"},
                    "gift": {"type": "array"},
                },
                "required": ["effort"],
            }
        return {"kind": "decision", **common, "action_schema": schema, "typed_question": question}

    def encode(self) -> dict[str, object]:
        if self.mode != "choice" or self.phase != "fishing":
            raise ValueError("Numeric encoding requires a fishing choice")
        last = self.engine.turns[-1] if self.engine.turns else None
        fish = list(self.engine.fish) + [0] * (MAX_SEATS - self.config.num_players)
        catches = list(last.catch) + [0] * (MAX_SEATS - self.config.num_players) if last else [0] * MAX_SEATS
        values = [
            self.seat / MAX_SEATS,
            self.engine.turn / 120,
            self.engine.fish[self.seat] / (100 + self.engine.fish[self.seat]),
            self.engine.lake.boat_capacity / 100,
            self.engine.last_effort[self.seat],
            *(value / (100 + value) for value in fish),
            *(value / (50 + value) for value in catches),
        ]
        return {
            "decision_id": self.decision_id,
            "values": values,
            "actions": [{"choice": choice} for choice in range(len(CHOICES))],
        }

    def teacher(self) -> dict[str, str]:
        if self.phase == "council":
            speech = ScriptedPolicy("steady", 0.4).say(self.engine, self.seat, self.round_index)
            return {"response": speech}
        if self.mode == "choice":
            return {"response": compact({"choice": EFFORTS.index(0.4) * len(SOCIAL)})}
        return {"response": compact({"effort": 0.4, "punish": [], "gift": []})}

    def say(self, request: dict[str, object]) -> dict[str, object]:
        if self.phase != "council" or request["decision_id"] != self.decision_id:
            raise ValueError("Stale council turn")
        text = str(request["text"])[: self.config.llm.say_max_chars]
        self.so_far.append(Speech(slot=self.seat, text=text))
        self.decision_id += 1
        self.speaker_index += 1
        if self.speaker_index == self.config.num_players:
            self.earlier.append(self.so_far)
            self.so_far = []
            self.speaker_index = 0
            self.round_index += 1
            if self.round_index == self.config.commune_rounds:
                self.engine.record_commune(self.earlier, self.order)
                self.phase = "fishing"
                self.seat = 0
            else:
                self.seat = self.order[0]
        else:
            self.seat = self.order[self.speaker_index]
        return {"kind": "spoken", "text": text, "to": "public", "observation": self.observation()}

    def step(self, request: dict[str, object]) -> dict[str, object]:
        if self.phase != "fishing" or request["decision_id"] != self.decision_id:
            return {"kind": "rejected", "reason": "stale fishing decision"}
        reply = extract_json(str(request["response"]))
        if reply is None:
            return {"kind": "rejected", "reason": "response needs one JSON object"}
        if self.mode == "choice":
            if "choice" not in reply:
                return {"kind": "rejected", "reason": "response needs a choice"}
            choice = reply["choice"]
            if type(choice) is not int or not 0 <= choice < len(CHOICES):
                return {"kind": "rejected", "reason": "illegal choice"}
            effort = EFFORTS[choice // len(SOCIAL)]
            social = SOCIAL[choice % len(SOCIAL)]
            others = [seat for seat in range(self.config.num_players) if seat != self.seat]
            punish = (
                [Punishment(target=max(others, key=lambda seat: (self.engine.fish[seat], -seat)), fish=1)]
                if social == "punish_high"
                else []
            )
            gift = (
                [Gift(target=min(others, key=lambda seat: (self.engine.fish[seat], seat)), fish=1)]
                if social == "gift_low"
                else []
            )
            action = Action(effort=effort, punish=punish, gift=gift)
            accepted = {"choice": choice}
        else:
            parsed = parse_action(reply, self.engine, self.seat)
            if isinstance(parsed, str):
                return {"kind": "rejected", "reason": parsed}
            action = parsed
            accepted = action.model_dump(exclude={"auto"})
        self.actions.append(action)
        self.decision_id += 1
        self.seat += 1
        if self.seat == self.config.num_players:
            self.engine.resolve_turn(self.actions)
            self.actions = []
            if not self.engine.finished and self.engine.commune_due():
                self.phase = "council"
                self.order = self.engine.council_order()
                self.earlier = []
                self.so_far = []
                self.round_index = 0
                self.speaker_index = 0
                self.seat = self.order[0]
            else:
                self.seat = 0
        return {"kind": "accepted", "action": accepted, "observation": self.observation()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="certification")
    parser.add_argument("--mode", choices=("choice", "text"), default="choice")
    parser.add_argument("--turns", type=int)
    args = parser.parse_args()
    session = TrainingSession(args.variant, args.mode, args.turns)
    for line in sys.stdin:
        request = json.loads(line)
        match request["kind"]:
            case "reset":
                response = session.reset(request)
            case "encode":
                response = session.encode()
            case "teacher":
                response = session.teacher()
            case "say":
                response = session.say(request)
            case "step":
                response = session.step(request)
            case _:
                raise ValueError(f"Unknown training command {request['kind']}")
        print(compact(response), flush=True)


if __name__ == "__main__":
    main()
