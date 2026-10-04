"""Export private complete episodes using the production scripted-seat runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from overfished.__main__ import stage_local_episode
from overfished.config import GameConfig
from overfished.lifecycle import main_owned
from overfished.scripted import SCRIPTED_NAMES
from overfished.server import Episode, load_seats
from overfished.soul import parse_soul
from tools.gen_manifest import manifest

ROOT = Path(__file__).resolve().parents[1]


async def export(args: argparse.Namespace, source: str) -> None:
    if args.games < 10 or args.first_seed < 1:
        raise ValueError("at least ten complete games and a positive first seed are required")
    base = next(item["game_config"] for item in manifest()["variants"] if item["id"] == args.variant)
    count = len(base["players"])
    if len(args.soul) != count:
        raise ValueError(f"{args.variant} requires {count} concrete soul artifacts")
    soul_paths = [path.resolve() for path in args.soul]
    config = GameConfig.model_validate({**base, "tokens": [f"teacher-{seat}" for seat in range(count)]})
    souls = [parse_soul(path.read_bytes(), config.model_aliases, set(SCRIPTED_NAMES)) for path in soul_paths]
    if not all(soul.scripted for soul in souls):
        raise ValueError("this exporter requires server-owned scripted teachers")
    os.umask(0o077)
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    runs = []
    for seed in range(args.first_seed, args.first_seed + args.games):
        episode_id = f"overfished-{args.variant}-{seed}"
        episode_dir = output / episode_id
        os.environ.update(
            COWORLD_EPISODE_ID=episode_id,
            COWORLD_GAME_VERSION=f"source-{source}",
            COWORLD_SOURCE_REVISION=source,
            COGAME_SAVE_TRAJECTORY_URI=(episode_dir / "complete-episode.jsonl").as_uri(),
        )
        seats_path, artifacts = stage_local_episode(config, soul_paths, episode_dir)
        episode = Episode.from_seats(config, seed, load_seats(seats_path.as_uri()), None, artifacts)
        await episode.run()
        episode.finalize()
        assert episode.engine.finished and episode.trajectory is not None
        corpus = episode_dir / "complete-episode.jsonl"
        with corpus.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        runs.append(
            {
                "episode_id": episode_id,
                "seed": seed,
                "seed_family": f"overfished-{seed}",
                "decisions": episode.trajectory.decision_count,
                "complete_episode_file": str(corpus),
                "complete_episode_sha256": digest,
                "scores": episode.engine.results()["scores"],
            }
        )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1",
                "game": "overfished",
                "source_revision": source,
                "variant": args.variant,
                "configuration": config.model_dump(mode="json"),
                "teacher_policies": [f"scripted/{soul.scripted_name}" for soul in souls],
                "policy_ids": episode.policy_ids,
                "memory_mode": "disabled",
                "review_status": "unreviewed_source_owned_teacher_collection",
                "training_labels": False,
                "runs": runs,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"complete private episodes={len(runs)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--first-seed", type=int, default=1)
    parser.add_argument("--variant", choices=[item["id"] for item in manifest()["variants"]], required=True)
    parser.add_argument("--soul", type=Path, action="append", required=True)
    args = parser.parse_args()
    if subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, text=True, capture_output=True, check=True, timeout=10
    ).stdout.strip():
        raise ValueError("commit the qualified source before generating a pinned corpus")
    source = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True, timeout=10
    ).stdout.strip()
    main_owned(export(args, source))
