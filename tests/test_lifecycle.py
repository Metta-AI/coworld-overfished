import asyncio
import json
from pathlib import Path

import pytest
from test_episode import SOULS, config_for

from overfished.__main__ import stage_local_episode
from overfished.lifecycle import OwnershipUnsettled
from overfished.scripted import ScriptedView, scripted_policy
from overfished.server import Episode, load_seats


class UncancelledFuture(asyncio.Future):
    def cancel(self, msg=None):
        return False


async def test_unjoined_actor_preserves_writable_spool_and_withholds_public_completion(tmp_path, monkeypatch):
    destination = tmp_path / "complete.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", destination.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "unjoined-owner")
    monkeypatch.setenv("COWORLD_GAME_VERSION", "test")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", "a" * 40)
    monkeypatch.setattr(
        "overfished.server.cleanup_deadline", lambda: asyncio.get_running_loop().time() + 0.04
    )
    souls = [SOULS / "steady.md", SOULS / "greedy.md"]
    config = config_for(souls, turns=1, commune_rounds=0, commune_at_start=False)
    seats, artifacts = stage_local_episode(config, souls, tmp_path)
    episode = Episode.from_seats(config, 7, load_seats(seats.as_uri()), None, artifacts)
    blocked = UncancelledFuture()
    entered = asyncio.Event()

    async def actor(self, seat, think_turns):
        entered.set()
        return await blocked

    monkeypatch.setattr(Episode, "fishing_decision", actor)
    task = asyncio.create_task(episode.run())
    try:
        await entered.wait()
        task.cancel()
        with pytest.raises(OwnershipUnsettled):
            await asyncio.wait_for(asyncio.shield(task), 0.2)
        assert episode.tasks and all(not task.done() for task in episode.tasks)
        assert not destination.exists()
        assert not Path(artifacts.results_uri.removeprefix("file://")).exists()
        assert not Path(artifacts.replay_uri.removeprefix("file://")).exists()
        assert not episode.trajectory.spool.closed
        episode.trajectory.spool.write('{"late":"actual still-owned write"}\n')
        episode.trajectory.spool.flush()
        assert "actual still-owned write" in episode.trajectory.decisions_path.read_text()
    finally:
        blocked.set_result(None)
        await asyncio.wait(episode.tasks)
        episode.trajectory.spool.close()
        for seat in episode.seats:
            seat.log.close()


def test_scripted_teacher_is_invariant_to_hidden_lake_and_other_private_actions():
    from overfished.engine import Action, Engine

    souls = [SOULS / "steady.md", SOULS / "greedy.md"]
    engine = Engine(config_for(souls), 7, ["first", "second"])
    engine.resolve_turn([Action(effort=0.4), Action(effort=0.5)])
    before = ScriptedView.from_engine(engine, 0)
    teacher = scripted_policy("enforcer", "")
    action = teacher.act(before)
    engine.stock *= 0.5
    engine.last_effort[1] = 0.99
    engine.turns[-1].effort[1] = 0.99
    after = ScriptedView.from_engine(engine, 0)
    assert before == after
    assert teacher.act(after) == action
    assert "stock" not in json.dumps(before.model_dump(mode="json"))


@pytest.mark.parametrize("repeat", [False, True])
async def test_actual_process_signal_joins_partial_native_reader_before_private_seal(tmp_path, repeat):
    import base64
    import os
    import signal
    import sys
    from uuid import uuid4

    from test_episode import VILLAGER

    call_id = str(uuid4())
    prefix = b'{"private":"SIGNAL_PRIVATE_SENTINEL\xe2'
    observed = asyncio.Event()
    released = asyncio.Event()

    async def handle(reader, writer):
        headers = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in headers.split(b"\r\n")
            if line.lower().startswith(b"content-length:")
        )
        await reader.readexactly(length)
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\nX-Softmax-Llm-Call-Id: {call_id}\r\n\r\n".encode()
            + prefix
        )
        await writer.drain()
        observed.set()
        while await reader.read(1024):
            pass
        writer.close()
        await writer.wait_closed()
        released.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    souls = [VILLAGER, SOULS / "steady.md"]
    config = config_for(souls, turns=1, commune_rounds=0, commune_at_start=False)
    seats, artifacts = stage_local_episode(config, souls, tmp_path)
    destination = tmp_path / "complete.jsonl"
    environment = os.environ | {
        "COGAME_CONFIG_URI": (tmp_path / "config.json").as_uri(),
        "COGAME_PLAYER_SEATS_URI": seats.as_uri(),
        "COGAME_RESULTS_URI": artifacts.results_uri,
        "COGAME_SAVE_REPLAY_URI": artifacts.replay_uri,
        "COGAME_SAVE_TRAJECTORY_URI": destination.as_uri(),
        "COGAME_HOST": "127.0.0.1",
        "COGAME_PORT": "0",
        "COWORLD_EPISODE_ID": "signal-fixture",
        "COWORLD_GAME_VERSION": "test",
        "COWORLD_SOURCE_REVISION": "a" * 40,
        "COWORLD_LLM_ENDPOINT": f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}",
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "overfished",
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        await asyncio.wait_for(observed.wait(), 5)
        pending = destination.with_name(destination.name + ".pending")
        for _ in range(100):
            if pending.exists() and base64.b64encode(prefix).decode() in pending.read_text():
                break
            await asyncio.sleep(0.01)
        assert base64.b64encode(prefix).decode() in pending.read_text()
        process.send_signal(signal.SIGTERM)
        if repeat:
            process.send_signal(signal.SIGINT)
            process.send_signal(signal.SIGTERM)
        output, _ = await asyncio.wait_for(process.communicate(), 3)
        await asyncio.wait_for(released.wait(), 1)
        assert process.returncode == 0, output.decode()
        assert b"SIGNAL_PRIVATE_SENTINEL" not in output
        complete = json.loads(destination.read_text())
        assert complete["episode"]["status"] == "truncated"
        assert complete["episode"]["participant_outcomes"] == {}
        attempt = complete["episode"]["outcome"]["unapplied_attempts"][0]["attempt"]
        assert attempt["platform_call_id"] == call_id
        assert base64.b64decode(attempt["response_body_b64"], validate=True) == prefix
        assert attempt["raw_response"] is None and attempt["http_status"] == 200
        assert attempt["response_complete"] is False and attempt["response_reader_joined"] is True
        assert not Path(artifacts.results_uri.removeprefix("file://")).exists()
        assert not Path(artifacts.replay_uri.removeprefix("file://")).exists()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        server.close()
        await server.wait_closed()


async def test_unjoined_actual_http_surface_withholds_final_artifacts(tmp_path, monkeypatch):
    from aiohttp import web

    from overfished.server import serve_episode

    souls = [SOULS / "steady.md", SOULS / "greedy.md"]
    config = config_for(souls, turns=1, commune_rounds=0, commune_at_start=False)
    seats, artifacts = stage_local_episode(config, souls, tmp_path)
    destination = tmp_path / "complete.jsonl"
    monkeypatch.setenv("COGAME_SAVE_TRAJECTORY_URI", destination.as_uri())
    monkeypatch.setenv("COWORLD_EPISODE_ID", "observer-unjoined")
    monkeypatch.setenv("COWORLD_GAME_VERSION", "test")
    monkeypatch.setenv("COWORLD_SOURCE_REVISION", "a" * 40)
    episode = Episode.from_seats(config, 7, load_seats(seats.as_uri()), None, artifacts)
    monkeypatch.setattr(Episode, "from_seats", lambda *args: episode)
    monkeypatch.setattr(
        "overfished.server.cleanup_deadline", lambda: asyncio.get_running_loop().time() + 0.04
    )
    blocked = UncancelledFuture()
    finished = asyncio.Event()
    cleanup = web.AppRunner.cleanup

    async def pause_close(self):
        await blocked
        await cleanup(self)
        finished.set()

    monkeypatch.setattr(web.AppRunner, "cleanup", pause_close)
    try:
        with pytest.raises(OwnershipUnsettled, match="HTTP observer"):
            await serve_episode(config, 7, load_seats(seats.as_uri()), artifacts, "127.0.0.1", 0)
        assert not episode.surface_joined and not episode.done.is_set()
        assert not destination.exists() and not episode.trajectory.spool.closed
        assert not Path(artifacts.results_uri.removeprefix("file://")).exists()
        assert not Path(artifacts.replay_uri.removeprefix("file://")).exists()
    finally:
        blocked.set_result(None)
        await asyncio.wait_for(finished.wait(), 1)
        episode.trajectory.spool.close()
        for seat in episode.seats:
            seat.log.close()
