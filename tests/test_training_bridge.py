"""Complete-game proof for the shared Overfished training transport."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from overfished.memory import ScratchpadStore, policy_id
from tools.training_bridge import TrainingSession

ROOT = Path(__file__).resolve().parents[1]


def test_language_memory_and_notebook_use_hosted_phases(tmp_path):
    soul = ROOT / "souls/examples/villager.md"
    identifier = policy_id(soul.read_bytes())
    store = ScratchpadStore(tmp_path)
    store.append(identifier, "PRIVATE BOOTSTRAP NOTE")
    session = TrainingSession("quiet-lake", "text", 2, [soul] * 8, tmp_path)
    observation = session.reset({"seed": "memory-parity", "players": 8})
    reads = writes = 0
    while observation["kind"] != "terminal":
        assert observation["memory_mode"] == "append-v1"
        prompt = observation["messages"][1]["content"]
        if prompt.startswith("SCRATCHPAD READ"):
            assert "PRIVATE BOOTSTRAP NOTE" in prompt
            reads += 1
            response = {"notebook": "PRIVATE CARRIED NOTE"}
        elif prompt.startswith("SCRATCHPAD WRITE"):
            assert "PRIVATE CARRIED NOTE" in prompt
            writes += 1
            response = {"scratchpad_append": "PRIVATE EPISODE NOTE"}
        else:
            assert "PRIVATE CARRIED NOTE" in prompt
            response = {"effort": 0.4}
        result = session.step({"decision_id": observation["decision_id"], "response": json.dumps(response)})
        assert result["kind"] == "accepted"
        observation = result["observation"]
    assert reads == writes == 8
    assert store.read(identifier).notes == ["PRIVATE BOOTSTRAP NOTE"] + ["PRIVATE EPISODE NOTE"] * 8
    assert "PRIVATE" not in json.dumps(session.engine.replay())


def test_language_thinking_and_retry_preserve_hosted_conversation():
    soul = ROOT / "souls/examples/villager.md"
    session = TrainingSession("quiet-lake", "text", 2, [soul] * 8, None)
    initial = session.reset({"seed": "retry-parity", "players": 8})
    first = session.step({"decision_id": 0, "response": '{"continue":true,"notebook":"private"}'})
    assert first["kind"] == "rejected" and first["observation"]["decision_id"] == 0
    assert (
        first["observation"]["messages"][-1]["content"]
        == "Continue privately. 0 thinking turn(s) left before you must decide."
    )
    second = session.step({"decision_id": 0, "response": "invalid JSON"})
    assert second["kind"] == "rejected"
    assert (
        second["observation"]["messages"][-1]["content"]
        == "That was not a JSON object. Reply with exactly one JSON object."
    )
    consumed = session.step({"decision_id": 0, "response": "invalid again"})
    assert consumed["kind"] == "consumed_rejection"
    assert consumed["action"] == {"effort": 0.4, "punish": [], "gift": [], "auto": True}
    assert consumed["observation"]["decision_id"] == 1
    assert initial["messages"][0]["content"].startswith(soul.read_text().partition("\n")[2].strip())


@pytest.mark.parametrize("mode", ["choice", "text"])
def test_certification_game_uses_player_views_and_real_scores(mode: str) -> None:
    session = TrainingSession("certification", mode, None, [ROOT / "souls/steady.md"] * 8, None)
    observation = session.reset({"seed": "training-proof", "players": 8})
    counts = {"speech_turn": 0, "decision": 0}
    while observation["kind"] != "terminal":
        assert "SCRATCHPAD" not in json.dumps(observation["messages"])
        assert "POLICY ROSTER" in json.dumps(observation["messages"])
        assert "stock" not in observation["semantic_view"]
        assert "seed" not in observation["semantic_view"]
        counts[observation["kind"]] += 1
        if observation["kind"] == "speech_turn":
            observation = session.say({"decision_id": observation["decision_id"], "text": "Fish steadily."})[
                "observation"
            ]
        else:
            if mode == "choice":
                encoding = session.encode()
                assert len(encoding["values"]) == 39
                assert len(encoding["actions"]) == (8 if session.phase == "voting" else 15)
            observation = session.step(
                {"decision_id": observation["decision_id"], "response": session.teacher()["response"]}
            )["observation"]
    assert counts == (
        {"speech_turn": 48, "decision": 120} if mode == "choice" else {"speech_turn": 0, "decision": 168}
    )
    assert observation["scores"] == {seat: float(score) for seat, score in enumerate(session.engine.fish)}
    assert all(0 <= utility < 1 for utility in observation["utilities"].values())


def test_quiet_lake_has_no_speech_turns() -> None:
    session = TrainingSession("quiet-lake", "choice", 2, [ROOT / "souls/steady.md"] * 8, None)
    observation = session.reset({"seed": "quiet", "players": 8})
    decisions = 0
    while observation["kind"] != "terminal":
        assert observation["kind"] == "decision"
        decisions += 1
        observation = session.step(
            {"decision_id": observation["decision_id"], "response": session.teacher()["response"]}
        )["observation"]
    assert decisions == 16


def test_jsonl_process_reuses_seeded_session() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tools/training_bridge.py"),
            *[arg for _ in range(8) for arg in ("--soul", str(ROOT / "souls/steady.md"))],
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None and process.stdout is not None
    for seed in ("alpha", "beta"):
        process.stdin.write(json.dumps({"kind": "reset", "seed": seed, "players": 8}) + "\n")
        process.stdin.flush()
        observation = json.loads(process.stdout.readline())
        assert observation["decision_id"] == 0
        assert observation["kind"] == "speech_turn"
    process.stdin.close()
    assert process.wait(timeout=5) == 0


@pytest.mark.parametrize("mode", ["choice", "text"])
def test_training_ballots_expel_and_reinstate_without_leaking_pending_votes(mode):
    session = TrainingSession("certification", mode, 6, [ROOT / "souls/steady.md"] * 8, None)
    observation = session.reset({"seed": "ballots", "players": 8})
    target_name = session.engine.pseudonyms[0]
    ballots_seen = 0
    while observation["kind"] != "terminal":
        if observation["kind"] == "speech_turn":
            observation = session.say({"decision_id": session.decision_id, "text": "Council chat"})[
                "observation"
            ]
            continue
        if session.phase == "voting":
            ballots_seen += 1
            if session.seat == 1:
                bad = {"choice": 2} if mode == "choice" else {"vote": session.engine.pseudonyms[1]}
                assert (
                    session.step({"decision_id": session.decision_id, "response": json.dumps(bad)})["kind"]
                    == "rejected"
                )
            if session.engine.turn == 1:
                assert session.engine.communes == []  # No partial ballots in public state.
            reply = (
                {"choice": 0 if session.seat == 0 else 1}
                if mode == "choice"
                else {"vote": None if session.seat == 0 else target_name}
            )
            response = json.dumps(reply)
        else:
            if session.phase == "fishing":
                assert session.seat != session.engine.expelled
            response = session.teacher()["response"]
        observation = session.step({"decision_id": session.decision_id, "response": response})["observation"]
    assert ballots_seen == 16
    assert [c.passed_target for c in session.engine.communes] == [0, 0]
    resumed = session.engine.communes[1].before_turn - 1
    assert all(t.catch[0] == 0 for t in session.engine.turns[:resumed])
    assert session.engine.turns[resumed].catch[0] > 0
    assert session.engine.expulsion_used and session.engine.expelled is None


def test_language_teacher_uses_registered_scripted_soul():
    souls = [ROOT / "souls/greedy.md"] * 8
    session = TrainingSession("quiet-lake", "text", 2, souls, None)
    observation = session.reset({"seed": "teacher-policy", "players": 8})
    teacher = session.teacher()
    assert teacher["policy"] == "scripted/greedy"
    assert json.loads(teacher["response"])["effort"] == 1.0
    result = session.step({"decision_id": observation["decision_id"], "response": teacher["response"]})
    assert result["kind"] == "accepted"
    for _ in range(7):
        observation = result["observation"]
        result = session.step(
            {"decision_id": observation["decision_id"], "response": session.teacher()["response"]}
        )
    assert session.engine.last_effort == [1.0] * 8


def test_language_teacher_and_prompt_ignore_hidden_lake_and_fortune():
    session = TrainingSession("quiet-lake", "text", 3, [ROOT / "souls/enforcer.md"] * 8, None)
    observation = session.reset({"seed": "hidden-view", "players": 8})
    for _ in range(8):
        result = session.step(
            {"decision_id": observation["decision_id"], "response": session.teacher()["response"]}
        )
        observation = result["observation"]
    prompt = json.dumps(session.observation(), sort_keys=True)
    teacher = session.teacher()
    session.engine.stock *= 0.5
    session.engine.lake.capacity *= 2
    session.engine.lake.growth_rate *= 0.5
    session.engine.lake.collapse_threshold *= 0.5
    session.engine.turn_limit += 10
    for turn in session.engine.turns:
        turn.stock_before *= 0.2
        turn.stock_after *= 0.3
        turn.fortune = [0.5] * 8
    assert json.dumps(session.observation(), sort_keys=True) == prompt
    assert session.teacher() == teacher
