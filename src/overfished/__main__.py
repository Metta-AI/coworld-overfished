"""`overfished` entrypoint.

- No arguments (the container path): Coworld mode driven by COGAME_* environment variables.
- `overfished run`: a local episode without Docker or the coworld CLI, staging soul files into an output
  directory the same way the runner would.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import secrets
import sys
from pathlib import Path

from overfished.config import GameConfig, PlayerName
from overfished.seats import SEATS_SCHEMA
from overfished.server import ArtifactPaths, choose_seed, load_seats, serve_episode, main_coworld


def stage_local_episode(config: GameConfig, souls: list[Path], out: Path) -> tuple[Path, ArtifactPaths]:
    """Write the config, staged soul files, and seats document the runner would have written."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)
    seats = []
    for slot, soul in enumerate(souls):
        data = soul.read_bytes()
        staged = out / "players" / str(slot) / "file"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(data)
        seats.append(
            {
                "slot": slot,
                "file_uri": staged.resolve().as_uri(),
                "content_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
                "log_uri": (out / "logs" / f"policy_agent_{slot}.log").resolve().as_uri(),
                "artifact_uri": (out / f"policy_artifact_{slot}.zip").resolve().as_uri(),
            }
        )
    seats_path = out / "player_seats.json"
    seats_path.write_text(
        json.dumps({"schema": SEATS_SCHEMA, "seats": seats, "player_status_uri": (out / "player_status.json").resolve().as_uri()}, indent=2)
    )
    (out / "config.json").write_text(config.model_dump_json(indent=2))
    artifacts = ArtifactPaths(
        results_uri=(out / "results.json").resolve().as_uri(),
        replay_uri=(out / "replay").resolve().as_uri(),
        failure_uri=(out / "player_failure.json").resolve().as_uri(),
    )
    return seats_path, artifacts


def run_local(args: argparse.Namespace) -> int:
    souls = [Path(p) for p in args.soul]
    base = json.loads(Path(args.config).read_text()) if args.config else {}
    base["tokens"] = [secrets.token_urlsafe(12) for _ in souls]
    base["players"] = [PlayerName(name=p.stem).model_dump() for p in souls]
    if args.turns is not None:
        base["turns"] = {"lo": args.turns, "hi": args.turns}
    if args.seed is not None:
        base["seed"] = args.seed
    config = GameConfig.model_validate(base)
    out = Path(args.out)
    seats_path, artifacts = stage_local_episode(config, souls, out)
    document = load_seats(seats_path.resolve().as_uri())
    return asyncio.run(serve_episode(config, choose_seed(config), document, artifacts, args.host, args.port))


def main() -> int:
    parser = argparse.ArgumentParser(prog="overfished")
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="run one local episode from soul files")
    run.add_argument("--soul", action="append", required=True, help="soul file; repeat once per seat")
    run.add_argument("--config", help="token-free game config JSON (a manifest variant's game_config)")
    run.add_argument("--out", required=True, help="artifact directory")
    run.add_argument("--turns", type=int, help="fix the episode length instead of sampling it")
    run.add_argument("--seed", type=int)
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.command == "run":
        return run_local(args)
    return main_coworld()


if __name__ == "__main__":
    sys.exit(main())
