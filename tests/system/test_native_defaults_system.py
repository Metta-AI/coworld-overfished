"""Complete native games against a provider that rejects unsupported controls."""

import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from aiohttp import web

from overfished.__main__ import stage_local_episode
from overfished.config import GameConfig
from overfished.llm import Transport
from overfished.server import Episode, load_seats

ROOT = Path(__file__).resolve().parents[2]
SOURCE = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


@pytest.mark.parametrize("explicit", [False, True])
async def test_complete_native_game_respects_optional_decoder_controls(
    tmp_path, monkeypatch, unused_tcp_port, explicit
):
    requests = []
    for variable in ("COWORLD_LLM_TEMPERATURE", "COWORLD_LLM_TOP_P"):
        monkeypatch.delenv(variable, raising=False)
    if explicit:
        monkeypatch.setenv("COWORLD_LLM_TEMPERATURE", "0.4")
        monkeypatch.setenv("COWORLD_LLM_TOP_P", "0.9")

    async def infer(request):
        body = await request.json()
        requests.append(body)
        controls = {key: body[key] for key in ("temperature", "top_p") if key in body}
        if not explicit and controls:
            return web.json_response({"error": {"code": "routing_parameters"}}, status=404)
        assert controls == ({"temperature": 0.4, "top_p": 0.9} if explicit else {})
        assert request.headers["X-Coworld-Player-Slot"] == "0"
        return web.json_response(
            {
                "model": body["model"],
                "choices": [{"message": {"content": '{"effort":0.4}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
            headers={"X-Softmax-Llm-Call-Id": str(uuid4())},
        )

    application = web.Application()
    application.router.add_post("/v1/chat/completions", infer)
    runner = web.AppRunner(application)
    await runner.setup()
    port = unused_tcp_port
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    private = tmp_path / "private.jsonl"
    for name, value in {
        "COGAME_SAVE_TRAJECTORY_URI": private.as_uri(),
        "COWORLD_EPISODE_ID": "native-defaults-system",
        "COWORLD_GAME_VERSION": "system-proof",
        "COWORLD_SOURCE_REVISION": SOURCE,
    }.items():
        monkeypatch.setenv(name, value)
    soul = tmp_path / "model.md"
    soul.write_text("#!fixture/model\nChoose a sustainable effort.\n")
    config = GameConfig.model_validate(
        {
            "tokens": ["model-seat", "scripted-seat"],
            "players": [{"name": "model"}, {"name": "teacher"}],
            "turns": {"lo": 2, "hi": 2},
            "commune_at_start": False,
            "commune_rounds": 0,
        }
    )
    paths, artifacts = stage_local_episode(config, [soul, ROOT / "souls/steady.md"], tmp_path / "game")
    transport = Transport(f"http://127.0.0.1:{port}", 2)
    try:
        episode = Episode.from_seats(config, 7, load_seats(paths.as_uri()), transport, artifacts)
        await episode.run()
        episode.finalize()
    finally:
        await runner.cleanup()
    record = json.loads(private.read_text())
    assert record["episode"]["status"] == "completed"
    assert record["episode"]["outcome"]["turns_played"] == 2
    model_decisions = [decision for decision in record["decisions"] if decision["seat"] == "0"]
    assert len(model_decisions) == 2
    assert all(decision["action_status"] == "accepted" for decision in model_decisions)
    for decision, request in zip(model_decisions, requests, strict=True):
        selected = next(a for a in decision["attempts"] if a["attempt_id"] == decision["selected_attempt_id"])
        assert selected["request"] == request
        assert selected["parsed_action"] == decision["executed_action"]
        assert selected["decoder"]["temperature"] == (0.4 if explicit else None)
        assert selected["decoder"]["top_p"] == (0.9 if explicit else None)
        assert selected["sampled_token_ids"] is None and selected["behavior_logprobs"] is None
    assert "fixture/model" not in json.dumps(json.loads((tmp_path / "game" / "replay").read_text())["turns"])
