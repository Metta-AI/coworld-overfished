import json
import os
import subprocess
from uuid import uuid4

import pytest
from aiohttp import web
from test_episode import SOULS, VILLAGER, run_episode

from overfished.llm import Transport
from overfished.trajectory import Attempt, Trajectory

SOURCE_REVISION = (
    os.environ["COWORLD_SOURCE_REVISION"]
    if "COWORLD_SOURCE_REVISION" in os.environ
    else subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
)


def test_recorder_rejects_applied_action_mismatch(tmp_path):
    recorder = Trajectory(
        episode_id="episode",
        game_version="test",
        source_revision="a" * 40,
        seed_family="seed",
        image_digest=None,
        destination=tmp_path / "trajectory.jsonl",
    )
    proposal = Attempt(
        policy="teacher",
        origin="teacher",
        inference_mode="text_action",
        prompt=[],
        accepted=True,
        rejection_reason=None,
        parsed_action={"effort": 0.4},
    )
    with pytest.raises(ValueError, match="independently executed"):
        recorder.record(
            decision_id="turn",
            seat=0,
            observation={},
            prompt=[],
            attempts=[proposal],
            executed_action={"effort": 0.7},
            fallback_origin=None,
            terminal=False,
        )


@pytest.mark.parametrize("sampled", [False, True])
async def test_native_attempts_join_private_memory_speech_and_actions(
    tmp_path, monkeypatch, unused_tcp_port, sampled
):
    archives = {}

    async def complete(request):
        body = await request.json()
        slot = request.headers["X-Coworld-Player-Slot"]
        assert body["temperature"] == (1 if sampled else 0) and body["stream"] is False
        observation = body["messages"][-1]["content"]
        if observation.startswith("SCRATCHPAD READ"):
            answer = {"notebook": "PRIVATE MEMORY SENTINEL"}
        elif observation.startswith("SCRATCHPAD WRITE"):
            answer = {"scratchpad_append": "PRIVATE DURABLE SENTINEL"}
        elif observation.startswith("COUNCIL BALLOT"):
            answer = {"vote": None}
        elif "COUNCIL" in body["messages"][1]["content"][:60]:
            answer = {"say": "Public council sentence", "thinking": "PRIVATE THOUGHT SENTINEL"}
        elif observation.startswith("Continue privately"):
            answer = {"effort": 0.4, "notebook": "PRIVATE NOTEBOOK SENTINEL"}
        else:
            answer = {"continue": True, "thinking": "PRIVATE THOUGHT SENTINEL"}
        call_id = str(uuid4())
        payload = {
            "model": "fixture/actually-served",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}],
            "usage": {"prompt_tokens": 32768 if sampled else 7, "completion_tokens": 2 if sampled else 3},
            "sampling_evidence": {
                "policy_revision": "synthetic-model",
                "tokenizer_revision": "synthetic-tokenizer",
                "chat_template": "synthetic-template",
                "sampling": "full_softmax_temperature_one",
                "enable_thinking": False,
                "max_new_tokens": body["max_tokens"],
                "max_sequence_length": 32768 + body["max_tokens"],
                "sampling_seed": 0,
                "eos_token_ids": [4],
                "prompt_token_ids": list(range(32768)),
                "completion_token_ids": [3, 4],
                "behavior_log_probs": [-0.5, -0.6],
                "response": json.dumps(answer),
                "stop_reason": "eos",
            }
            if sampled
            else None,
        }
        archives[call_id] = (slot, body, payload)
        return web.json_response(
            payload,
            headers={
                "X-Softmax-Llm-Call-Id": call_id,
                "X-Coworld-Checkpoint-Sha256": "a" * 64,
                "X-Coworld-Tokenizer-Sha256": "b" * 64,
                "X-Coworld-Chat-Template-Sha256": "c" * 64,
            },
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    trajectory = tmp_path / "trajectory.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", trajectory.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "native-fixture")
    monkeypatch.setenv("COWORLD_GAME_VERSION", "fixture")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", SOURCE_REVISION)
    monkeypatch.delenv("COWORLD_GAME_IMAGE_DIGEST", raising=False)
    monkeypatch.setenv("COWORLD_LLM_TEMPERATURE", "1" if sampled else "0")
    try:
        transport = Transport(base_url=f"http://127.0.0.1:{unused_tcp_port}", timeout_seconds=2)
        results, replay, _ = await run_episode(
            tmp_path, [VILLAGER, SOULS / "steady.md"], transport, turns=2, commune_every=3
        )
    finally:
        await runner.cleanup()
    complete = json.loads(trajectory.read_text())
    records = complete["decisions"] + [complete["episode"]]
    assert records[-1]["outcome"]["scores"] == results["scores"]
    attempts = [a for r in records[:-1] for a in r["attempts"] if a["origin"] == "model"]
    archive_path = tmp_path / "native-call-archives.json"
    archive_path.write_text(json.dumps(archives) + "\n")
    archive_path.chmod(0o600)
    assert len(attempts) == len(archives)
    assert {a["platform_call_id"] for a in attempts} == set(archives)
    assert {a["inference_mode"] for a in attempts} == {"memory", "speech", "text_action"}
    for attempt in attempts:
        slot, request, response = archives[attempt["platform_call_id"]]
        assert attempt["request"] == request and json.loads(attempt["raw_response"]) == response
        assert attempt["prompt"] == request["messages"] and slot == "0"
        assert attempt["model"] == "fixture/actually-served"
        assert attempt["model_identity"] == "a" * 64
        if sampled:
            assert attempt["prompt_token_ids"] == list(range(32768))
            assert attempt["sampled_token_ids"] == [3, 4] and attempt["behavior_logprobs"] == [-0.5, -0.6]
        else:
            assert attempt["sampled_token_ids"] is None and attempt["behavior_logprobs"] is None
    assert sum(not a["accepted"] for a in attempts) == 2
    for record in records[:-1]:
        selected = next(a for a in record["attempts"] if a["attempt_id"] == record["selected_attempt_id"])
        assert selected["parsed_action"] == record["executed_action"]
    assert "PRIVATE" not in json.dumps(replay)
    assert trajectory.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mode", ["http429", "invalid-json", "invalid-schema"])
async def test_failed_native_responses_survive_complete_fallback_episode(
    tmp_path, monkeypatch, unused_tcp_port, mode
):
    archives = {}

    async def complete(request):
        body = await request.json()
        call_id = str(uuid4())
        status = 429 if mode == "http429" else 200
        text = "PRIVATE FAILED RESPONSE" if mode != "invalid-schema" else '{"choices":[]}'
        archives[call_id] = (body, text)
        return web.Response(text=text, status=status, headers={"X-Softmax-Llm-Call-Id": call_id})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    trajectory = tmp_path / "trajectory.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", trajectory.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", f"native-failure-{mode}")
    monkeypatch.setenv("COWORLD_GAME_VERSION", "fixture")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", SOURCE_REVISION)
    try:
        transport = Transport(f"http://127.0.0.1:{unused_tcp_port}", 2)
        _, replay, _ = await run_episode(
            tmp_path,
            [VILLAGER, SOULS / "steady.md"],
            transport,
            turns=2,
            commune_rounds=0,
            commune_at_start=False,
        )
    finally:
        await runner.cleanup()
    complete = json.loads(trajectory.read_text())
    records = complete["decisions"] + [complete["episode"]]
    assert records[-1]["status"] == "completed"
    model_rows = [row for row in records[:-1] if row["seat"] == "0"]
    assert model_rows and all(row["action_status"] == "fallback" for row in model_rows)
    attempts = [attempt for row in model_rows for attempt in row["attempts"]]
    archive_path = tmp_path / "native-call-archives.json"
    archive_path.write_text(json.dumps(archives) + "\n")
    archive_path.chmod(0o600)
    assert len(attempts) == len(archives)
    for attempt in attempts:
        request, raw = archives[attempt["platform_call_id"]]
        assert attempt["request"] == request
        assert attempt["raw_response"] == raw
        assert not attempt["accepted"] and attempt["rejection_reason"]
    assert "PRIVATE" not in json.dumps(replay)


def test_failed_latest_snapshot_preserves_previous_received_facts_and_new_private_partial(
    tmp_path, monkeypatch
):
    recorder = Trajectory(
        episode_id="snapshot",
        game_version="test",
        source_revision="a" * 40,
        seed_family="overfished-1",
        image_digest=None,
        destination=tmp_path / "complete.jsonl",
    )
    attempt = Attempt(policy="fixture", inference_mode="text_action", prompt=[], response="first actual text")
    recorder.observe(0, attempt, [])
    previous = recorder.pending_path.read_bytes()
    attempt.response = "second actual text"

    def fail_replace(source, destination):
        raise OSError("owned snapshot replacement failed")

    monkeypatch.setattr("overfished.trajectory.os.replace", fail_replace)
    try:
        with pytest.raises(OSError, match="snapshot replacement"):
            recorder.observe(0, attempt, [])
        assert recorder.pending_path.read_bytes() == previous
        partial = next(tmp_path.glob("*.partial"))
        assert "second actual text" in partial.read_text()
        assert partial.stat().st_mode & 0o777 == 0o600
    finally:
        recorder.spool.close()
