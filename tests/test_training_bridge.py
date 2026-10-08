"""Complete-game proof for the shared Overfished training transport."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.training_bridge import TrainingSession

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["choice", "text"])
def test_certification_game_uses_player_views_and_real_scores(mode: str) -> None:
    session = TrainingSession("certification", mode, None)
    observation = session.reset({"seed": "training-proof", "players": 8})
    counts = {"speech_turn": 0, "decision": 0}
    while observation["kind"] != "terminal":
        assert "SCRATCHPAD" not in json.dumps(observation["messages"])
        assert "POLICY ROSTER" not in json.dumps(observation["messages"])
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
    assert counts == {"speech_turn": 48, "decision": 120}
    assert observation["scores"] == {seat: float(score) for seat, score in enumerate(session.engine.fish)}
    assert all(0 <= utility < 1 for utility in observation["utilities"].values())


def test_quiet_lake_has_no_speech_turns() -> None:
    session = TrainingSession("quiet-lake", "choice", 2)
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
        [str(ROOT / ".venv/bin/python"), str(ROOT / "tools/training_bridge.py")],
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
    session = TrainingSession("certification", mode, 6)
    observation = session.reset({"seed": "ballots", "players": 8})
    target_name = session.engine.pseudonyms[0]
    ballots_seen = 0
    while observation["kind"] != "terminal":
        if observation["kind"] == "speech_turn":
            observation = session.say({"decision_id": session.decision_id, "text": "Council chat"})["observation"]
            continue
        if session.phase == "voting":
            ballots_seen += 1
            if session.seat == 1:
                bad = {"choice": 2} if mode == "choice" else {"vote": session.engine.pseudonyms[1]}
                assert session.step({"decision_id": session.decision_id, "response": json.dumps(bad)})["kind"] == "rejected"
            if session.engine.turn == 1:
                assert session.engine.communes == []  # No partial ballots in public state.
            reply = ({"choice": 0 if session.seat == 0 else 1} if mode == "choice"
                     else {"vote": None if session.seat == 0 else target_name})
            response = json.dumps(reply)
        else:
            response = session.teacher()["response"]
        observation = session.step({"decision_id": session.decision_id, "response": response})["observation"]
    assert ballots_seen == 16
    assert [c.passed_target for c in session.engine.communes] == [0, 0]
    resumed = session.engine.communes[1].before_turn - 1
    assert all(t.catch[0] == 0 for t in session.engine.turns[:resumed])
    assert session.engine.turns[resumed].catch[0] > 0
    assert session.engine.expulsion_used and session.engine.expelled is None
