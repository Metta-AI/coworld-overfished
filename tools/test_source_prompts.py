"""Complete production source exports, compared across wording and original source."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from overfished.prompts import PromptConfig

ROOT = Path(__file__).resolve().parents[1]


def export(root: Path, output: Path, variant: str, wording: str, seed: int, original: bool = False) -> dict:
    catalog = json.loads((root / "coworld_manifest_template.json").read_text())
    base = next(item["game_config"] for item in catalog["variants"] if item["id"] == variant)
    names = ("steady", "greedy", "enforcer", "steady")
    command = [
        sys.executable,
        "-m",
        "tools.export_posttrain",
        str(output),
        "--variant",
        variant,
        "--games",
        "10",
    ]
    command += [
        arg
        for slot in range(len(base["players"]))
        for arg in ("--soul", str(root / f"souls/{names[slot % 4]}.md"))
    ]
    if not original:
        command += ["--prompt-variant", wording, "--prompt-seed", str(seed)]
    subprocess.run(command, cwd=root, env={**os.environ, "PYTHONPATH": str(root / "src")}, check=True)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["training_labels"] is False
    assert manifest["review_status"] == "unreviewed_source_owned_teacher_collection"
    assert len(manifest["runs"]) == 10
    assert len({run["seed_family"] for run in manifest["runs"]}) == 10
    for run in manifest["runs"]:
        source = Path(run["complete_episode_file"])
        assert source.stat().st_mode & 0o777 == 0o600
        with source.open("rb") as handle:
            assert hashlib.file_digest(handle, "sha256").hexdigest() == run["complete_episode_sha256"]
    return manifest


def compare(reference: dict, candidate: dict, wording: str, seed: int) -> None:
    assert reference["policy_ids"] == candidate["policy_ids"]
    assert reference["teacher_policies"] == candidate["teacher_policies"]
    assert reference["memory_mode"] == candidate["memory_mode"] == "disabled"
    for old_run, new_run in zip(reference["runs"], candidate["runs"], strict=True):
        selection = PromptConfig.model_validate(
            {"promptVariant": wording, "promptSeed": new_run["prompt_configuration"]["seed"]}
        ).resolve()
        before = json.loads(Path(old_run["complete_episode_file"]).read_text())
        after = json.loads(Path(new_run["complete_episode_file"]).read_text())
        assert before["episode"]["status"] == after["episode"]["status"] == "completed"
        assert before["episode"]["seed_family"] == after["episode"]["seed_family"]
        old_outcome, new_outcome = before["episode"]["outcome"], after["episode"]["outcome"]
        assert new_outcome.pop("prompt_configuration") == selection.model_dump(mode="json")
        old_outcome.pop("prompt_configuration", None)
        for outcome in (old_outcome, new_outcome):
            outcome["runtime_configuration"].pop("prompt_variant", None)
            outcome["runtime_configuration"].pop("prompt_seed", None)
        assert old_outcome == new_outcome
        replay = json.loads(Path(new_run["complete_episode_file"]).with_name("replay").read_text())
        assert replay["turns"] == new_outcome["engine_effects"]["turns"]
        assert replay["communes"] == new_outcome["engine_effects"]["councils"]
        assert replay["scores"] == new_outcome["scores"]
        for old, new in zip(before["decisions"], after["decisions"], strict=True):
            for key in (
                "seat",
                "observation",
                "executed_action",
                "action_status",
                "fallback_origin",
                "terminal",
            ):
                assert old[key] == new[key], key
            assert len(old["attempts"]) == len(new["attempts"]) == 1
            assert old["attempts"][0]["response"] == new["attempts"][0]["response"]
            assert new["attempts"][0]["parsed_action"] == new["executed_action"]
            assert new["attempts"][0]["prompt"] == new["prompt"]
            name = new_outcome["pseudonyms"][int(new["seat"])]
            intro = selection.introduction(name, len(new_outcome["pseudonyms"]))
            prefix, rules = new["prompt"][0]["content"].split("=== OVERFISHED: THE RULES ===\n", 1)
            assert rules.startswith(intro)
            canonical = f"You are {name}, one of {len(new_outcome['pseudonyms'])} fishers on one lake."
            new["prompt"][0]["content"] = (
                prefix + "=== OVERFISHED: THE RULES ===\n" + canonical + rules[len(intro) :]
            )
            if wording != "canonical":
                values = (replay["game"]["boat_capacity"], *replay["game"]["fortune"])
                new["prompt"][0]["content"] = new["prompt"][0]["content"].replace(
                    selection.resource_rules(*values), PromptConfig().resolve().resource_rules(*values), 1
                )
            assert old["prompt"] == new["prompt"]
        del before, after
    print(candidate["variant"], wording, seed, "ten production source comparisons PASS", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline-root", type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for variant in ("village", "pond", "quiet-lake", "long-season"):
        canonical = export(ROOT, args.output / f"{variant}-canonical", variant, "canonical", 0)
        if args.baseline_root:
            original = export(
                args.baseline_root, args.output / f"{variant}-original", variant, "canonical", 0, True
            )
            compare(original, canonical, "canonical", 0)
        cases = [("mixed", 0)]
        for wording, seed in cases:
            candidate = export(ROOT, args.output / f"{variant}-{wording}", variant, wording, seed)
            compare(canonical, candidate, wording, seed)
    for wording in ("heldout-briefing", "heldout-reference"):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.export_posttrain",
                str(args.output / wording),
                "--variant",
                "pond",
                "--soul",
                str(ROOT / "souls/steady.md"),
                "--prompt-variant",
                wording,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0 and "invalid choice" in result.stderr
        assert not (args.output / wording).exists()
    print("90 source games, all variants and training wordings, held-out exclusion PASS", flush=True)


if __name__ == "__main__":
    main()
