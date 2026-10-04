"""Headless Overfished decisions through the shared Coworld JSONL protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from pydantic import BaseModel

from overfished.config import GameConfig
from overfished.engine import Action, Engine, Gift, Punishment, Speech
from overfished.llm import (
    InvalidReply,
    SeatBrain,
    SpeechReply,
    ThinkingReply,
    council_observation,
    extract_json,
    parse_decision_reply,
    scratchpad_read_observation,
    scratchpad_write_observation,
    seat_system_prompt,
    turn_observation,
)
from overfished.memory import SCRATCHPAD_NOTE_BYTES, ScratchpadStore, policy_id
from overfished.scripted import SCRIPTED_NAMES, ScriptedPolicy, ScriptedView, fallback_action, scripted_policy
from overfished.soul import parse_soul

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


class ResetRequest(BaseModel):
    seed: str
    players: int


class TrainingSession:
    def __init__(
        self, variant: str, mode: str, turns: int | None, souls: list[Path], scratchpad_dir: Path | None
    ):
        manifest = json.loads((ROOT / "coworld_manifest_template.json").read_text())
        self.base_config = (
            manifest["certification"]["game_config"]
            if variant == "certification"
            else next(entry["game_config"] for entry in manifest["variants"] if entry["id"] == variant)
        )
        self.mode = mode
        self.turns = turns
        self.decision_id = 0
        self.soul_data = [path.read_bytes() for path in souls]
        self.memory = ScratchpadStore(scratchpad_dir) if scratchpad_dir is not None else None

    def reset(self, request: dict[str, object]) -> dict[str, object]:
        reset = ResetRequest.model_validate(request)
        players = reset.players
        if players != len(self.base_config["players"]):
            raise ValueError(f"Variant requires {len(self.base_config['players'])} seats")
        if len(self.soul_data) != players:
            raise ValueError("one concrete soul artifact per training seat is required")
        seed = int.from_bytes(hashlib.sha256(reset.seed.encode()).digest()[:8], "big") or 1
        config = dict(self.base_config)
        config.update(tokens=[f"training-{seat}" for seat in range(players)], seed=seed)
        if self.turns is not None:
            config["turns"] = {"lo": self.turns, "hi": self.turns}
        self.config = GameConfig.model_validate(config)
        self.policy_ids = [policy_id(data) for data in self.soul_data]
        self.engine = Engine(self.config, seed, self.policy_ids)
        souls = [parse_soul(data, self.config.model_aliases, set(SCRIPTED_NAMES)) for data in self.soul_data]
        self.brains = [
            SeatBrain(
                slot=slot,
                soul=soul,
                system_prompt=seat_system_prompt(
                    self.engine, slot, soul, persistent_memory=self.memory is not None
                ),
            )
            for slot, soul in enumerate(souls)
        ]
        self.decision_id = 0
        self.seat = 0
        self.actions: list[Action] = []
        self.earlier: list[list[Speech]] = []
        self.so_far: list[Speech] = []
        self.round_index = 0
        self.speaker_index = 0
        self.order = self.engine.council_order()
        self.phase = "council" if self.engine.commune_due() else "fishing"
        self.memory_seats = [
            slot for slot, soul in enumerate(souls) if not soul.model.startswith("scripted/")
        ]
        self.memory_index = 0
        if self.memory is not None and self.memory_seats:
            if self.mode != "text":
                raise ValueError("persistent memory decisions require language mode")
            self.phase = "memory_read"
            self.seat = self.memory_seats[0]
        self.conversation = []
        self.retries_left = 1
        self.thinks_left = self.config.llm.think_turns
        self.calls = 0
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
        brain = self.brains[self.seat]
        system = brain.system_prompt
        if self.phase == "memory_read":
            assert self.memory is not None
            user = scratchpad_read_observation(
                self.engine, self.seat, self.memory.read(self.policy_ids[self.seat])
            )
        elif self.phase == "memory_write":
            shared_seats = self.policy_ids.count(self.policy_ids[self.seat])
            limit = (SCRATCHPAD_NOTE_BYTES - (shared_seats - 1)) // shared_seats
            user = scratchpad_write_observation(self.engine, self.seat, brain.notebook, limit)
        elif self.phase == "council":
            user = council_observation(
                self.engine,
                self.seat,
                brain.notebook,
                self.round_index,
                self.order,
                self.earlier,
                self.so_far,
            )
        else:
            user = turn_observation(self.engine, self.seat, brain.notebook)
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def observation(self) -> dict[str, object]:
        if self.engine.finished and self.phase != "memory_write":
            scores = {seat: float(score) for seat, score in enumerate(self.engine.results()["scores"])}
            scale = self.engine.lake.boat_capacity * self.config.turns.hi / self.config.num_players
            return {
                "kind": "terminal",
                "scores": scores,
                "utilities": {seat: score / (scale + score) for seat, score in scores.items()},
            }
        view = self.view()
        messages = self.messages()
        if self.mode == "text":
            if not self.conversation:
                self.conversation = messages
            messages = self.conversation
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
            "memory_mode": "append-v1" if self.memory is not None else "disabled",
        }
        if self.phase in {"memory_read", "memory_write"}:
            key = "notebook" if self.phase == "memory_read" else "scratchpad_append"
            return {
                "kind": "decision",
                **common,
                "inference_mode": "text_action",
                "action_schema": {"type": "object", "properties": {key: {"type": "string"}}},
                "typed_question": None,
            }
        if self.phase == "council":
            if self.mode == "text":
                return {
                    "kind": "decision",
                    **common,
                    "inference_mode": "text_action",
                    "action_schema": {
                        "type": "object",
                        "properties": {"say": {"type": "string"}},
                        "required": ["say"],
                    },
                    "typed_question": None,
                }
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
        if self.phase == "memory_read":
            assert self.memory is not None
            memory = self.memory.read(self.policy_ids[self.seat])
            return {"response": compact({"notebook": memory.summary})}
        if self.phase == "memory_write":
            return {"response": "{}"}
        soul = self.brains[self.seat].soul
        policy = (
            scripted_policy(soul.scripted_name, soul.text) if soul.scripted else ScriptedPolicy("steady", 0.4)
        )
        if self.phase == "council":
            speech = policy.say(self.round_index)
            return {"response": compact({"say": speech}) if self.mode == "text" else speech}
        if self.mode == "choice":
            return {"response": compact({"choice": EFFORTS.index(0.4) * len(SOCIAL)})}
        return {
            "response": policy.act(ScriptedView.from_engine(self.engine, self.seat)).model_dump_json(),
            "policy": f"scripted/{policy.name}",
        }

    def say(self, request: dict[str, object]) -> dict[str, object]:
        if self.phase != "council" or request["decision_id"] != self.decision_id:
            raise ValueError("Stale council turn")
        text = str(request["text"])[: self.config.llm.say_max_chars]
        self.so_far.append(Speech(slot=self.seat, text=text))
        self.decision_id += 1
        self.conversation = []
        self.calls = 0
        self.retries_left = 1
        self.thinks_left = self.config.llm.think_turns
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
        if request["decision_id"] != self.decision_id:
            return {"kind": "rejected", "reason": "stale decision"}
        if self.phase in {"memory_read", "memory_write"}:
            return self.step_memory(request)
        if self.mode == "choice":
            return self.step_choice(request)
        response = str(request["response"])
        self.calls += 1
        parsed = parse_decision_reply(response, self.brains[self.seat], self.engine, self.phase == "council")
        consumed = ""
        if isinstance(parsed, (ThinkingReply, InvalidReply)):
            self.conversation.append({"role": "assistant", "content": response})
            if isinstance(parsed, ThinkingReply):
                if self.thinks_left <= 0:
                    correction = "No more private thinking turns. Decide now with one JSON object."
                else:
                    self.thinks_left -= 1
                    correction = f"Continue privately. {self.thinks_left} thinking turn(s) left before you must decide."
                reason = "private thinking continuation; no game action"
                exhausted = self.calls == self.config.llm.max_calls_per_decision
            else:
                reason, correction = parsed.reason, parsed.retry_message
                exhausted = self.retries_left == 0 or self.calls == self.config.llm.max_calls_per_decision
                self.retries_left -= 1
            if not exhausted:
                self.conversation.append({"role": "user", "content": correction})
                return {"kind": "rejected", "reason": reason, "observation": self.observation()}
            if self.phase == "council":
                result = self.say({"decision_id": self.decision_id, "text": ""})
                return {
                    "kind": "consumed_rejection",
                    "reason": reason,
                    "action": {"say": ""},
                    "observation": result["observation"],
                }
            action = fallback_action(self.engine, self.seat)
            consumed = reason
        elif isinstance(parsed, SpeechReply):
            result = self.say({"decision_id": self.decision_id, "text": parsed.text})
            return {
                "kind": "accepted",
                "action": {"say": result["text"]},
                "observation": result["observation"],
            }
        else:
            action = parsed.action
        self.actions.append(action)
        self.advance_fishing()
        return {
            "kind": "consumed_rejection" if consumed else "accepted",
            "reason": consumed,
            "action": action.model_dump(mode="json"),
            "observation": self.observation(),
        }

    def step_memory(self, request: dict[str, object]) -> dict[str, object]:
        assert self.memory is not None
        reply = extract_json(str(request["response"]))
        action = {}
        rejected = ""
        if self.phase == "memory_read":
            if reply is not None and isinstance(reply.get("notebook"), str):
                self.brains[self.seat].notebook = reply["notebook"].strip()[
                    : self.config.llm.notebook_max_chars
                ]
                action = {"notebook": self.brains[self.seat].notebook}
            else:
                rejected = "memory unchanged: invalid notebook response"
        else:
            shared_seats = self.policy_ids.count(self.policy_ids[self.seat])
            limit = (SCRATCHPAD_NOTE_BYTES - (shared_seats - 1)) // shared_seats
            if (
                reply is not None
                and set(reply) == {"scratchpad_append"}
                and isinstance(reply["scratchpad_append"], str)
                and len(reply["scratchpad_append"].encode("utf-8")) <= limit
            ):
                self.memory.append(self.policy_ids[self.seat], reply["scratchpad_append"])
                action = {"scratchpad_append": reply["scratchpad_append"]}
            else:
                rejected = "memory unchanged: invalid or absent append"
        self.decision_id += 1
        self.conversation = []
        self.memory_index += 1
        if self.memory_index < len(self.memory_seats):
            self.seat = self.memory_seats[self.memory_index]
        else:
            self.phase = (
                "terminal"
                if self.phase == "memory_write"
                else ("council" if self.engine.commune_due() else "fishing")
            )
            self.seat = self.order[0] if self.phase == "council" else 0
        return {
            "kind": "consumed_rejection" if rejected else "accepted",
            "reason": rejected,
            "action": action,
            "observation": self.observation(),
        }

    def step_choice(self, request: dict[str, object]) -> dict[str, object]:
        if self.phase != "fishing":
            return {"kind": "rejected", "reason": "numeric decisions require fishing phase"}
        reply = extract_json(str(request["response"]))
        if reply is None:
            return {"kind": "rejected", "reason": "response needs one JSON object"}
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
        self.actions.append(action)
        self.advance_fishing()
        return {"kind": "accepted", "action": accepted, "observation": self.observation()}

    def advance_fishing(self) -> None:
        self.conversation = []
        self.calls = 0
        self.retries_left = 1
        self.thinks_left = self.config.llm.think_turns
        self.decision_id += 1
        self.seat += 1
        if self.seat == self.config.num_players:
            self.engine.resolve_turn(self.actions)
            self.actions = []
            if self.engine.finished and self.memory is not None and self.memory_seats:
                self.phase = "memory_write"
                self.memory_index = 0
                self.seat = self.memory_seats[0]
            elif not self.engine.finished and self.engine.commune_due():
                self.phase = "council"
                self.order = self.engine.council_order()
                self.earlier = []
                self.so_far = []
                self.round_index = 0
                self.speaker_index = 0
                self.seat = self.order[0]
            else:
                self.seat = 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", default="certification")
    parser.add_argument("--mode", choices=("choice", "text"), default="choice")
    parser.add_argument("--turns", type=int)
    parser.add_argument("--soul", type=Path, action="append", required=True)
    parser.add_argument("--scratchpad-dir", type=Path)
    args = parser.parse_args()
    session = TrainingSession(args.variant, args.mode, args.turns, args.soul, args.scratchpad_dir)
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
