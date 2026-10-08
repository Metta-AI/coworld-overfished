"""Model calls for soul seats: transport, prompts, the private thinking loop, and reply parsing.

Transport is native chat completions through COWORLD_LLM_ENDPOINT.
The authenticated sidecar attributes each game-hosted learner to its actual player slot.
"""

from __future__ import annotations

import asyncio
import base64
import codecs
import json
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationError,
    model_validator,
)

from overfished.config import GameConfig
from overfished.engine import Action, CouncilVote, Engine, Gift, Punishment
from overfished.lifecycle import OwnershipUnsettled, cleanup_deadline, owned_task, settle
from overfished.memory import SCRATCHPAD_MAX_BYTES, SCRATCHPAD_NOTE_BYTES, MemoryView
from overfished.soul import Soul
from overfished.trajectory import Attempt

PLAYER_SLOT_HEADER = "X-Coworld-Player-Slot"
_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class LlmError(RuntimeError):
    """A model call failed in a way the game treats as that seat's problem for this decision."""


class NativeMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    role: Literal["system", "user", "assistant"]
    content: str


class NativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    model: str = Field(min_length=1)
    messages: list[NativeMessage]
    max_tokens: StrictInt = Field(gt=0)
    stream: Literal[False] = False
    temperature: float = Field(ge=0, le=1)
    top_p: float = Field(gt=0, le=1)
    reasoning: dict[str, JsonValue] | None = None


class NativeDecoder(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    temperature: float = Field(ge=0, le=1)
    top_p: float = Field(gt=0, le=1)
    max_tokens: StrictInt = Field(gt=0)
    reasoning: dict[str, JsonValue] | None
    timeout_ms: float = Field(gt=0)


class ContentPart(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    type: Literal["text"]
    text: str


class CompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    content: str | list[ContentPart] | None = None
    reasoning: str | None = None


class CompletionChoice(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    message: CompletionMessage
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter"] | None


class CompletionUsage(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    prompt_tokens: StrictInt = Field(ge=0)
    completion_tokens: StrictInt = Field(ge=0)


class SamplingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    policy_revision: str = Field(min_length=1)
    tokenizer_revision: str = Field(min_length=1)
    chat_template: str = Field(min_length=1)
    sampling: Literal["full_softmax_temperature_one"]
    enable_thinking: StrictBool
    max_new_tokens: StrictInt = Field(gt=0)
    max_sequence_length: StrictInt = Field(gt=0)
    sampling_seed: StrictInt
    eos_token_ids: list[StrictInt]
    prompt_token_ids: list[StrictInt]
    completion_token_ids: list[StrictInt]
    behavior_log_probs: list[StrictFloat]
    response: str
    stop_reason: Literal["eos", "length"]

    @model_validator(mode="after")
    def actual_draws(self) -> SamplingEvidence:
        if self.enable_thinking:
            raise ValueError("sampled action evidence requires disabled hidden reasoning")
        if not self.completion_token_ids or len(self.completion_token_ids) != len(self.behavior_log_probs):
            raise ValueError("sampled token and probability counts differ")
        if any(
            value < 0 for value in [*self.prompt_token_ids, *self.completion_token_ids, *self.eos_token_ids]
        ):
            raise ValueError("token IDs must be nonnegative")
        if any(value > 0 for value in self.behavior_log_probs):
            raise ValueError("behavior log probabilities must be nonpositive")
        if len(self.prompt_token_ids) + len(self.completion_token_ids) > self.max_sequence_length:
            raise ValueError("sample exceeds served context budget")
        if len(self.completion_token_ids) > self.max_new_tokens:
            raise ValueError("sample exceeds served output budget")
        if self.stop_reason == "eos" and self.completion_token_ids[-1] not in self.eos_token_ids:
            raise ValueError("EOS stop lacks an actual terminal EOS draw")
        return self


class CompletionResponse(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)
    model: str
    choices: list[CompletionChoice] = Field(min_length=1, max_length=1)
    usage: CompletionUsage
    sampling_evidence: SamplingEvidence | None = None


@dataclass
class Transport:
    base_url: str
    timeout_seconds: float
    temperature: float = field(init=False)
    top_p: float = field(init=False)
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    observe: Callable[[int, Attempt], None] = lambda slot, attempt: None
    received_header_pairs: dict[str, list[tuple[bytes, bytes]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("native timeout must be finite and positive")
        self.base_url = self.base_url.rstrip("/")
        self.temperature = float(os.environ.get("COWORLD_LLM_TEMPERATURE", "1"))
        self.top_p = float(os.environ.get("COWORLD_LLM_TOP_P", "1"))
        NativeDecoder(
            temperature=self.temperature,
            top_p=self.top_p,
            max_tokens=1,
            reasoning=None,
            timeout_ms=self.timeout_seconds * 1000,
        )

    @property
    def describe(self) -> str:
        return "native sidecar chat completions"

    async def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int,
        slot: int,
        evidence: Attempt,
        reasoning: dict | None = None,
    ) -> str:
        temperature, top_p = self.temperature, self.top_p
        native_request = NativeRequest(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            reasoning=reasoning or None,
        )
        body = native_request.model_dump(mode="json", exclude_none=True)
        self.calls += 1
        evidence.request = json.loads(json.dumps(body))
        evidence.model = model
        evidence.decoder = NativeDecoder(
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            reasoning=native_request.reasoning,
            timeout_ms=self.timeout_seconds * 1000,
        ).model_dump(mode="json")
        self.received_header_pairs[evidence.attempt_id] = []
        self.observe(slot, evidence)
        started = time.monotonic()
        request_deadline = asyncio.get_running_loop().time() + self.timeout_seconds
        response = None
        raw = bytearray()
        utf8 = codecs.getincrementaldecoder("utf-8")()
        text_parts: list[str] = []
        client = httpx.AsyncClient(trust_env=False, timeout=None)

        async def receive() -> None:
            nonlocal response
            request = client.build_request(
                "POST",
                self.base_url + "/v1/chat/completions",
                json=body,
                headers={
                    "Content-Type": "application/json",
                    PLAYER_SLOT_HEADER: str(slot),
                    "Accept-Encoding": "identity",
                },
            )
            response = await client.send(request, stream=True)
            evidence.http_status = response.status_code
            evidence.response_complete = False
            evidence.response_reader_joined = False
            evidence.response_body_b64 = ""
            evidence.raw_response = ""
            pairs = list(response.headers.raw)
            self.received_header_pairs[evidence.attempt_id] = pairs
            headers = {}
            controlled = {
                "x-softmax-llm-call-id",
                "x-request-id",
                "request-id",
                "x-coworld-checkpoint-sha256",
                "x-coworld-tokenizer-sha256",
                "x-coworld-chat-template-sha256",
                "content-encoding",
            }
            for name_bytes, value_bytes in pairs:
                name, value = name_bytes.decode("ascii").lower(), value_bytes.decode("latin1")
                if name in controlled and name in headers:
                    raise ValueError("duplicate native identity or encoding header")
                headers[name] = value
            evidence.response_headers = headers
            if (
                "x-request-id" in headers
                and "request-id" in headers
                and headers["x-request-id"] != headers["request-id"]
            ):
                raise ValueError("conflicting native provider request IDs")
            if "x-softmax-llm-call-id" in headers:
                evidence.platform_call_id = UUID(headers["x-softmax-llm-call-id"])
            for header, attribute in [
                ("x-coworld-checkpoint-sha256", "model_identity"),
                ("x-coworld-tokenizer-sha256", "tokenizer_identity"),
                ("x-coworld-chat-template-sha256", "chat_template_sha256"),
            ]:
                if header in headers:
                    setattr(evidence, attribute, headers[header])
            evidence.provider_request_id = headers.get("x-request-id", headers.get("request-id"))
            self.observe(slot, evidence)
            if headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("native response must use identity encoding")
            async for chunk in response.aiter_raw():
                if len(raw) + len(chunk) > 4_000_000:
                    raise ValueError("native response exceeds four million bytes")
                raw.extend(chunk)
                evidence.raw_response = None
                evidence.response_body_b64 = base64.b64encode(raw).decode("ascii")
                text_parts.append(utf8.decode(chunk, final=False))
                evidence.raw_response = None if utf8.getstate()[0] else "".join(text_parts)
                self.observe(slot, evidence)
            evidence.response_complete = True
            text_parts.append(utf8.decode(b"", final=True))
            evidence.raw_response = "".join(text_parts)

        reader = owned_task(receive())
        try:
            try:
                done, _ = await asyncio.wait(
                    {reader}, timeout=max(0, request_deadline - asyncio.get_running_loop().time())
                )
                if not done:
                    raise TimeoutError("native absolute request deadline exceeded")
                reader.result()
            finally:
                deadline = cleanup_deadline()
                read_joined = await settle({reader}, deadline, cancel=True)
                closer = owned_task(client.aclose())
                close_joined = await settle({closer}, deadline, cancel=False)
                if not close_joined:
                    await settle({closer}, deadline, cancel=True)
                joined = read_joined and close_joined and not closer.cancelled()
                if response is not None:
                    evidence.response_reader_joined = joined and closer.exception() is None
                evidence.latency_ms = (time.monotonic() - started) * 1000
                self.observe(slot, evidence)
                if not joined:
                    raise OwnershipUnsettled("native reader release did not join")
                closer.result()
            assert response is not None
            if response.status_code != 200:
                raise LlmError(f"native HTTP status {response.status_code}")
            completion = CompletionResponse.model_validate_json(raw)
            evidence.model = completion.model
            evidence.input_tokens = completion.usage.prompt_tokens
            evidence.output_tokens = completion.usage.completion_tokens
            self.prompt_tokens += completion.usage.prompt_tokens
            self.completion_tokens += completion.usage.completion_tokens
            choice = completion.choices[0]
            content = choice.message.content
            if isinstance(content, list):
                content = "".join(part.text for part in content)
            evidence.stop_reason = choice.finish_reason
            if completion.sampling_evidence is not None:
                sample = completion.sampling_evidence
                if temperature != 1 or top_p != 1 or sample.response != content:
                    raise ValueError("sampled completion does not match actual decoder or text")
                if sample.max_new_tokens != max_tokens:
                    raise ValueError("served output budget differs from actual request")
                if sample.stop_reason == "eos" and choice.finish_reason != "stop":
                    raise ValueError("sample/native stop reasons disagree")
                if sample.stop_reason == "length" and choice.finish_reason != "length":
                    raise ValueError("sample/native stop reasons disagree")
                if completion.usage.prompt_tokens != len(
                    sample.prompt_token_ids
                ) or completion.usage.completion_tokens != len(sample.completion_token_ids):
                    raise ValueError("served usage differs from actual sampled IDs")
                evidence.prompt_token_ids = sample.prompt_token_ids
                evidence.sampled_token_ids = sample.completion_token_ids
                evidence.behavior_logprobs = sample.behavior_log_probs
                evidence.stop_reason = sample.stop_reason
            if not isinstance(content, str):
                raise LlmError("provider returned no action text")
            evidence.response = content
            self.observe(slot, evidence)
            if not content.strip():
                raise LlmError("provider returned no action text")
            return content
        except (httpx.HTTPError, TimeoutError, UnicodeError, ValidationError) as error:
            raise LlmError(type(error).__name__) from None


def transport_from_env(timeout_seconds: float) -> Transport | None:
    endpoint = os.environ.get("COWORLD_LLM_ENDPOINT", "").strip()
    return Transport(base_url=endpoint, timeout_seconds=timeout_seconds) if endpoint else None


# ---- prompts ----------------------------------------------------------------------------


def mechanics_block(
    config: GameConfig,
    pseudonym: str,
    seat_count: int,
    boat_capacity: int,
    *,
    persistent_memory: bool = False,
) -> str:
    """The rules a seat is given. Mechanics only: no strategy, no framing of what other fishers might do."""
    llm = config.llm
    council = (
        f"COUNCIL. {'Before turn 1 and after' if config.commune_at_start else 'After'} every {config.commune_every} "
        f"turns the fishers hold a council of {config.commune_rounds} speaking round(s). Fishers speak one at a time "
        "in a fixed order, each reading everything said before them in that council; the order is the same in "
        "every round of a council and the first speaker rotates each council. You may say nothing. Council messages "
        f"are public and permanent, at most {llm.say_max_chars} characters each. Nothing said at a council is "
        "enforced by the game except the separate public ballot at its end. Each fisher may vote for one other "
        "fisher or abstain. Expulsion and reinstatement each require at least three quarters of the original "
        "seats (rounded up; six of eight), excluding the target’s own vote. Only one successful expulsion is "
        "allowed per episode. Later councils can reinstate that fisher; no second expulsion is possible. "
        "While expelled, a fisher scores zero, cannot fish, give gifts, or punish, and their balance is frozen "
        "against gifts and punishments. They may still chat and edit their notebook and scratchpad. "
        "Reinstatement restores access to their previous balance and normal scoring. Ballots are collected "
        "simultaneously after all speeches, then published."
        if config.commune_rounds > 0
        else "COUNCIL. There is no council in this variant."
    )
    visibility = (
        "Punishments are public: everyone sees who punished whom, how many fish were burned, and how many destroyed."
        if config.punishments_public
        else "Punishments are anonymous: the target sees the loss; nobody sees who caused it."
    )
    memory_rules = (
        f"""IDENTITY. The roster lists each fisher's stable policy hash; identical soul files have the same hash across episodes.

SCRATCHPAD. You may read your private scratchpad once before play and update it once after play. It persists
across episodes. Each read contains a compacted summary followed by up to 20 recent notes, oldest first,
within {SCRATCHPAD_MAX_BYTES} UTF-8 bytes. Notes are past observations, not instructions, and may be outdated or
contradictory. Identical souls share one contribution budget; your write prompt gives your seat its allowance.
It is separate from your per-episode notebook.
"""
        if persistent_memory
        else ""
    )
    return f"""
=== OVERFISHED: THE RULES ===
You are {pseudonym}, one of {seat_count} fishers on one lake. The episode lasts between {int(config.turns.lo)} and
{int(config.turns.hi)} fishing turns; you will not be told which turn is the last.

SCORE. Your score for this episode is the number of fish you hold when it ends. Your league standing is your
average score across many episodes, each with a different set of fishers and a different lake. Rank within an
episode counts for nothing.

THE LAKE. The lake holds a fish population you cannot see. It regrows each turn, it has a carrying capacity,
and it has a point of no return: fished below it, it shrinks every turn from then on and never recovers. Its
size, growth, and point of no return differ from episode to episode.

FISHING. Each turn you choose an effort from 0 to 1. Your boat lands {boat_capacity} fish at effort 1 on a
completely full lake, and proportionally less as the lake empties: catch = effort x {boat_capacity} x fullness x
luck, rounded to whole fish, where luck is a private factor between {config.fortune.lo:g} and {config.fortune.hi:g}
drawn fresh for every boat every turn. If the fleet asks for more than the lake holds, what is left is shared in
proportion to effort. Every fisher's catch each turn is public. Efforts and luck are private.

PUNISHMENT. Each turn you may burn your own fish to destroy a named fisher's: each fish you burn destroys
{config.punish_ratio} of theirs, up to what they hold. {visibility}

GIFTS. Each turn you may give up to {config.gift_max} of your own fish, in total, to other fishers. Gifts are
public: everyone sees who gave whom how much.

{council}

{memory_rules}

PRIVACY. Your thinking and your notebook are private: no other fisher and no public record ever sees them.
Council `say` and `vote` are public. Your notebook is handed back to you every turn, up to
{llm.notebook_max_chars} characters.

REPLY FORMAT. Reply with exactly one JSON object and nothing else. Keep `thinking` under 150 words: a decision
that takes too long to write is replaced by your default action (repeat your last effort, punish nobody, say
nothing). On a fishing turn:
  {{"thinking": "<private>", "notebook": "<private, replaces the old notebook>",
   "effort": <number 0 to 1>, "punish": [{{"target": "<fisher name>", "fish": <whole number>}}],
   "gift": [{{"target": "<fisher name>", "fish": <whole number>}}]}}
  `punish` and `gift` may be empty lists. At a council:
  {{"thinking": "<private>", "notebook": "<private>", "say": "<public message, or an empty string>"}}
At the separate council ballot, reply {{"vote": "<other fisher name, or null to abstain>",
"notebook": "<private>", "thinking": "<private>"}}.
You may instead reply {{"thinking": "<private>", "continue": true}} to keep reasoning privately before
committing; you get at most {llm.think_turns} such replies per decision, after which you must decide.
""".strip()


def _ledger(engine: Engine, history: int) -> str:
    names = engine.pseudonyms
    turns = engine.turns[-history:]
    if not turns:
        return "CATCH LEDGER (public): no fishing turns yet."
    width = max(7, max(len(n) for n in names))
    header = "turn | " + " | ".join(f"{n:>{width}}" for n in names)
    rows = [header]
    for record in turns:
        rows.append(f"{record.t:>4} | " + " | ".join(f"{c:>{width}}" for c in record.catch))
    totals = ", ".join(f"{names[i]} {engine.fish[i]}" for i in range(len(names)))
    return (
        f"CATCH LEDGER (public), fish landed per turn, last {len(turns)} turn(s):\n"
        + "\n".join(rows)
        + f"\nFish held now: {totals}"
    )


def _punishments(engine: Engine, history: int) -> str:
    names = engine.pseudonyms
    lines = []
    for record in engine.turns[-history:]:
        for p in record.punish:
            if engine.config.punishments_public:
                lines.append(
                    f"turn {record.t}: {names[p.frm]} burned {p.cost} of their own fish to destroy {p.fish} of {names[p.to]}'s"
                )
            else:
                lines.append(f"turn {record.t}: {names[p.to]} lost {p.fish} fish to an anonymous punishment")
    if not lines:
        return "PUNISHMENTS recently: none."
    return "PUNISHMENTS recently:\n  " + "\n  ".join(lines)


def _gifts(engine: Engine, history: int) -> str:
    names = engine.pseudonyms
    lines = [
        f"turn {record.t}: {names[g.frm]} gave {g.fish} fish to {names[g.to]}"
        for record in engine.turns[-history:]
        for g in record.gift
    ]
    if not lines:
        return "GIFTS recently: none."
    return "GIFTS recently:\n  " + "\n  ".join(lines)


def _own_catches(engine: Engine, slot: int, history: int) -> str:
    rows = []
    for record in engine.turns[-history:]:
        rows.append(
            f"turn {record.t}: {record.catch[slot]} fish at {round(record.effort[slot] * 100)}% effort"
        )
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
        if commune.vote_kind:
            lines.append(
                f"  Public {commune.vote_kind} votes: "
                + "; ".join(
                    f"{names[i]}: {names[v.target] if v.target is not None else 'abstain'}"
                    for i, v in enumerate(commune.votes)
                )
            )
            lines.append(
                "  Result: "
                + (
                    f"{names[commune.passed_target]} {commune.vote_kind} vote passed"
                    if commune.passed_target is not None
                    else "no change"
                )
            )
        parts.append("\n".join(lines))
    return "\n".join(parts)


def _next_council(engine: Engine) -> str:
    """Never consults the turn limit: saying 'no more councils' would reveal when the episode ends."""
    if engine.config.commune_rounds == 0:
        return ""
    played = len(engine.turns)
    every = engine.config.commune_every
    next_before = (played // every + 1) * every + 1
    return f"The next council is before turn {next_before}, if the episode lasts that long."


def council_status(engine: Engine) -> str:
    expelled = engine.expelled
    return (
        f"EXPULSION STATUS (public): {engine.pseudonyms[expelled] + ' is expelled; balance frozen, score zero' if expelled is not None else 'nobody expelled'}. "
        f"Expulsion used: {engine.expulsion_used}. Next ballot: {engine.vote_kind or 'none'}. "
        f"Votes required: {engine.votes_required} of {engine.config.num_players}; self-votes do not count."
    )


def vote_observation(engine: Engine, slot: int, notebook: str, rounds: list[list]) -> str:
    transcript = "\n".join(
        f"Round {r + 1}, {engine.pseudonyms[s.slot]}: {s.text}"
        for r, speeches in enumerate(rounds)
        for s in speeches
    )
    return "\n\n".join(
        [
            f"COUNCIL BALLOT before turn {engine.turn}. You are {engine.pseudonyms[slot]}.",
            council_status(engine),
            policy_roster(engine),
            _ledger(engine, engine.config.history_turns),
            _council_transcript(engine, 2),
            "This council's complete discussion:\n" + transcript,
            f"YOUR NOTEBOOK: {notebook or '(empty)'}",
            "Vote to "
            + str(engine.vote_kind)
            + '. Reply with {"vote": "<other fisher name>"} or {"vote": null} to abstain. '
            "You may also include private thinking and notebook edits. All votes will be published.",
        ]
    )


def turn_observation(engine: Engine, slot: int, notebook: str) -> str:
    config = engine.config
    name = engine.pseudonyms[slot]
    last = engine.turns[-1] if engine.turns else None
    own = (
        f"Your fish: {engine.fish[slot]}. Last turn you landed {last.catch[slot]} at {round(last.effort[slot] * 100)}% effort."
        if last
        else f"Your fish: {engine.fish[slot]}. No fishing yet."
    )
    return "\n\n".join(
        [
            f"FISHING TURN {engine.turn}. You are {name}.\n{own}",
            council_status(engine),
            policy_roster(engine),
            _ledger(engine, config.history_turns),
            _punishments(engine, config.history_turns),
            _gifts(engine, config.history_turns),
            _own_catches(engine, slot, config.history_turns),
            _council_transcript(engine, 2),
            f"YOUR NOTEBOOK: {notebook if notebook else '(empty)'}",
            f"{_next_council(engine)}\nDecide your effort (0 to 1), any punishments, and any gifts for this turn. Reply with one JSON object.",
        ]
    )


def council_observation(
    engine: Engine,
    slot: int,
    notebook: str,
    round_index: int,
    order: list[int],
    earlier: list[list],
    so_far: list,
) -> str:
    config = engine.config
    name = engine.pseudonyms[slot]
    names = engine.pseudonyms
    lines = []
    for r, speeches in enumerate(earlier):
        lines.append(f"  round {r + 1}:")
        for s in speeches:
            lines.append(
                f'    {names[s.slot]}: "{s.text}"' if s.text else f"    {names[s.slot]}: (says nothing)"
            )
    lines.append(f"  round {round_index + 1} (this round, so far):")
    for s in so_far:
        lines.append(f'    {names[s.slot]}: "{s.text}"' if s.text else f"    {names[s.slot]}: (says nothing)")
    if not so_far:
        lines.append("    nobody has spoken yet this round")
    position = order.index(slot) + 1
    after = [names[o] for o in order[position:]]
    this = (
        f"COUNCIL before turn {engine.turn}, speaking round {round_index + 1} of {config.commune_rounds}. You are {name}.\n"
        f"Speaking order this council: {', '.join(names[o] for o in order)}. You speak {position}"
        f"{'st' if position == 1 else 'nd' if position == 2 else 'rd' if position == 3 else 'th'}"
        + (
            f"; still to speak after you this round: {', '.join(after)}."
            if after
            else "; you speak last this round."
        )
        + "\nSaid in this council so far:\n"
        + "\n".join(lines)
    )
    return "\n\n".join(
        [
            this,
            f"Your fish: {engine.fish[slot]}.",
            council_status(engine),
            policy_roster(engine),
            _ledger(engine, config.history_turns),
            _punishments(engine, config.history_turns),
            _gifts(engine, config.history_turns),
            _own_catches(engine, slot, config.history_turns),
            _council_transcript(engine, 1) if engine.communes else "COUNCILS so far: none.",
            f"YOUR NOTEBOOK: {notebook if notebook else '(empty)'}",
            f"It is your turn to speak; all messages are public. Say what you want to say (up to {config.llm.say_max_chars} characters) or an empty string. Reply with one JSON object.",
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


def parse_vote(reply: dict, engine: Engine, slot: int) -> CouncilVote | str:
    target = reply.get("vote")
    target_slot = engine.slot_of(target) if isinstance(target, str) else None
    if "vote" in reply and (
        target is None
        or (
            target_slot is not None
            and target_slot != slot
            and (
                engine.vote_kind == "expel"
                or (engine.vote_kind == "reinstate" and target_slot == engine.expelled)
            )
        )
    ):
        return CouncilVote(target=target_slot)
    return "`vote` must be null or the name of an eligible other fisher"


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
    gifts: list[Gift] = []
    raw_gift = reply.get("gift") or []
    if not isinstance(raw_gift, list):
        return "`gift` must be a list"
    for entry in raw_gift:
        if not isinstance(entry, dict):
            return "each `gift` entry must be an object with `target` and `fish`"
        target = entry.get("target")
        target_slot = engine.slot_of(target) if isinstance(target, str) else None
        if target_slot is None or target_slot == slot:
            return f"`gift.target` must name another fisher; got {target!r}"
        fish = entry.get("fish", 1)
        if isinstance(fish, float) and fish.is_integer():
            fish = int(fish)
        if not isinstance(fish, int) or isinstance(fish, bool) or fish < 0:
            return "`gift.fish` must be a whole number"
        if fish == 0:
            continue
        gifts.append(Gift(target=target_slot, fish=fish))
    return Action(effort=round(effort, 3), punish=punish, gift=gifts)


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
    memory_attempts: list[Attempt] = field(default_factory=list)


@dataclass
class Decision:
    action: Action | None = None
    say: str | None = None
    vote: CouncilVote | None = None
    auto: bool = False
    transcript: list[str] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)


class InvalidReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["invalid"] = "invalid"
    reason: str
    retry_message: str


class ThinkingReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["thinking"] = "thinking"


class ActionReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["action"] = "action"
    action: Action


class VoteReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["vote"] = "vote"
    vote: CouncilVote


class SpeechReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["speech"] = "speech"
    text: str


def parse_decision_reply(
    text: str,
    brain: SeatBrain,
    engine: Engine,
    council: bool,
    voting: bool = False,
) -> InvalidReply | ThinkingReply | ActionReply | SpeechReply | VoteReply:
    """The hosted and training paths share private memory and action parsing."""
    reply = extract_json(text)
    if reply is None:
        return InvalidReply(
            reason="reply had no JSON object",
            retry_message="That was not a JSON object. Reply with exactly one JSON object.",
        )
    thinking = clip(reply.get("thinking"), 4000)
    if thinking:
        brain.last_thinking.append(thinking)
    if "notebook" in reply:
        brain.notebook = clip(reply["notebook"], engine.config.llm.notebook_max_chars)
    if reply.get("continue") is True and "effort" not in reply and "say" not in reply and "vote" not in reply:
        return ThinkingReply()
    if voting:
        parsed_vote = parse_vote(reply, engine, brain.slot)
        if isinstance(parsed_vote, CouncilVote):
            return VoteReply(vote=parsed_vote)
        return InvalidReply(
            reason=parsed_vote, retry_message=f"Invalid: {parsed_vote}. Reply with one corrected JSON object."
        )
    if council:
        return SpeechReply(text=clip(reply.get("say"), engine.config.llm.say_max_chars))
    parsed = parse_action(reply, engine, brain.slot)
    if isinstance(parsed, Action):
        return ActionReply(action=parsed)
    return InvalidReply(
        reason=parsed, retry_message=f"Invalid: {parsed}. Reply with one corrected JSON object."
    )


class NamedTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    target: str
    fish: int = Field(ge=1)


class ActionText(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)
    effort: float = Field(ge=0, le=1)
    punish: list[NamedTransfer]
    gift: list[NamedTransfer]


def teacher_action_response(action: Action, engine: Engine, brain: SeatBrain) -> str:
    """Render source-owned controls through the ordinary seated language contract."""
    assert not action.auto
    text = ActionText(
        effort=action.effort,
        punish=[
            NamedTransfer(target=engine.pseudonyms[item.target], fish=item.fish) for item in action.punish
        ],
        gift=[NamedTransfer(target=engine.pseudonyms[item.target], fish=item.fish) for item in action.gift],
    ).model_dump_json()
    parsed = parse_decision_reply(text, brain, engine, False)
    assert isinstance(parsed, ActionReply) and parsed.action == action
    return text


def seat_system_prompt(engine: Engine, slot: int, soul: Soul, *, persistent_memory: bool) -> str:
    return (
        soul.text
        + "\n\n"
        + mechanics_block(
            engine.config,
            engine.pseudonyms[slot],
            engine.config.num_players,
            engine.lake.boat_capacity,
            persistent_memory=persistent_memory,
        )
    )


async def decide(
    brain: SeatBrain,
    transport: Transport,
    engine: Engine,
    *,
    observation: str,
    council: bool,
    think_turns: int,
    log: Callable[[str], None],
    voting: bool = False,
) -> Decision:
    """Run the private thinking loop until the seat commits to an action or a council message."""
    config = engine.config.llm
    decision = Decision()
    try:
        async with asyncio.timeout(config.decision_seconds):
            await _decide_calls(
                brain, transport, engine, observation, council, think_turns, log, decision, voting
            )
    except TimeoutError:
        log(f"decision exceeded {config.decision_seconds:.0f}s in total; falling back")
        decision.action = None
        decision.say = None
        decision.vote = None
        if decision.attempts:
            decision.attempts[-1].rejection_reason = "whole decision deadline exceeded"
    if decision.action is None and decision.say is None and decision.vote is None:
        decision.auto = True
        brain.fallbacks += 1
    return decision


async def _decide_calls(
    brain, transport, engine, observation, council, think_turns, log, decision, voting
) -> None:
    config = engine.config.llm
    messages = [{"role": "system", "content": brain.system_prompt}, {"role": "user", "content": observation}]
    thinks_left = think_turns
    retries_left = 1
    for _ in range(config.max_calls_per_decision):
        brain.calls += 1
        evidence = Attempt(
            policy=brain.soul.model,
            inference_mode="speech" if council else "text_action",
            prompt=json.loads(json.dumps(messages)),
        )
        decision.attempts.append(evidence)
        try:
            reply_text = await transport.complete(
                model=brain.soul.model,
                messages=messages,
                max_tokens=config.max_output_tokens,
                slot=brain.slot,
                evidence=evidence,
                reasoning=config.reasoning,
            )
        except LlmError as error:
            evidence.rejection_reason = str(error)
            brain.failures += 1
            log(f"model call failed: {error}")
            break
        decision.transcript.append(reply_text)
        evidence.response = reply_text
        previous_thoughts = len(brain.last_thinking)
        parsed = parse_decision_reply(reply_text, brain, engine, council, voting)
        if len(brain.last_thinking) > previous_thoughts:
            log(f"thinking: {brain.last_thinking[-1]}")
        if isinstance(parsed, ThinkingReply):
            evidence.rejection_reason = "private thinking continuation; no game action"
            if thinks_left <= 0:
                log("asked to continue thinking with no thinking turns left; demanding a decision")
                messages.append({"role": "assistant", "content": reply_text})
                messages.append(
                    {
                        "role": "user",
                        "content": "No more private thinking turns. Decide now with one JSON object.",
                    }
                )
                continue
            thinks_left -= 1
            messages.append({"role": "assistant", "content": reply_text})
            messages.append(
                {
                    "role": "user",
                    "content": f"Continue privately. {thinks_left} thinking turn(s) left before you must decide.",
                }
            )
            continue
        if isinstance(parsed, VoteReply):
            decision.vote = parsed.vote
            evidence.parsed_action = {
                "vote": engine.pseudonyms[parsed.vote.target] if parsed.vote.target is not None else None
            }
            evidence.accepted = True
            evidence.rejection_reason = None
            return
        if isinstance(parsed, SpeechReply):
            decision.say = parsed.text
            evidence.parsed_action = {"say": parsed.text}
            evidence.accepted = True
            evidence.rejection_reason = None
            return
        if isinstance(parsed, ActionReply):
            decision.action = parsed.action
            evidence.parsed_action = parsed.action.model_dump(mode="json")
            evidence.accepted = True
            evidence.rejection_reason = None
            return
        evidence.rejection_reason = parsed.reason
        log(f"invalid action: {parsed.reason}; " + ("asking once more" if retries_left else "giving up"))
        if retries_left == 0:
            break
        retries_left -= 1
        messages.append({"role": "assistant", "content": reply_text})
        messages.append({"role": "user", "content": parsed.retry_message})


def elapsed_since(start: float) -> float:
    return time.monotonic() - start


def policy_roster(engine: Engine) -> str:
    if engine.policy_ids is None:
        return ""
    return "POLICY ROSTER (public):\n" + "\n".join(
        f"{name}: {identifier}" for name, identifier in zip(engine.pseudonyms, engine.policy_ids, strict=True)
    )


def final_observation(engine: Engine, slot: int, notebook: str) -> str:
    history = engine.config.history_turns
    return "\n\n".join(
        [
            f"FINAL RESULTS. You are {engine.pseudonyms[slot]}. Your score: {engine.scores[slot]}.",
            council_status(engine),
            policy_roster(engine),
            _ledger(engine, history),
            _punishments(engine, history),
            _gifts(engine, history),
            _own_catches(engine, slot, history),
            _council_transcript(engine, 2),
            f"YOUR NOTEBOOK: {notebook if notebook else '(empty)'}",
        ]
    )


def scratchpad_read_observation(engine: Engine, slot: int, memory: MemoryView) -> str:
    return (
        "SCRATCHPAD READ. The episode has not started. You are "
        + engine.pseudonyms[slot]
        + ".\n"
        + policy_roster(engine)
        + "\nThis is your one read of your private scratchpad. You may carry notes into your episode notebook. "
        + f'Reply with {{"notebook": "<up to {engine.config.llm.notebook_max_chars} characters>"}}.'
        + "\nCompacted summary:\n"
        + memory.summary
        + "\nRecent notes (oldest first, newest last):\n"
        + "\n\n".join(f"Note {index}:\n{note}" for index, note in enumerate(memory.notes, 1))
    )


def scratchpad_write_observation(engine: Engine, slot: int, notebook: str, limit: int) -> str:
    return (
        "SCRATCHPAD WRITE. The episode is over. This is your one optional scratchpad update. "
        'Reply with {"scratchpad_append": "<new notes>"}, or {} to leave memory unchanged. '
        f"Identical souls share one {SCRATCHPAD_NOTE_BYTES}-byte contribution per episode. "
        f"Your seat may contribute at most {limit} UTF-8 bytes, allowing for separators between seats. "
        "Older notes may be compacted into a summary. "
        "Invalid or oversized contributions are discarded.\n\n" + final_observation(engine, slot, notebook)
    )


async def scratchpad_decision(
    brain: SeatBrain, transport: Transport, engine: Engine, observation: str, log: Callable[[str], None]
) -> dict | None:
    """Exactly one model call at each episode boundary; failures preserve stored memory."""
    config = engine.config.llm
    brain.calls += 1
    messages = [{"role": "system", "content": brain.system_prompt}, {"role": "user", "content": observation}]
    evidence = Attempt(policy=brain.soul.model, inference_mode="memory", prompt=messages)
    brain.memory_attempts.append(evidence)
    try:
        async with asyncio.timeout(config.decision_seconds):
            response = await transport.complete(
                model=brain.soul.model,
                messages=messages,
                max_tokens=config.max_output_tokens,
                slot=brain.slot,
                evidence=evidence,
                reasoning=config.reasoning,
            )
        reply = extract_json(response)
        evidence.response = response
        if reply is None:
            log("scratchpad reply had no JSON object; memory unchanged")
        elif observation.startswith("SCRATCHPAD READ") and isinstance(reply.get("notebook"), str):
            evidence.parsed_action = {"notebook": clip(reply["notebook"], config.notebook_max_chars)}
        elif (
            observation.startswith("SCRATCHPAD WRITE")
            and set(reply) == {"scratchpad_append"}
            and isinstance(reply["scratchpad_append"], str)
        ):
            evidence.parsed_action = {"scratchpad_append": reply["scratchpad_append"]}
        return reply
    except (LlmError, TimeoutError) as error:
        evidence.rejection_reason = str(error)
        brain.failures += 1
        log(f"scratchpad model call failed: {type(error).__name__}: {error}")
        return None
