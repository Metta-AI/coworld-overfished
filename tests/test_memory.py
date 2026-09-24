import json

import pytest
from test_episode import SOULS, VILLAGER, config_for

from overfished.__main__ import stage_local_episode
from overfished.llm import Transport
from overfished.memory import SCRATCHPAD_MAX_BYTES, ScratchpadStore, policy_id
from overfished.server import Episode, load_seats


def test_store_limits_and_concurrent_updates(tmp_path):
    store = ScratchpadStore(tmp_path)
    key = policy_id(b"a soul")
    assert store.read(key) == ""
    store.write(key, "", "a")
    with pytest.raises(ValueError, match="changed"):
        store.write(key, "", "stale replacement")
    store.write(key, "", "b", append=True)
    assert store.read(key) == "ab"
    full = "é" * (SCRATCHPAD_MAX_BYTES // 2)
    store.write(key, "ab", full)
    assert store.read(key) == full
    with pytest.raises(ValueError, match="exceeds"):
        store.write(key, full, "x", append=True)
    assert store.read(key) == full
    store.write(key, full, "")
    assert store.read(key) == ""
    assert store.read(policy_id(b"another soul")) == ""


class MemoryTransport(Transport):
    def __init__(self, update):
        self.calls = self.prompt_tokens = self.completion_tokens = 0
        self.base_url = "fake"
        self.api_key = None
        self.update = update
        self.observations = []

    async def complete(self, **kwargs):
        self.calls += 1
        observation = kwargs["messages"][-1]["content"]
        self.observations.append(observation)
        if observation.startswith("SCRATCHPAD READ"):
            return json.dumps({"notebook": "remembered privately"})
        if observation.startswith("SCRATCHPAD WRITE"):
            return json.dumps(self.update)
        if observation.startswith("COUNCIL"):
            return json.dumps({"say": "hello"})
        return json.dumps({"effort": 0.4})


async def make_episode(out, memory, souls, transport, seed):
    config = config_for(souls, turns=1, identity="episode", commune_rounds=1)
    path, artifacts = stage_local_episode(config, souls, out)
    episode = Episode.from_seats(config, seed, load_seats(path.as_uri()), transport, artifacts, memory)
    await episode.run()
    return episode


async def test_memory_across_episodes_and_roster_before_opening_council(tmp_path):
    memory = tmp_path / "memory"
    souls = [VILLAGER, SOULS / "steady.md"]
    first = MemoryTransport({"scratchpad": "private previous episode note"})
    one = await make_episode(tmp_path / "one", memory, souls, first, 7)
    renamed = tmp_path / "renamed.md"
    renamed.write_bytes(VILLAGER.read_bytes())
    second = MemoryTransport({"scratchpad_append": "\nnext note"})
    two = await make_episode(tmp_path / "two", memory, [souls[1], renamed], second, 42)
    key = one.engine.policy_ids[0]
    assert two.engine.policy_ids[1] == key == policy_id(renamed.read_bytes())
    assert first.observations[0].startswith("SCRATCHPAD READ")
    assert first.observations[1].startswith("COUNCIL before turn 1")
    assert first.observations[-1].startswith("SCRATCHPAD WRITE")
    assert sum(p.startswith("SCRATCHPAD READ") for p in second.observations) == 1
    assert sum(p.startswith("SCRATCHPAD WRITE") for p in second.observations) == 1
    assert "private previous episode note" in second.observations[0]
    assert all("private previous episode note" not in p for p in second.observations[1:])
    assert all(key in p for p in second.observations)
    assert "remembered privately" in second.observations[1]
    assert ScratchpadStore(memory).read(key) == "private previous episode note\nnext note"
    for artifact in ("replay", "results.json"):
        public = (tmp_path / "two" / artifact).read_text()
        assert key in public
        assert "private previous episode note" not in public
        assert "remembered privately" not in public


@pytest.mark.parametrize(
    "update",
    [{}, {"scratchpad": 5}, {"scratchpad": "x", "scratchpad_append": "y"}, {"scratchpad": "é" * 500001}],
)
async def test_invalid_or_absent_update_preserves_memory(tmp_path, update):
    store = ScratchpadStore(tmp_path / "memory")
    key = policy_id(VILLAGER.read_bytes())
    store.write(key, "", "original")
    await make_episode(
        tmp_path / "episode", store.root, [VILLAGER, SOULS / "steady.md"], MemoryTransport(update), 7
    )
    assert store.read(key) == "original"


async def test_cli_memory_survives_process_restart(tmp_path, unused_tcp_port, monkeypatch):
    """Carry memory from a local CLI game into a new hosted-entrypoint process."""
    import asyncio
    import os
    import sys

    from aiohttp import web

    requests = []

    async def complete(request):
        payload = await request.json()
        observation = payload["messages"][-1]["content"]
        requests.append((request.headers["X-Coworld-Player-Slot"], observation))
        if observation.startswith("SCRATCHPAD READ"):
            reply = {"notebook": "private carried note" if "private durable note" in observation else ""}
        elif observation.startswith("SCRATCHPAD WRITE"):
            reply = {"scratchpad_append": "private durable note\n"}
        elif observation.startswith("COUNCIL"):
            reply = {"say": "hello"}
        else:
            # These fields must never write memory during play.
            reply = {
                "effort": 0.2 if "private carried note" in observation else 0.7,
                "scratchpad": "illegal mid-game replacement",
            }
        return web.json_response({"choices": [{"message": {"content": json.dumps(reply)}}]})

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"commune_rounds": 1, "llm": {"think_turns": 0}}))
    memory = tmp_path / "memory"
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setenv("OPENROUTER_BASE_URL", f"http://127.0.0.1:{unused_tcp_port}")
    try:
        for index in range(2):
            out = tmp_path / f"episode-{index}"
            souls = [VILLAGER, SOULS / "steady.md"]
            if index:
                souls.reverse()
            environment = os.environ.copy()
            if index:
                config = config_for(souls, turns=1, commune_rounds=1, llm={"think_turns": 0})
                seats_path, artifacts = stage_local_episode(config, souls, out)
                environment.update(
                    COGAME_CONFIG_URI=(out / "config.json").as_uri(),
                    COGAME_PLAYER_SEATS_URI=seats_path.as_uri(),
                    COGAME_RESULTS_URI=artifacts.results_uri,
                    COGAME_SAVE_REPLAY_URI=artifacts.replay_uri,
                    COGAME_PLAYER_FAILURE_URI=artifacts.failure_uri,
                    COGAME_HOST="127.0.0.1",
                    COGAME_PORT="0",
                    OVERFISHED_SCRATCHPAD_DIR=str(memory),
                    AWS_ENDPOINT_URL_BEDROCK_RUNTIME=f"http://127.0.0.1:{unused_tcp_port}",
                )
                arguments = []
            else:
                arguments = [
                    "run",
                    "--out",
                    str(out),
                    "--scratchpad-dir",
                    str(memory),
                    "--turns",
                    "1",
                    "--seed",
                    "1",
                    "--port",
                    "0",
                    "--config",
                    str(config_path),
                    "--soul",
                    str(souls[0]),
                    "--soul",
                    str(souls[1]),
                ]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "overfished",
                *arguments,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            assert process.returncode == 0, stdout.decode()
            results = json.loads((out / "results.json").read_text())
            assert results["policy_ids"][index] == policy_id(VILLAGER.read_bytes())
            replay = json.loads((out / "replay").read_text())
            assert replay["turns"][0]["effort"][index] == (0.2 if index else 0.7)
            assert "private" not in json.dumps(replay)
    finally:
        await runner.cleanup()
    reads = [(slot, text) for slot, text in requests if text.startswith("SCRATCHPAD READ")]
    assert [slot for slot, _ in reads] == ["0", "1"]
    assert "private durable note" not in reads[0][1]
    assert "private durable note" in reads[1][1]
    assert ScratchpadStore(memory).read(policy_id(VILLAGER.read_bytes())) == "private durable note\n" * 2


def _append_in_process(root, key, index):
    ScratchpadStore(root).write(key, "", f"{index}\n", append=True)


def test_concurrent_processes_do_not_lose_appends(tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    key = policy_id(b"shared policy")
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        futures = [executor.submit(_append_in_process, tmp_path, key, index) for index in range(16)]
        for future in futures:
            future.result(timeout=15)
    assert sorted(map(int, ScratchpadStore(tmp_path).read(key).splitlines())) == list(range(16))


@pytest.mark.parametrize("error", ["transport", "timeout", "invalid"])
async def test_failed_boundary_calls_preserve_memory(tmp_path, error):
    import asyncio

    from overfished.llm import LlmError

    class FailingTransport(MemoryTransport):
        async def complete(self, **kwargs):
            if kwargs["messages"][-1]["content"].startswith("SCRATCHPAD"):
                if error == "transport":
                    raise LlmError("unavailable")
                if error == "timeout":
                    await asyncio.sleep(1)
                return "not JSON"
            return await super().complete(**kwargs)

    store = ScratchpadStore(tmp_path / "memory")
    key = policy_id(VILLAGER.read_bytes())
    store.write(key, "", "original")
    souls = [VILLAGER, SOULS / "steady.md"]
    config = config_for(souls, turns=1, llm={"decision_seconds": 0.01})
    path, artifacts = stage_local_episode(config, souls, tmp_path / "episode")
    episode = Episode.from_seats(
        config, 7, load_seats(path.as_uri()), FailingTransport({}), artifacts, store.root
    )
    await episode.run()
    assert store.read(key) == "original"
    assert episode.done.is_set()
