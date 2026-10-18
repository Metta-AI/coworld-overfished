"""Complete JSONL games with unchanged souls, views, mechanics, actions, and memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, TextIO, cast

from overfished.memory import ScratchpadStore, policy_id
from overfished.prompts import PromptConfig, TRAIN

ROOT = Path(__file__).resolve().parents[1]
WORDINGS = (*TRAIN, "heldout-briefing", "heldout-reference")


def play(
    root: Path,
    variant: str,
    mode: str,
    seed: int,
    wording: str,
    prompt_seed: int,
    memory_path: Path | None,
    original: bool,
    *,
    exercise_retries: bool = False,
) -> str:
    manifest = json.loads((root / "coworld_manifest_template.json").read_text())
    config = (
        manifest["certification"]["game_config"]
        if variant == "certification"
        else next(item["game_config"] for item in manifest["variants"] if item["id"] == variant)
    )
    players = len(config["players"])
    souls = [root / ("souls/examples/villager.md" if memory_path else "souls/steady.md")] * players
    if memory_path:
        store = ScratchpadStore(memory_path)
        store.append(policy_id(souls[0].read_bytes()), "PRIVATE bootstrap café 🐟")
    command = [sys.executable, str(root / "tools/training_bridge.py"), "--variant", variant, "--mode", mode]
    command += [value for soul in souls for value in ("--soul", str(soul))]
    if memory_path:
        command += ["--scratchpad-dir", str(memory_path)]
    process = subprocess.Popen(
        command,
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    stdin, stdout = cast(TextIO, process.stdin), cast(TextIO, process.stdout)

    def call(request: dict[str, Any]) -> dict[str, Any]:
        stdin.write(json.dumps(request) + "\n")
        stdin.flush()
        return json.loads(stdout.readline())

    selection = PromptConfig.model_validate({"promptVariant": wording, "promptSeed": prompt_seed}).resolve()
    trace = hashlib.sha256()
    count = 0
    injected = 0
    continued_seen = False
    try:
        observation = call(
            {
                "kind": "reset",
                "seed": f"wording-{seed}",
                "players": players,
                "promptVariant": wording,
                "promptSeed": prompt_seed,
            }
        )
        while observation["kind"] != "terminal":
            count += 1
            continued_seen |= len(observation["messages"]) > 2
            view = observation["semantic_view"]
            messages = observation["messages"]
            if original:
                view["memory_mode"] = observation.pop("memory_mode")
                if mode == "choice" and observation["kind"] == "decision":
                    observation["typed_question"]["state"]["memory_mode"] = view["memory_mode"]
            if not original:
                assert view.pop("prompt_template") == selection.metadata(messages)
                if observation["speech_messages"]:
                    assert view.pop("speech_prompt_template") == selection.metadata(
                        observation["speech_messages"]
                    )
            if mode == "choice" and observation["kind"] == "decision":
                state = observation["typed_question"]["state"]
                if not original:
                    assert state.pop("prompt_template") == selection.metadata(messages)
                assert state == view
            name = view["pseudonym"]
            intro = (
                selection.introduction(name, players)
                if not original
                else f"You are {name}, one of {players} fishers on one lake."
            )
            canonical = f"You are {name}, one of {players} fishers on one lake."
            for key in ("messages", "speech_messages"):
                if observation[key]:
                    message = observation[key][0]
                    prefix, rules = message["content"].split("=== OVERFISHED: THE RULES ===\n", 1)
                    assert (
                        prefix
                        == souls[int(observation["seat"])].read_text().partition("\n")[2].strip() + "\n\n"
                    )
                    assert rules.startswith(intro)
                    message["content"] = (
                        prefix + "=== OVERFISHED: THE RULES ===\n" + canonical + rules[len(intro) :]
                    )
            trace.update(json.dumps(observation, sort_keys=True).encode())
            if mode == "choice" and observation["kind"] == "decision":
                trace.update(json.dumps(call({"kind": "encode"}), sort_keys=True).encode())
            teacher = call({"kind": "teacher"})
            if exercise_retries and view["phase"] == "fishing" and injected < 2:
                teacher["response"] = (
                    '{"thinking":"PRIVATE café 🐟","continue":true}'
                    if injected == 0
                    else '{"effort":"invalid effort"}'
                )
                injected += 1
            if observation["kind"] == "speech_turn":
                request = {
                    "kind": "say",
                    "decision_id": observation["decision_id"],
                    "text": teacher["response"],
                }
            else:
                request = {
                    "kind": "step",
                    "decision_id": observation["decision_id"],
                    "response": teacher["response"],
                }
            response = call(request)
            observation = response.pop("observation")
            trace.update(json.dumps(response, sort_keys=True).encode())
        assert count > 0 and len(observation["scores"]) == players
        if exercise_retries:
            assert injected == 2 and continued_seen
        trace.update(json.dumps(observation, sort_keys=True).encode())
        if memory_path:
            trace.update(
                json.dumps(
                    ScratchpadStore(memory_path).read(policy_id(souls[0].read_bytes())).model_dump(),
                    sort_keys=True,
                ).encode()
            )
        return trace.hexdigest()
    finally:
        stdin.close()
        assert process.wait(timeout=10) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--baseline-root", type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    count = 0
    for variant in ("certification", "village", "pond", "quiet-lake", "long-season"):
        for mode, memory in (("choice", False), ("text", False), ("text", True)):
            for seed in (1, 7):
                group = args.output / f"{variant}-{mode}-{memory}-{seed}"
                group.mkdir()
                canonical = None
                for wording, prompt_seed in [(name, 0) for name in WORDINGS] + [
                    ("mixed", n) for n in range(5)
                ]:
                    memory_path = group / f"{wording}-{prompt_seed}" if memory else None
                    digest = play(ROOT, variant, mode, seed, wording, prompt_seed, memory_path, False)
                    if wording == "canonical":
                        canonical = digest
                        if args.baseline_root:
                            assert digest == play(
                                args.baseline_root,
                                variant,
                                mode,
                                seed,
                                wording,
                                prompt_seed,
                                group / "original-memory" if memory else None,
                                True,
                            )
                    assert digest == canonical
                    count += 1
                print(f"{variant}/{mode}/memory={memory}/seed={seed}: 12 full games PASS", flush=True)
    canonical = None
    for wording, prompt_seed in [(name, 0) for name in WORDINGS] + [("mixed", n) for n in range(5)]:
        digest = play(
            ROOT, "quiet-lake", "text", 11, wording, prompt_seed, None, False, exercise_retries=True
        )
        if wording == "canonical":
            canonical = digest
            if args.baseline_root:
                assert digest == play(
                    args.baseline_root,
                    "quiet-lake",
                    "text",
                    11,
                    wording,
                    prompt_seed,
                    None,
                    True,
                    exercise_retries=True,
                )
        assert digest == canonical
        count += 1
    print(f"{count} complete games including thinking/retries PASS", flush=True)


if __name__ == "__main__":
    main()
