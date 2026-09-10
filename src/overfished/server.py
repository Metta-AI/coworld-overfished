"""Episode driver and the Coworld HTTP/WebSocket surface.

One process owns the whole episode: it stages nothing (the runner did), reads the seats document, parses
every soul, then alternates councils and fishing turns until the last turn, writing seat logs as it goes
and results last.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
from aiohttp import web

from overfished.config import GameConfig
from overfished.engine import Action, Engine, Speech
from overfished.llm import (
    SeatBrain,
    Transport,
    council_observation,
    decide,
    mechanics_block,
    transport_from_env,
    turn_observation,
)
from overfished.scripted import SCRIPTED_NAMES, ScriptedPolicy, fallback_action, scripted_policy
from overfished.seats import (
    SeatLog,
    SeatsDocument,
    load_seats,
    local_path,
    read_uri,
    write_json_atomic,
    write_player_failure,
    write_player_status,
)
from overfished.soul import Soul, SoulError, parse_soul
from overfished.viewer_build import DEFAULT_VIEWER_DIR, build_index_html


def log(message: str) -> None:
    print(f"overfished: {message}", flush=True)


@dataclass
class SeatRuntime:
    slot: int
    soul: Soul
    log: SeatLog
    scripted: ScriptedPolicy | None
    brain: SeatBrain | None

    def note(self, line: str) -> None:
        self.log.write(f"[{time.strftime('%H:%M:%S')}] {line}")


@dataclass
class ArtifactPaths:
    results_uri: str
    replay_uri: str
    failure_uri: str


@dataclass
class Episode:
    config: GameConfig
    engine: Engine
    seats: list[SeatRuntime]
    transport: Transport | None
    artifacts: ArtifactPaths
    status_uri: str | None
    started: float = field(default_factory=time.monotonic)
    phase: str = "starting"
    subscribers: set[web.WebSocketResponse] = field(default_factory=set)
    seat_subscribers: dict[int, set[web.WebSocketResponse]] = field(default_factory=dict)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    # ---- construction ----------------------------------------------------------------

    @classmethod
    def from_seats(
        cls,
        config: GameConfig,
        seed: int,
        document: SeatsDocument,
        transport: Transport | None,
        artifacts: ArtifactPaths,
    ) -> "Episode":
        if len(document.seats) != config.num_players:
            raise ValueError(f"seats document has {len(document.seats)} seats but the config seats {config.num_players}")
        engine = Engine(config, seed)
        seats: list[SeatRuntime] = []
        logs = [SeatLog(local_path(seat.log_uri)) for seat in document.seats]
        for seat, seat_log in zip(document.seats, logs, strict=True):
            data = read_uri(seat.file_uri)
            try:
                soul = parse_soul(data, config.model_aliases, set(SCRIPTED_NAMES))
            except SoulError as error:
                seat_log.write(f"soul file rejected: {error}")
                for other in logs:
                    other.close()
                write_player_failure(artifacts.failure_uri, seat.slot, f"seat {seat.slot}: soul file rejected: {error}")
                raise
            scripted = scripted_policy(soul.scripted_name, soul.text) if soul.scripted else None
            brain = None
            if scripted is None:
                if transport is None:
                    for other in logs:
                        other.close()
                    raise RuntimeError(
                        f"seat {seat.slot} needs model {soul.model} but no LLM transport is configured: set "
                        "OPENROUTER_API_KEY locally, or run hosted where the sidecar provides AWS_ENDPOINT_URL_BEDROCK_RUNTIME"
                    )
                brain = SeatBrain(
                    slot=seat.slot,
                    soul=soul,
                    system_prompt=soul.text
                    + "\n\n"
                    + mechanics_block(config, engine.pseudonyms[seat.slot], config.num_players, engine.lake.boat_capacity),
                )
            runtime = SeatRuntime(slot=seat.slot, soul=soul, log=seat_log, scripted=scripted, brain=brain)
            runtime.note(
                f"seated as {engine.pseudonyms[seat.slot]} (slot {seat.slot}); model {soul.model}; "
                f"soul {len(data)} bytes sha256 {seat.content_hash}"
            )
            runtime.note("this log is private to the seat: it holds prompts, private thinking, and notebook updates")
            seats.append(runtime)
        return cls(
            config=config,
            engine=engine,
            seats=seats,
            transport=transport,
            artifacts=artifacts,
            status_uri=document.player_status_uri,
        )

    # ---- live feed -------------------------------------------------------------------

    def snapshot(self) -> dict:
        return {"type": "snapshot", "phase": self.phase, "live": not self.done.is_set(), "replay": self.engine.replay()}

    async def broadcast(self, message: dict) -> None:
        data = json.dumps(message)
        stale = []
        for ws in list(self.subscribers):
            if ws.closed:
                stale.append(ws)
                continue
            await ws.send_str(data)
        for ws in stale:
            self.subscribers.discard(ws)

    async def seat_event(self, slot: int, line: str) -> None:
        subscribers = self.seat_subscribers.get(slot)
        if not subscribers:
            return
        data = json.dumps({"type": "log", "slot": slot, "line": line})
        for ws in list(subscribers):
            if ws.closed:
                subscribers.discard(ws)
                continue
            await ws.send_str(data)

    # ---- budget ----------------------------------------------------------------------

    def think_turns_now(self) -> int:
        remaining = self.config.episode_wall_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            return -1
        if remaining < 0.25 * self.config.episode_wall_seconds:
            return 0
        return self.config.llm.think_turns

    # ---- decisions -------------------------------------------------------------------

    async def fishing_decision(self, seat: SeatRuntime, think_turns: int) -> Action:
        if seat.scripted is not None:
            action = seat.scripted.act(self.engine, seat.slot)
            seat.note(f"turn {self.engine.turn}: scripted {seat.scripted.name} -> effort {action.effort:.2f}, punish {[p.model_dump() for p in action.punish]}")
            return action
        assert seat.brain is not None and self.transport is not None
        if think_turns < 0:
            action = fallback_action(self.engine, seat.slot)
            seat.note(f"turn {self.engine.turn}: LLM wall budget exhausted; fallback effort {action.effort:.2f}")
            return action
        observation = turn_observation(self.engine, seat.slot, seat.brain.notebook)
        seat.note(f"turn {self.engine.turn}: observation\n{observation}")

        def note(line: str) -> None:
            seat.note(f"turn {self.engine.turn}: {line}")

        decision = await decide(
            seat.brain, self.transport, self.engine, observation=observation, council=False, think_turns=think_turns, log=note
        )
        for reply in decision.transcript:
            seat.note(f"turn {self.engine.turn}: raw reply\n{reply}")
        if decision.action is None:
            action = fallback_action(self.engine, seat.slot)
            seat.note(f"turn {self.engine.turn}: no valid action; fallback effort {action.effort:.2f}")
            return action
        seat.note(
            f"turn {self.engine.turn}: effort {decision.action.effort:.2f}, punish "
            f"{[(self.engine.pseudonyms[p.target], p.fish) for p in decision.action.punish]}; notebook now {len(seat.brain.notebook)} chars"
        )
        return decision.action

    async def council_decision(
        self,
        seat: SeatRuntime,
        round_index: int,
        order: list[int],
        earlier: list[list[Speech]],
        so_far: list[Speech],
        think_turns: int,
    ) -> Speech:
        if seat.scripted is not None:
            text = seat.scripted.say(self.engine, seat.slot, round_index)
            return Speech(slot=seat.slot, text=text[: self.config.llm.say_max_chars])
        assert seat.brain is not None and self.transport is not None
        if think_turns < 0:
            seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: LLM wall budget exhausted; silent")
            return Speech(slot=seat.slot, text="", auto=True)
        observation = council_observation(self.engine, seat.slot, seat.brain.notebook, round_index, order, earlier, so_far)
        seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: observation\n{observation}")

        def note(line: str) -> None:
            seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: {line}")

        decision = await decide(
            seat.brain, self.transport, self.engine, observation=observation, council=True, think_turns=think_turns, log=note
        )
        for reply in decision.transcript:
            seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: raw reply\n{reply}")
        if decision.say is None:
            seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: no valid message; silent")
            return Speech(slot=seat.slot, text="", auto=True)
        seat.note(f"council before turn {self.engine.turn} round {round_index + 1}: says {decision.say!r}")
        return Speech(slot=seat.slot, text=decision.say)

    # ---- phases ----------------------------------------------------------------------

    async def hold_council(self) -> None:
        """Fishers speak one at a time in a rotating order; every round of a council keeps the same order."""
        self.phase = "council"
        order = self.engine.council_order()
        rounds: list[list[Speech]] = []
        for round_index in range(self.config.commune_rounds):
            speeches: list[Speech] = []
            for slot in order:
                think_turns = self.think_turns_now()
                speech = await self.council_decision(self.seats[slot], round_index, order, rounds, speeches, think_turns)
                speeches.append(speech)
                await self.broadcast(
                    {
                        "type": "speech",
                        "before_turn": self.engine.turn,
                        "round": round_index,
                        "order": order,
                        "speeches": [s.model_dump() for s in speeches],
                    }
                )
            rounds.append(speeches)
        record = self.engine.record_commune(rounds, order)
        log(f"council before turn {record.before_turn}: {sum(1 for r in rounds for s in r if s.text)} messages")
        await self.broadcast({"type": "commune", "commune": record.model_dump()})

    async def fishing_turn(self) -> None:
        self.phase = "fishing"
        think_turns = self.think_turns_now()
        actions = await asyncio.gather(*(self.fishing_decision(seat, think_turns) for seat in self.seats))
        record = self.engine.resolve_turn(list(actions))
        log(
            f"turn {record.t}: stock {record.stock_before:.0f} -> {record.stock_after:.0f}, "
            f"catch {sum(record.catch)}, punishments {len(record.punish)}, auto {record.auto}"
        )
        await self.broadcast({"type": "turn", "turn": record.model_dump()})

    async def run(self) -> None:
        log(
            f"episode start: {self.config.num_players} seats, {self.engine.turn_limit} turns (hidden from seats), seed {self.engine.seed}, "
            f"lake capacity {self.engine.lake.capacity:.0f} (hidden from seats), transport "
            f"{self.transport.describe if self.transport else 'none (scripted seats only)'}"
        )
        while not self.engine.finished:
            if self.engine.commune_due():
                await self.hold_council()
            await self.fishing_turn()
        self.phase = "done"
        self.finalize()
        await self.broadcast({"type": "end", "scores": self.engine.results()["scores"]})
        self.done.set()

    def finalize(self) -> None:
        """Seat logs and status first, replay next, results last: results are the completion marker."""
        results = self.engine.results()
        if self.config.reveal_models:
            results["models"] = [seat.soul.model for seat in self.seats]
        states = []
        for seat in self.seats:
            brain = seat.brain
            seat.note(
                f"episode over: {self.engine.fish[seat.slot]} fish"
                + (f"; {brain.calls} model calls, {brain.failures} failures, {brain.fallbacks} fallbacks" if brain else "")
            )
            seat.log.close()
            states.append({"slot": seat.slot, "state": "exited", "exit_code": 0, "reason": "Completed"})
        write_player_status(self.status_uri, states)
        replay = self.engine.replay()
        if self.config.reveal_models:
            for player, seat in zip(replay["players"], self.seats, strict=True):
                player["model"] = seat.soul.model
        replay_path = local_path(self.artifacts.replay_uri)
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        replay_path.write_bytes(json.dumps(replay, separators=(",", ":")).encode("utf-8"))
        if self.transport is not None:
            results["llm"] = {
                "calls": self.transport.calls,
                "prompt_tokens": self.transport.prompt_tokens,
                "completion_tokens": self.transport.completion_tokens,
                "wall_seconds": round(time.monotonic() - self.started, 1),
            }
        write_json_atomic(local_path(self.artifacts.results_uri), results)
        log(f"episode over: scores {results['scores']}, final stock {results['final_stock']}, collapsed {results['collapsed']}")


# ---- HTTP surface ---------------------------------------------------------------------------


def _viewer_html() -> str:
    source = Path(os.environ.get("OVERFISHED_VIEWER_DIR", str(DEFAULT_VIEWER_DIR)))
    return build_index_html(source)


def make_app(episode: Episode | None, config: GameConfig | None, replay_bytes: bytes | None) -> web.Application:
    viewer_html = _viewer_html()
    app = web.Application()

    async def healthz(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    def check_token(request: web.Request) -> int:
        if config is None:
            raise web.HTTPNotFound(text="replay mode has no player seats")
        slot_text = request.query.get("slot", "")
        token = request.query.get("token", "")
        if not slot_text.isdecimal() or int(slot_text) >= config.num_players:
            raise web.HTTPForbidden(text="unknown slot")
        slot = int(slot_text)
        if not token or not secrets.compare_digest(token, config.tokens[slot]):
            raise web.HTTPForbidden(text="invalid token")
        return slot

    async def client_player(request: web.Request) -> web.Response:
        slot = check_token(request)
        assert episode is not None
        name = episode.engine.pseudonyms[slot]
        seat = episode.seats[slot]
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Overfished seat {slot}</title>
<style>body{{font:14px/1.5 -apple-system,system-ui,sans-serif;background:#fffdf4;color:#111827;margin:2rem;max-width:56rem}}
h1{{font-family:Georgia,serif}} pre{{white-space:pre-wrap;background:#f8f6ef;padding:1rem;border:1px solid #e4dac8}}</style></head>
<body><h1>Seat {slot}: {name}</h1>
<p>Model <code>{seat.soul.model}</code>. This page is private to the seat's token. Souls play themselves; nothing here accepts input.
The private log streams below.</p><pre id="log"></pre>
<script>const q=new URLSearchParams(location.search);const ws=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/player?slot='+q.get('slot')+'&token='+q.get('token'));
ws.onmessage=(e)=>{{const m=JSON.parse(e.data);if(m.type==='log'){{document.getElementById('log').textContent+=m.line+"\\n";}}}};</script></body></html>"""
        return web.Response(text=body, content_type="text/html")

    async def player_ws(request: web.Request) -> web.WebSocketResponse:
        slot = check_token(request)
        assert episode is not None
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        episode.seat_subscribers.setdefault(slot, set()).add(ws)
        await ws.send_str(json.dumps({"type": "seat", "slot": slot, "pseudonym": episode.engine.pseudonyms[slot]}))
        async for _message in ws:
            pass
        episode.seat_subscribers[slot].discard(ws)
        return ws

    async def client_global(_request: web.Request) -> web.Response:
        return web.Response(text=viewer_html, content_type="text/html")

    async def global_ws(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=None)
        await ws.prepare(request)
        if episode is not None:
            episode.subscribers.add(ws)
            await ws.send_str(json.dumps(episode.snapshot()))
        else:
            await ws.send_str(json.dumps({"type": "replay", "replay": json.loads(replay_bytes or b"{}")}))
        async for _message in ws:
            pass
        if episode is not None:
            episode.subscribers.discard(ws)
        return ws

    async def client_replay(_request: web.Request) -> web.Response:
        return web.Response(text=viewer_html, content_type="text/html")

    async def replay_json(_request: web.Request) -> web.Response:
        if replay_bytes is None:
            raise web.HTTPNotFound(text="not in replay mode")
        return web.Response(body=replay_bytes, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/client/player", client_player)
    app.router.add_get("/player", player_ws)
    app.router.add_get("/client/global", client_global)
    app.router.add_get("/global", global_ws)
    app.router.add_get("/client/replay", client_replay)
    app.router.add_get("/replay", global_ws)
    app.router.add_get("/replay.json", replay_json)
    return app


# ---- entrypoints ----------------------------------------------------------------------------


def load_config(uri: str) -> GameConfig:
    return GameConfig.model_validate_json(read_uri(uri))


def choose_seed(config: GameConfig) -> int:
    return config.seed if config.seed > 0 else random.SystemRandom().randrange(1, 2**31)


async def serve_episode(config: GameConfig, seed: int, document: SeatsDocument, artifacts: ArtifactPaths, host: str, port: int) -> int:
    """Coworld mode: HTTP surface plus one episode. Returns the process exit code."""
    async with aiohttp.ClientSession() as session:
        transport = transport_from_env(session, config.llm.timeout_seconds)
        try:
            episode = Episode.from_seats(config, seed, document, transport, artifacts)
        except SoulError as error:
            log(f"terminal player failure declared: {error}")
            return 0
        app = make_app(episode, config, None)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()
        log(f"listening on {host}:{port}")
        try:
            await episode.run()
            # Give the runner a moment to notice results and any viewer to receive the end frame.
            await asyncio.sleep(2.0)
        finally:
            await runner.cleanup()
    return 0


async def serve_replay(replay_uri: str, host: str, port: int) -> int:
    replay_bytes = read_uri(replay_uri) if replay_uri.startswith("file://") else await _fetch(replay_uri)
    app = make_app(None, None, replay_bytes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log(f"replay mode listening on {host}:{port}")
    while True:
        await asyncio.sleep(3600)


async def _fetch(uri: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(uri) as response:
            if response.status != 200:
                raise RuntimeError(f"replay fetch failed: HTTP {response.status} for {uri}")
            return await response.read()


def main_coworld() -> int:
    host = os.environ.get("COGAME_HOST", "0.0.0.0")
    port = int(os.environ.get("COGAME_PORT", "8080"))
    replay_uri = os.environ.get("COGAME_LOAD_REPLAY_URI", "").strip()
    if replay_uri:
        return asyncio.run(serve_replay(replay_uri, host, port))
    config = load_config(os.environ["COGAME_CONFIG_URI"])
    document = load_seats(os.environ["COGAME_PLAYER_SEATS_URI"])
    workdir = local_path(os.environ["COGAME_RESULTS_URI"]).parent
    artifacts = ArtifactPaths(
        results_uri=os.environ["COGAME_RESULTS_URI"],
        replay_uri=os.environ["COGAME_SAVE_REPLAY_URI"],
        failure_uri=os.environ.get("COGAME_PLAYER_FAILURE_URI", (workdir / "player_failure.json").as_uri()),
    )
    return asyncio.run(serve_episode(config, choose_seed(config), document, artifacts, host, port))


if __name__ == "__main__":
    sys.exit(main_coworld())
