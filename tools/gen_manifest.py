"""Generate coworld_manifest_template.json from the Pydantic config so the schema never drifts.

    python3 tools/gen_manifest.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from overfished.config import GameConfig  # noqa: E402

GITHUB = "https://github.com/Metta-AI/coworld-overfished"
BLOB = f"{GITHUB}/blob/main"


def uri(path: str) -> dict:
    return {"type": "uri", "value": f"{BLOB}/{path}"}


def seats(count: int) -> list[dict]:
    return [{"name": f"Fisher {i + 1}"} for i in range(count)]


def config_schema() -> dict:
    schema = GameConfig.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Overfished game config"
    tokens = schema["properties"]["tokens"]
    assert tokens["minItems"] == 2 and tokens["maxItems"] == 16
    return schema


def results_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "required": ["scores", "pseudonyms", "turns_played", "final_stock", "collapsed"],
        "properties": {
            "scores": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 16,
                       "description": "Fish held at the end of the episode, one per seat by slot."},
            "pseudonyms": {"type": "array", "items": {"type": "string"}},
            "turns_played": {"type": "integer"},
            "final_stock": {"type": "number"},
            "capacity": {"type": "number"},
            "collapsed": {"type": "boolean", "description": "True when the lake ended below its point of no return."},
            "total_catch": {"type": "integer"},
            "models": {"type": "array", "items": {"type": "string"}},
            "llm": {"type": "object"},
        },
    }


def player(id_: str, name: str, description: str) -> dict:
    return {
        "id": id_,
        "name": name,
        "type": "player",
        "description": description,
        "file": f"souls/{id_}.md",
        "source_url": f"{GITHUB}/tree/main/souls",
    }


def variant(id_: str, name: str, description: str, **overrides) -> dict:
    count = overrides.pop("seats", 8)
    game_config = {"players": seats(count), **overrides}
    return {"id": id_, "name": name, "description": description, "game_config": game_config}


def manifest() -> dict:
    return {
        "$schema": "https://raw.githubusercontent.com/Metta-AI/coworld/main/src/coworld/coworld_manifest_schema.json",
        "tags": ["commons", "negotiation", "social", "llm", "soul-md"],
        "episode_timeout_minutes": 20,
        "game": {
            "name": "overfished",
            "description": (
                "Eight fishers share one lake with a hidden population that can collapse for good. Each turn every "
                "seat picks how hard to fish and may burn its own fish to burn a rival's; every five turns the seats "
                "hold a council with no rules but talk. A player is a soul.md: line 1 names the model, the rest is a "
                "philosophy. Score is the fish you hold at the end."
            ),
            "owner": "rohit@softmax.com",
            "player_runtime": "game-hosted",
            "config_schema": config_schema(),
            "results_schema": results_schema(),
            "replay_viewer": {"bundle": "build/static-replay-viewer", "replay_compression": "gzip"},
            "protocols": {"player": uri("docs/PROTOCOL.md"), "global": uri("docs/GLOBAL.md")},
            "docs": {
                "readme": uri("README.md"),
                "pages": [
                    {"id": "protocol.md", "title": "Soul files and the seat contract", "content": uri("docs/PROTOCOL.md")},
                    {"id": "global.md", "title": "Spectator stream and replay format", "content": uri("docs/GLOBAL.md")},
                ],
            },
            "runnable": {
                "type": "game",
                "image": "{{GAME_IMAGE}}",
                "run": ["overfished"],
                "source_url": f"{GITHUB}/tree/main",
                "resources": {"requests": {"cpu": "1", "memory": "1Gi"}, "limits": {"cpu": "2", "memory": "2Gi"}},
            },
        },
        "player": [
            player("steady", "Steady", "Scripted: 40% effort every turn, never punishes. The certification baseline and the fallback."),
            player("greedy", "Greedy", "Scripted: 100% effort every turn, never punishes."),
            player("enforcer", "Enforcer", "Scripted: 40% effort; burns one fish of up to two fishers whose last catch was 1.5x the median."),
        ],
        "variants": [
            variant("village", "Village (8 seats, 60 turns)", "The league default: 8 seats, 60 turns, a council before turn 1 and after every 5 turns."),
            variant("pond", "Pond (4 seats, 30 turns)", "A cheap smoke variant for trying a soul.", seats=4, turns=30),
            variant("quiet-lake", "Quiet lake (8 seats, no council)", "Same lake, no talking: only the ledger and punishment carry signal.", commune_rounds=0, commune_at_start=False),
            variant(
                "long-season",
                "Long season (8 seats, 200 turns)",
                "200 turns for local experiments. Hosted episodes have a 20 minute deadline, so this variant needs fast models or the wall budget cuts thinking.",
                turns=200,
                episode_wall_seconds=1000.0,
            ),
        ],
        "certification": {
            "players": [{"player_id": p} for p in ["steady", "greedy", "enforcer", "steady", "steady", "greedy", "enforcer", "steady"]],
            "game_config": {"players": seats(8), "seed": 7, "turns": 12, "commune_every": 4},
        },
    }


if __name__ == "__main__":
    target = REPO / "coworld_manifest_template.json"
    target.write_text(json.dumps(manifest(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {target}")
