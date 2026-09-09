"""Model calls for soul seats: transport, prompts, the private thinking loop, and reply parsing.

Transport is OpenAI-style chat completions over one of two bases:

- hosted: the Softmax LLM sidecar on loopback (`AWS_ENDPOINT_URL_BEDROCK_RUNTIME`), which forwards to
  OpenRouter and needs no auth header; `X-Coworld-Player-Slot` bills the call to the seat;
- local: OpenRouter directly with `OPENROUTER_API_KEY`.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import aiohttp

from overfished.config import GameConfig
from overfished.engine import Action, Engine, Punishment
from overfished.soul import Soul

PLAYER_SLOT_HEADER = "X-Coworld-Player-Slot"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LlmError(RuntimeError):
    """A model call failed in a way the game treats as that seat's problem for this decision."""


@dataclass
class Transport:
    base_url: str
    api_key: str | None
    timeout_seconds: float
    session: aiohttp.ClientSession
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def describe(self) -> str:
        return f"{self.base_url}/v1/chat/completions ({'bearer key' if self.api_key else 'sidecar, no auth'})"

    async def complete(self, *, model: str, messages: list[dict], max_tokens: int, slot: int) -> str:
        headers = {"Content-Type": "application/json", PLAYER_SLOT_HEADER: str(slot)}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False}
        self.calls += 1
        try:
            async with self.session.post(
                f"{self.base_url}/v1/chat/completions",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=self.timeout_seconds),
            ) as response:
                text = await response.text()
                if response.status != 200:
                    raise LlmError(f"HTTP {response.status} from {self.base_url}: {text[:800]}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise LlmError(f"{type(error).__name__}: {error}") from None
        payload = json.loads(text)
        if "error" in payload and "choices" not in payload:
            raise LlmError(f"provider error: {json.dumps(payload['error'])[:800]}")
        usage = payload.get("usage") or {}
        self.prompt_tokens += int(usage.get("prompt_tokens") or 0)
        self.completion_tokens += int(usage.get("completion_tokens") or 0)
        choices = payload["choices"]
        if not choices:
            raise LlmError("provider returned no choices")
        content = choices[0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str):
            raise LlmError("provider returned non-text content")
        return content


def transport_from_env(session: aiohttp.ClientSession, timeout_seconds: float) -> Transport | None:
    sidecar = os.environ.get("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "").strip().rstrip("/")
    if sidecar:
        return Transport(base_url=sidecar, api_key=None, timeout_seconds=timeout_seconds, session=session)
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        base = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api").strip().rstrip("/")
        return Transport(base_url=base, api_key=key, timeout_seconds=timeout_seconds, session=session)
    return None


# ---- prompts ----------------------------------------------------------------------------


def mechanics_block(config: GameConfig, pseudonym: str, seat_count: int) -> str:
    llm = config.llm
    council = (
        f"Every {config.commune_every} turns"
        + (", and once before turn 1," if config.commune_at_start else "")
        + f" the {seat_count} of you hold a council: {config.commune_rounds} speaking round(s), everyone speaks "
        "at once in each round and reads all earlier rounds. Messages are public and permanent. "
        f"Each message is at most {llm.say_max_chars} characters."
        if config.commune_rounds > 0
        else "There is no council in this variant: you never talk."
    )
    return f"""
=== THE GAME: OVERFISHED ===
You are {pseudonym}, one of {seat_count} fishers on a single lake. The episode lasts {config.turns} fishing turns.
Your score is the number of fish you hold when the episode ends. Nothing else counts.

THE LAKE. The lake holds a fish population you cannot see. It regrows each turn, faster when it is neither
nearly empty nor nearly full, and it has a carrying capacity. If it is fished below a hidden point of no
return it will shrink every turn from then on and never recover, no matter what anyone does afterwards.

FISHING. Each turn you choose an effort from 0% to 100% of your boat's capacity. Your boat lands
{config.boat_capacity} fish per turn at 100% effort on a completely full lake; at a lower fill the catch falls in
proportion, so catch = effort x {config.boat_capacity} x (lake fullness). Fish are landed in whole numbers. If
the fleet asks for more than the lake holds, the lake is emptied and shared in proportion to effort.
Everyone's catch each turn is public. Efforts are private, but a catch reveals a lot about effort.

PUNISHMENT. Each turn you may destroy fish: for every one of your own fish you destroy, one fish is
destroyed from a named fisher's stock. It costs you exactly what it costs them. {"Punishments are public: everyone sees who punished whom and by how much." if config.punishments_public else "Punishments are anonymous: the target sees the loss, nobody sees the sender."}

COUNCIL. {council}
There are no other mechanics: no enforceable contracts, no votes, no alliances beyond what people say and
then do. Promises are only words. Threats are only words until fish are destroyed.

PRIVACY. Your thinking and your notebook are private. Nobody, not the other fishers and not the public
record, ever reads them. Only what you put in `say` during a council is public. Your notebook is handed
back to you every turn; keep it under {llm.notebook_max_chars} characters and use it to remember plans,
promises made to you, and grudges.

REPLY FORMAT. Reply with exactly one JSON object and nothing else. On a fishing turn:
  {{"thinking": "<private>", "notebook": "<private, replaces the old notebook>",
   "effort": <number 0 to 1>, "punish": [{{"target": "<fisher name>", "fish": <whole number>}}]}}
  `punish` may be an empty list. At a council:
  {{"thinking": "<private>", "notebook": "<private>", "say": "<public message, or an empty string>"}}
You may instead reply {{"thinking": "<private>", "continue": true}} to keep reasoning privately before
committing; you get at most {llm.think_turns} such replies per decision, after which you must decide.
""".strip()


def _ledger(engine: Engine, history: int) -> str:
    names = engine.pseudonyms
    turns = engine.turns[-history:]
    if not turns:
        return "CATCH LEDGER (public): no fishing turns yet."
    header = "turn | " + " | ".join(f"{n:>7}" for n in names)
    rows = [header]
    for record in turns:
        rows.append(f"{record.t:>4} | " + " | ".join(f"{c:>7}" for c in record.catch))
    totals = ", ".join(f"{names[i]} {engine.fish[i]}" for i in range(len(names)))
    ranking = sorted(range(len(names)), key=lambda i: -engine.fish[i])
    ranked = ", ".join(f"{k + 1}. {names[i]} ({engine.fish[i]})" for k, i in enumerate(ranking))
    return (
        f"CATCH LEDGER (public), fish landed per turn, last {len(turns)} turn(s):\n"
        + "\n".join(rows)
        + f"\nFish held now: {totals}\nRanking: {ranked}"
    )


def _punishments(engine: Engine, history: int) -> str:
    names = engine.pseudonyms
    lines = []
    for record in engine.turns[-history:]:
        for p in record.punish:
            if engine.config.punishments_public:
                lines.append(f"turn {record.t}: {names[p.frm]} destroyed {p.fish} of {names[p.to]}'s fish (and {p.fish} of their own)")
            else:
                lines.append(f"turn {record.t}: {names[p.to]} lost {p.fish} fish to an anonymous punishment")
    if not lines:
        return "PUNISHMENTS recently: none."
    return "PUNISHMENTS recently:\n  " + "\n  ".join(lines)


def _own_catches(engine: Engine, slot: int, history: int) -> str:
    rows = []
    for record in engine.turns[-history:]:
        rows.append(f"turn {record.t}: {record.catch[slot]} fish at {int(round(record.effort[slot] * 100))}% effort")
    if not rows:
        return "YOUR OWN EFFORT AND CATCH: nothing yet."
    return "YOUR OWN EFFORT AND CATCH (private), most recent last:\n  " + "\n  ".join(rows)


def _council_transcript(engine: Engine, count: int) -> str:
    names = engine.pseudonyms
    if not engine.communes:
        return "COUNCILS so far: none."
    parts = []
    for commune in engine.communes[-count:]:
        lines = [f"COUNCIL before turn {commune.before_turn}:"]
        for r, speeches in enumerate(commune.rounds):
            said = [f'{names[s.slot]}: "{s.text}"' for s in speeches if s.text]
            lines.append(f"  round {r + 1}: " + (" | ".join(said) if said else "(silence)"))
        parts.append("\n".join(lines))
    return "\n".join(parts)


def _next_council(engine: Engine) -> str:
    if engine.config.commune_rounds == 0:
        return ""
    played = len(engine.turns)
    every = engine.config.commune_every
    next_before = (played // every + 1) * every + 1
    if next_before > engine.config.turns:
        return "There are no more councils before the episode ends."
    return f"The next council is before turn {next_before}."


def turn_observation(engine: Engine, slot: int, notebook: str) -> str:
    config = engine.config
    name = engine.pseudonyms[slot]
    last = engine.turns[-1] if engine.turns else None
    own = (
        f"Your fish: {engine.fish[slot]}. Last turn you landed {last.catch[slot]} at {int(round(last.effort[slot] * 100))}% effort."
        if last
        else f"Your fish: {engine.fish[slot]}. No fishing yet."
    )
    return "\n\n".join(
        [
            f"FISHING TURN {engine.turn} of {config.turns}. You are {name}.\n{own}",
            _ledger(engine, config.history_turns),
            _punishments(engine, config.history_turns),
            _own_catches(engine, slot, config.history_turns),
            _council_transcript(engine, 2),
            f"YOUR NOTEBOOK: {notebook if notebook else '(empty)'}",
            f"{_next_council(engine)}\nDecide your effort (0 to 1) and any punishments for this turn. Reply with one JSON object.",
        ]
    )


def council_observation(engine: Engine, slot: int, notebook: str, round_index: int, earlier: list[list]) -> str:
    config = engine.config
    name = engine.pseudonyms[slot]
    names = engine.pseudonyms
    earlier_text = []
    for r, speeches in enumerate(earlier):
        said = [f'{names[s.slot]}: "{s.text}"' for s in speeches if s.text]
        earlier_text.append(f"  round {r + 1}: " + (" | ".join(said) if said else "(silence)"))
    this = (
        f"COUNCIL before turn {engine.turn}, speaking round {round_index + 1} of {config.commune_rounds}. You are {name}.\n"
        + (
            "Earlier rounds of this council:\n" + "\n".join(earlier_text)
            if earlier_text
            else "This is the first round; nobody has spoken yet."
        )
    )
    return "\n\n".join(
        [
            this,
            f"Your fish: {engine.fish[slot]}.",
            _ledger(engine, config.history_turns),
            _punishments(engine, config.history_turns),
            _own_catches(engine, slot, config.history_turns),
            _council_transcript(engine, 1) if engine.communes else "COUNCILS so far: none.",
            f"YOUR NOTEBOOK: {notebook if notebook else '(empty)'}",
            f"Everyone speaks at once this round; all messages are public. Say what you want to say (up to {config.llm.say_max_chars} characters) or an empty string. Reply with one JSON object.",
        ]
    )


# ---- reply parsing ----------------------------------------------------------------------


def extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a reply, tolerating fences and surrounding prose."""
    match = _JSON_OBJECT.search(text)
    if match is None:
        return None
    candidate = match.group(0)
    for attempt in (candidate, candidate.strip("`")):
        try:
            value = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    # Trim trailing junk after the last closing brace progressively.
    end = candidate.rfind("}")
    while end > 0:
        try:
            value = json.loads(candidate[: end + 1])
        except json.JSONDecodeError:
            end = candidate.rfind("}", 0, end)
            continue
        return value if isinstance(value, dict) else None
    return None


def parse_action(reply: dict, engine: Engine, slot: int) -> Action | str:
    """An Action, or a string explaining why the reply is not one."""
    effort = reply.get("effort")
    if isinstance(effort, str):
        stripped = effort.strip().rstrip("%")
        try:
            effort = float(stripped) / (100.0 if effort.strip().endswith("%") else 1.0)
        except ValueError:
            return "`effort` must be a number between 0 and 1"
    if not isinstance(effort, (int, float)) or isinstance(effort, bool):
        return "`effort` must be a number between 0 and 1"
    effort = float(effort)
    if 5.0 <= effort <= 100.0:
        effort = effort / 100.0
    if not 0.0 <= effort <= 1.0:
        return "`effort` must be between 0 and 1 (a fraction of capacity)"
    punish: list[Punishment] = []
    raw_punish = reply.get("punish") or []
    if not isinstance(raw_punish, list):
        return "`punish` must be a list"
    for entry in raw_punish:
        if not isinstance(entry, dict):
            return "each `punish` entry must be an object with `target` and `fish`"
        target = entry.get("target")
        target_slot = engine.slot_of(target) if isinstance(target, str) else None
        if target_slot is None or target_slot == slot:
            return f"`punish.target` must name another fisher; got {target!r}"
        fish = entry.get("fish", 1)
        if isinstance(fish, float) and fish.is_integer():
            fish = int(fish)
        if not isinstance(fish, int) or isinstance(fish, bool) or fish < 0:
            return "`punish.fish` must be a whole number"
        if fish == 0:
            continue
        punish.append(Punishment(target=target_slot, fish=fish))
    return Action(effort=round(effort, 3), punish=punish)


def clip(text: object, limit: int) -> str:
    if not isinstance(text, str):
        return ""
    return text.strip()[:limit]


# ---- the decision loop -------------------------------------------------------------------


@dataclass
class SeatBrain:
    """One soul seat's conversation state and budget accounting."""

    slot: int
    soul: Soul
    system_prompt: str
    notebook: str = ""
    calls: int = 0
    failures: int = 0
    fallbacks: int = 0
    last_thinking: list[str] = field(default_factory=list)


@dataclass
class Decision:
    action: Action | None = None
    say: str | None = None
    auto: bool = False
    transcript: list[str] = field(default_factory=list)


async def decide(
    brain: SeatBrain,
    transport: Transport,
    engine: Engine,
    *,
    observation: str,
    council: bool,
    think_turns: int,
    log: callable,
) -> Decision:
    """Run the private thinking loop until the seat commits to an action or a council message."""
    config = engine.config.llm
    messages = [{"role": "system", "content": brain.system_prompt}, {"role": "user", "content": observation}]
    decision = Decision()
    thinks_left = think_turns
    retries_left = 1
    for _ in range(config.max_calls_per_decision):
        brain.calls += 1
        try:
            reply_text = await transport.complete(
                model=brain.soul.model, messages=messages, max_tokens=config.max_output_tokens, slot=brain.slot
            )
        except LlmError as error:
            brain.failures += 1
            log(f"model call failed: {error}")
            break
        decision.transcript.append(reply_text)
        reply = extract_json(reply_text)
        if reply is None:
            log("reply had no JSON object; " + ("asking once more" if retries_left else "giving up"))
            if retries_left == 0:
                break
            retries_left -= 1
            messages.append({"role": "assistant", "content": reply_text})
            messages.append({"role": "user", "content": "That was not a JSON object. Reply with exactly one JSON object."})
            continue
        thinking = clip(reply.get("thinking"), 4000)
        if thinking:
            brain.last_thinking.append(thinking)
            log(f"thinking: {thinking}")
        if "notebook" in reply:
            brain.notebook = clip(reply.get("notebook"), config.notebook_max_chars)
        if reply.get("continue") is True and "effort" not in reply and "say" not in reply:
            if thinks_left <= 0:
                log("asked to continue thinking with no thinking turns left; demanding a decision")
                messages.append({"role": "assistant", "content": reply_text})
                messages.append({"role": "user", "content": "No more private thinking turns. Decide now with one JSON object."})
                continue
            thinks_left -= 1
            messages.append({"role": "assistant", "content": reply_text})
            messages.append(
                {"role": "user", "content": f"Continue privately. {thinks_left} thinking turn(s) left before you must decide."}
            )
            continue
        if council:
            decision.say = clip(reply.get("say"), config.say_max_chars)
            return decision
        parsed = parse_action(reply, engine, brain.slot)
        if isinstance(parsed, Action):
            decision.action = parsed
            return decision
        log(f"invalid action: {parsed}; " + ("asking once more" if retries_left else "giving up"))
        if retries_left == 0:
            break
        retries_left -= 1
        messages.append({"role": "assistant", "content": reply_text})
        messages.append({"role": "user", "content": f"Invalid: {parsed}. Reply with one corrected JSON object."})
    decision.auto = True
    brain.fallbacks += 1
    return decision


def elapsed_since(start: float) -> float:
    return time.monotonic() - start
