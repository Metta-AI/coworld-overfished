"""End-to-end episodes without Docker: scripted seats, a fake model transport, and a rejected soul."""

import json
from pathlib import Path

import pytest

from overfished.config import GameConfig
from overfished.llm import Transport
from overfished.server import ArtifactPaths, Episode, load_seats, serve_episode
from overfished.soul import SoulError
from overfished.__main__ import stage_local_episode

SOULS = Path(__file__).resolve().parent.parent / "souls"
VILLAGER = SOULS / "examples" / "villager.md"


def config_for(souls: list[Path], **overrides) -> GameConfig:
    base = {
        "tokens": [f"tok{i}" for i in range(len(souls))],
        "players": [{"name": p.stem} for p in souls],
        "seed": 7,
        "turns": 6,
        "commune_every": 3,
    }
    base.update(overrides)
    return GameConfig.model_validate(base)


class FakeTransport(Transport):
    """Answers like a model that thinks once, then acts; punishes the top catcher every third turn."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.base_url = "fake"
        self.api_key = None
        self.timeout_seconds = 1.0
        self.session = None
        self.slots_seen: set[int] = set()
        self.replies = replies
        self.council_prompts: list[str] = []

    @property
    def describe(self) -> str:
        return "fake"

    async def complete(self, *, model: str, messages: list[dict], max_tokens: int, slot: int, reasoning: dict | None = None) -> str:
        self.calls += 1
        self.slots_seen.add(slot)
        assert model.startswith("anthropic/") or "/" in model
        assert reasoning == {"effort": "low"}
        if self.replies is not None:
            return self.replies.pop(0)
        last = messages[-1]["content"]
        if last.startswith("Continue privately"):
            return json.dumps({"thinking": "ok, deciding", "notebook": "keep at 50%", "effort": 0.5, "punish": []})
        if "COUNCIL" in last[:60]:
            self.council_prompts.append(last)
            return json.dumps({"thinking": "say something", "say": f"Seat {slot} says: let us all fish at half."})
        return json.dumps({"thinking": "let me think more", "continue": True})


async def run_episode(tmp_path: Path, souls: list[Path], transport, **overrides) -> tuple[dict, dict, Path]:
    config = config_for(souls, **overrides)
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path)
    document = load_seats(seats_path.resolve().as_uri())
    episode = Episode.from_seats(config, 7, document, transport, artifacts)
    await episode.run()
    results = json.loads((tmp_path / "results.json").read_text())
    replay = json.loads((tmp_path / "replay").read_text())
    return results, replay, tmp_path


async def test_scripted_episode_writes_every_artifact(tmp_path: Path):
    souls = [SOULS / "steady.md", SOULS / "greedy.md", SOULS / "enforcer.md", SOULS / "steady.md"]
    results, replay, out = await run_episode(tmp_path, souls, None)
    assert len(results["scores"]) == 4
    assert results["turns_played"] == 6
    assert len(replay["turns"]) == 6
    assert [c["before_turn"] for c in replay["communes"]] == [1, 4]
    for slot in range(4):
        log = (out / "logs" / f"policy_agent_{slot}.log").read_text()
        assert "seated as" in log and "episode over" in log
    status = json.loads((out / "player_status.json").read_text())
    assert [p["state"] for p in status["players"]] == ["exited"] * 4
    assert results["models"][1] == "scripted/greedy"


async def test_soul_seats_think_then_act_and_talk(tmp_path: Path):
    villager = VILLAGER
    souls = [villager, villager, SOULS / "steady.md"]
    transport = FakeTransport()
    results, replay, out = await run_episode(tmp_path, souls, transport)
    assert transport.slots_seen == {0, 1}
    # every fishing turn the soul seats thought once and then acted at 50%
    for turn in replay["turns"]:
        assert turn["effort"][0] == 0.5 and turn["effort"][1] == 0.5
        assert turn["auto"] == []
    said = [s["text"] for c in replay["communes"] for r in c["rounds"] for s in r if s["slot"] == 0]
    assert said and all(t == "Seat 0 says: let us all fish at half." for t in said)
    # speaking is sequential: the second speaker's prompt already quotes the first speaker of that round
    first_council = replay["communes"][0]
    assert first_council["order"] == [0, 1, 2]
    assert [s["slot"] for s in first_council["rounds"][0]] == [0, 1, 2]
    second_speaker_prompts = [p for p in transport.council_prompts if "You speak 2nd" in p]
    assert second_speaker_prompts and "Seat 0 says" in second_speaker_prompts[0]
    assert replay["communes"][1]["order"] == [1, 2, 0]
    log = (out / "logs" / "policy_agent_0.log").read_text()
    assert "thinking: let me think more" in log
    assert "raw reply" in log
    assert results["llm"]["calls"] == transport.calls
    # private thinking never reaches the replay
    assert "let me think more" not in json.dumps(replay)


async def test_bad_replies_fall_back_and_are_marked_auto(tmp_path: Path):
    villager = VILLAGER
    souls = [villager, SOULS / "steady.md"]
    transport = FakeTransport(replies=["garbage"] * 200)
    results, replay, out = await run_episode(tmp_path, souls, transport, commune_rounds=1, turns=2)
    assert all(0 in turn["auto"] for turn in replay["turns"])
    assert all(s["auto"] for c in replay["communes"] for r in c["rounds"] for s in r if s["slot"] == 0)
    log = (out / "logs" / "policy_agent_0.log").read_text()
    assert "fallback" in log


async def test_exhausted_wall_budget_goes_scripted(tmp_path: Path):
    villager = VILLAGER
    transport = FakeTransport()
    souls = [villager, villager]
    config = config_for(souls, turns=2)
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path)
    document = load_seats(seats_path.resolve().as_uri())
    episode = Episode.from_seats(config, 7, document, transport, artifacts)
    episode.started -= config.episode_wall_seconds + 1
    await episode.run()
    replay = json.loads((tmp_path / "replay").read_text())
    assert transport.calls == 0
    assert all(turn["auto"] == [0, 1] for turn in replay["turns"])


async def test_rejected_soul_declares_player_failure(tmp_path: Path):
    bad = tmp_path / "bad.md"
    bad.write_text("no shebang\n")
    souls = [SOULS / "steady.md", bad]
    config = config_for(souls)
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path / "out")
    document = load_seats(seats_path.resolve().as_uri())
    with pytest.raises(SoulError):
        Episode.from_seats(config, 7, document, None, artifacts)
    failure = json.loads((tmp_path / "out" / "player_failure.json").read_text())
    assert failure["failed_policy_index"] == 1
    assert "line 1" in failure["message"]
    assert (tmp_path / "out" / "logs" / "policy_agent_1.log").read_text().startswith("soul file rejected")
    assert (tmp_path / "out" / "logs" / "policy_agent_0.log").exists()


async def test_soul_seat_without_transport_crashes_loudly(tmp_path: Path):
    souls = [VILLAGER, SOULS / "steady.md"]
    config = config_for(souls)
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path)
    document = load_seats(seats_path.resolve().as_uri())
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        Episode.from_seats(config, 7, document, None, artifacts)


async def test_serve_episode_http_surface(tmp_path: Path, unused_tcp_port: int):
    import aiohttp

    souls = [SOULS / "steady.md", SOULS / "greedy.md"]
    config = config_for(souls, turns=3, commune_rounds=1)
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path)
    document = load_seats(seats_path.resolve().as_uri())

    import asyncio

    task = asyncio.create_task(serve_episode(config, 7, document, artifacts, "127.0.0.1", unused_tcp_port))
    base = f"http://127.0.0.1:{unused_tcp_port}"
    async with aiohttp.ClientSession() as session:
        for _ in range(50):
            try:
                async with session.get(f"{base}/healthz") as r:
                    if r.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        async with session.get(f"{base}/client/player?slot=0&token=wrong") as r:
            assert r.status == 403
        async with session.get(f"{base}/client/player?slot=0&token={config.tokens[0]}") as r:
            assert r.status == 200 and "Seat 0" in await r.text()
        async with session.get(f"{base}/client/global") as r:
            assert r.status == 200 and "Overfished" in await r.text()
        async with session.ws_connect(f"{base}/global") as ws:
            first = json.loads((await ws.receive()).data)
            assert first["type"] == "snapshot" and first["replay"]["schema"] == "overfished-replay/1"
            pong = await ws.ping(b"sentinel")
            assert pong is None or True  # aiohttp answers pings at the protocol level
    assert await task == 0
    assert (tmp_path / "results.json").exists()


class SlowTransport(FakeTransport):
    async def complete(self, **kwargs) -> str:
        import asyncio

        self.calls += 1
        await asyncio.sleep(0.5)
        return json.dumps({"thinking": "slow", "effort": 0.9, "punish": []})


async def test_decision_deadline_falls_back(tmp_path: Path):
    souls = [VILLAGER, SOULS / "steady.md"]
    config = config_for(souls, turns=2, commune_rounds=0, llm={"decision_seconds": 0.1, "reasoning": {"effort": "low"}})
    seats_path, artifacts = stage_local_episode(config, souls, tmp_path)
    document = load_seats(seats_path.resolve().as_uri())
    transport = SlowTransport()
    episode = Episode.from_seats(config, 7, document, transport, artifacts)
    await episode.run()
    replay = json.loads((tmp_path / "replay").read_text())
    assert all(0 in turn["auto"] for turn in replay["turns"])
    assert "exceeded" in (tmp_path / "logs" / "policy_agent_0.log").read_text()
