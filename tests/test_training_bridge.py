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
                assert len(encoding["values"]) == 37
                assert len(encoding["actions"]) == 15
            observation = session.step(
                {"decision_id": observation["decision_id"], "response": session.teacher()["response"]}
            )["observation"]
    assert counts == {"speech_turn": 48, "decision": 96}
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
