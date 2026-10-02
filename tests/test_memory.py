import json

import pytest
from test_episode import SOULS, VILLAGER, config_for

from overfished.__main__ import stage_local_episode
from overfished.llm import Transport
from overfished.memory import SCRATCHPAD_MAX_BYTES, MemoryInput, MemoryView, ScratchpadStore, policy_id
from overfished.server import Episode, load_seats


def test_store_limits_and_concurrent_updates(tmp_path):
    store = ScratchpadStore(tmp_path)
    key = policy_id(b"a soul")
    assert store.read(key) == MemoryView(summary="", notes=[])
    store.append(key, "a")
    store.append(key, "b")
    assert store.read(key) == MemoryView(summary="", notes=["a", "b"])
    for _ in range(25):
        store.append(key, "é" * 8192)
    assert sum(len(note.encode()) for note in store.read(key).notes) == 20 * 16384
    with pytest.raises(ValueError, match="16384"):
        store.append(key, "é" * 8193)
    assert store.read(policy_id(b"another soul")) == MemoryView(summary="", notes=[])


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
    first = MemoryTransport({"scratchpad_append": "private previous episode note"})
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
    assert ScratchpadStore(memory).read(key).notes == ["private previous episode note", "\nnext note"]
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
    store.append(key, "original")
    await make_episode(
        tmp_path / "episode", store.root, [VILLAGER, SOULS / "steady.md"], MemoryTransport(update), 7
    )
    assert store.read(key).notes == ["original"]


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
        return web.json_response(
            {
                "model": "fixture/served",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(reply)}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", unused_tcp_port).start()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"commune_rounds": 1, "llm": {"think_turns": 0}}))
    memory = tmp_path / "memory"
    monkeypatch.delenv("COWORLD_LLM_ENDPOINT", raising=False)
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
                environment.pop("OPENROUTER_API_KEY", None)
                environment.pop("OPENROUTER_BASE_URL", None)
                config = config_for(souls, turns=1, commune_rounds=1, llm={"think_turns": 0})
                seats_path, artifacts = stage_local_episode(config, souls, out)
                (out / "memory-input.json").write_text(
                    json.dumps(
                        {
                            "protocol": "append-v1",
                            "namespace": "test",
                            "policies": {
                                policy_id(soul.read_bytes()): {
                                    **ScratchpadStore(memory).read(policy_id(soul.read_bytes())).model_dump(),
                                }
                                for soul in souls
                            },
                        }
                    )
                )
                environment.update(
                    COGAME_CONFIG_URI=(out / "config.json").as_uri(),
                    COGAME_PLAYER_SEATS_URI=seats_path.as_uri(),
                    COGAME_RESULTS_URI=artifacts.results_uri,
                    COGAME_SAVE_REPLAY_URI=artifacts.replay_uri,
                    COGAME_PLAYER_FAILURE_URI=artifacts.failure_uri,
                    COGAME_HOST="127.0.0.1",
                    COGAME_PORT="0",
                    COGAME_MEMORY_INPUT_URI=(out / "memory-input.json").as_uri(),
                    COGAME_MEMORY_OUTPUT_URI=(out / "memory-output.json").as_uri(),
                    COWORLD_LLM_ENABLED="true",
                    COWORLD_LLM_ENDPOINT=f"http://127.0.0.1:{unused_tcp_port}",
                    OPENAI_BASE_URL=f"http://127.0.0.1:{unused_tcp_port}/v1",
                    OPENAI_API_KEY="sidecar",
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
            if index:
                for key, note in json.loads((out / "memory-output.json").read_text())["notes"].items():
                    ScratchpadStore(memory).append(key, note)
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
    assert (
        ScratchpadStore(memory).read(policy_id(VILLAGER.read_bytes())).notes == ["private durable note\n"] * 2
    )


def _append_in_process(root, key, index):
    ScratchpadStore(root).append(key, f"{index}\n")


def test_concurrent_processes_do_not_lose_appends(tmp_path):
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    key = policy_id(b"shared policy")
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        futures = [executor.submit(_append_in_process, tmp_path, key, index) for index in range(16)]
        for future in futures:
            future.result(timeout=15)
    assert sorted(map(int, ScratchpadStore(tmp_path).read(key).notes)) == list(range(16))


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
    store.append(key, "original")
    souls = [VILLAGER, SOULS / "steady.md"]
    config = config_for(souls, turns=1, llm={"decision_seconds": 0.01})
    path, artifacts = stage_local_episode(config, souls, tmp_path / "episode")
    episode = Episode.from_seats(
        config, 7, load_seats(path.as_uri()), FailingTransport({}), artifacts, store.root
    )
    await episode.run()
    assert store.read(key).notes == ["original"]
    assert episode.done.is_set()


async def test_hosted_episode_without_opt_in_has_no_memory_prompts_or_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("COGAME_CONFIG_URI", "file:///hosted-config.json")
    monkeypatch.setenv("OVERFISHED_SCRATCHPAD_DIR", str(tmp_path / "ignored-local-memory"))
    monkeypatch.delenv("COGAME_MEMORY_INPUT_URI", raising=False)
    monkeypatch.delenv("COGAME_MEMORY_OUTPUT_URI", raising=False)
    transport = MemoryTransport({"scratchpad_append": "must not be written"})
    episode = await make_episode(tmp_path / "episode", tmp_path / "ignored-explicit-memory", [VILLAGER, SOULS / "steady.md"], transport, 7)
    assert episode.scratchpads is None
    assert not any("SCRATCHPAD" in observation for observation in transport.observations)
    assert not any("SCRATCHPAD" in seat.brain.system_prompt for seat in episode.seats if seat.brain is not None)
    assert not (tmp_path / "ignored-local-memory").exists()
    assert not (tmp_path / "ignored-explicit-memory").exists()


def test_hosted_snapshot_byte_limit():
    key = policy_id(b"a soul")
    data = {"protocol": "append-v1", "namespace": "league", "policies": {
        key: {"summary": "x" * (SCRATCHPAD_MAX_BYTES - 16384), "notes": ["é" * 8192]}
    }}
    assert MemoryInput.model_validate(data).policies[key].notes == ["é" * 8192]
    data["policies"][key]["summary"] += "x"
    with pytest.raises(ValueError, match="512 KiB"):
        MemoryInput.model_validate(data)


@pytest.mark.parametrize("hosted", [False, True])
async def test_memory_view_renders_unescaped_summary_and_separate_notes(tmp_path, monkeypatch, hosted):
    key = policy_id(VILLAGER.read_bytes())
    notes = ['a "quoted" lesson\nsecond line', "newest lesson"]
    summary = 'Earlier "summary"\nwith context' if hosted else ""
    memory = tmp_path / "memory"
    if hosted:
        source = tmp_path / "input.json"
        source.write_text(json.dumps({"protocol": "append-v1", "namespace": "test", "policies": {
            key: {"summary": summary, "notes": notes},
        }}))
        monkeypatch.setenv("COGAME_MEMORY_INPUT_URI", source.as_uri())
        monkeypatch.setenv("COGAME_MEMORY_OUTPUT_URI", (tmp_path / "output.json").as_uri())
    else:
        store = ScratchpadStore(memory)
        for note in notes:
            store.append(key, note)
    transport = MemoryTransport({})
    episode = await make_episode(tmp_path / "episode", memory, [VILLAGER, SOULS / "steady.md"], transport, 7)
    observation = transport.observations[0]
    assert f"Compacted summary:\n{summary}\nRecent notes (oldest first, newest last):\n" in observation
    assert 'Note 1:\na "quoted" lesson\nsecond line\n\nNote 2:\nnewest lesson' in observation
    assert "compacted summary followed by up to 20 recent notes" in episode.seats[0].brain.system_prompt


@pytest.mark.parametrize("oversized", [False, True])
async def test_identical_souls_share_contribution_budget_with_separator(tmp_path, monkeypatch, oversized):
    key = policy_id(VILLAGER.read_bytes())
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"protocol": "append-v1", "namespace": "test", "policies": {
        key: {"summary": "", "notes": []},
    }}))
    output = tmp_path / "output.json"
    monkeypatch.setenv("COGAME_MEMORY_INPUT_URI", source.as_uri())
    monkeypatch.setenv("COGAME_MEMORY_OUTPUT_URI", output.as_uri())
    note = "é" * 4095 + "x" + ("x" if oversized else "")
    transport = MemoryTransport({"scratchpad_append": note})
    await make_episode(tmp_path / "episode", None, [VILLAGER, VILLAGER], transport, 7)
    writes = [text for text in transport.observations if text.startswith("SCRATCHPAD WRITE")]
    assert len(writes) == 2
    assert all("Your seat may contribute at most 8191 UTF-8 bytes" in text for text in writes)
    assert json.loads(output.read_text())["notes"] == ({} if oversized else {key: note + "\n" + note})


async def test_memory_flush_failure_preserves_episode_results(tmp_path, monkeypatch):
    key = policy_id(VILLAGER.read_bytes())
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"protocol": "append-v1", "namespace": "test", "policies": {
        key: {"summary": "", "notes": []},
    }}))
    monkeypatch.setenv("COGAME_MEMORY_INPUT_URI", source.as_uri())
    monkeypatch.setenv("COGAME_MEMORY_OUTPUT_URI", (tmp_path / "missing" / "output.json").as_uri())
    with pytest.raises(FileNotFoundError):
        await make_episode(tmp_path / "episode", None, [VILLAGER, SOULS / "steady.md"], MemoryTransport({}), 7)
    assert len(json.loads((tmp_path / "episode" / "results.json").read_text())["scores"]) == 2
    assert (tmp_path / "episode" / "replay").exists()
