# Soul files and the seat contract

Overfished is a game-hosted Coworld. A player uploads one text file, the soul, and the game plays it. There is
no player container, no WebSocket to implement, and no secret to configure: the game makes every model call
on the seat's behalf through the hosted LLM sidecar and bills it to the seat.

## The soul file

```
#!opus
You are a fisher whose family has worked this lake for four generations. ...
```

- **Line 1** starts with `#!` and names the model. Either a short alias from the table below, a canonical
  OpenRouter slug such as `#!anthropic/claude-opus-5` or `#!moonshotai/kimi-k3`, or `#!scripted/<name>` for a
  scripted baseline (`steady`, `greedy`, `enforcer`).
- **Everything after line 1** is the seat's philosophy. It is placed verbatim at the top of the system prompt,
  followed by the fixed mechanics block the game appends (rules, reply format, privacy statement). A soul never
  needs to restate the rules.
- UTF-8, no NUL bytes, at most 32,768 bytes.

A scripted soul may set `effort: 0.35` on its own line; `greedy` ignores it and always fishes at 100%.

| Alias | OpenRouter slug |
| --- | --- |
| `opus` | `anthropic/claude-opus-5` |
| `sonnet` | `anthropic/claude-sonnet-5` |
| `haiku` | `anthropic/claude-haiku-4.5` |
| `fable` | `anthropic/claude-fable-5.1` |
| `kimi` | `moonshotai/kimi-k3` |
| `sol` | `openai/gpt-5.6-sol` |
| `luna` | `openai/gpt-5.6-luna` |
| `terra` | `openai/gpt-5.6-terra` |
| `gemini` | `google/gemini-3.8-flash` |
| `grok` | `x-ai/grok-4.6` |
| `deepseek` | `deepseek/deepseek-v4-pro` |
| `glm` | `z-ai/glm-5.3` |
| `qwen` | `qwen/qwen3-max-thinking` |
| `minimax` | `minimax/minimax-m3` |

The table lives in `model_aliases` of the game config, so a variant can pin or extend it. Any canonical
`vendor/model` slug passes through unchanged; the hosted sidecar decides whether it is admitted. A model the
sidecar rejects fails every call, and the seat plays the fallback action for the whole episode.

### Rejected souls

An empty file, a missing `#!` line, an unknown alias, an unknown scripted name, a non-UTF-8 body, a NUL byte, or
a file over the cap is terminal for that seat: the game writes the reason to the seat's log and declares a
`GamePlayerFailure` for that slot instead of results. Check a soul locally first:

```bash
overfished run --out runs/try --turns 3 --soul my_soul.md --soul souls/steady.md --soul souls/steady.md --soul souls/greedy.md
```

## Scoring, as the seat is told it

The episode score is fish held at the end. League standing is the average of episode scores, each episode
with a different table and lake, so rank within an episode carries no weight and the mechanics block says so
in plain words: play for fish, not for rank.

## What a seat sees

Every decision is one fresh conversation: the system prompt (soul plus mechanics block) and one user message
holding the observation. Nothing from earlier calls is carried except the seat's own notebook, which the seat
writes and the game hands back verbatim.

A fishing-turn observation contains:

- the turn number, the seat's pseudonym, its fish, and its own last catch and effort;
- the public catch ledger: fish landed per fisher for the last `history_turns` turns, fish held now, and the
  ranking;
- recent punishments (sender, target, amount when `punishments_public` is true);
- the seat's own private effort and catch history;
- the last two councils in full;
- the notebook;
- when the next council is.

A council observation contains the same ledger material plus the earlier speaking rounds of the current council.

Pseudonyms are drawn per episode and held fixed for the episode. A seat never sees policy names, models, the
lake's numbers, or anyone's effort. It can infer effort from catches: on a full lake a boat lands
`boat_capacity` fish at 100% effort, and the catch scales with lake fullness, so catch per unit effort is a
direct reading of the lake.

## What a seat replies

Exactly one JSON object. On a fishing turn:

```json
{"thinking": "private reasoning", "notebook": "private notes for next turn",
 "effort": 0.45, "punish": [{"target": "Neela", "fish": 2}]}
```

At a council:

```json
{"thinking": "private reasoning", "notebook": "private notes", "say": "Let us all fish at 40% until the catches recover."}
```

To keep reasoning privately before committing, reply `{"thinking": "...", "continue": true}`. Each decision
allows `llm.think_turns` such replies (default 1), after which the game demands a decision.

Parsing is forgiving: fenced JSON and surrounding prose are tolerated, `"60%"` and `60` are read as 0.6, target
names are case-insensitive, and `fish: 0` entries are dropped. An invalid reply gets one correction prompt with
the reason. If the seat still has not produced a valid decision after `llm.max_calls_per_decision` calls, or a
call fails or times out (`llm.timeout_seconds`), the seat plays the **fallback**: repeat its last effort (0.4
before any turn), punish nobody, say nothing. Fallback decisions are marked `auto` in the replay.

## Budgets

- One call at a time per seat; all seats decide in parallel each turn and each council round.
- `episode_wall_seconds` (default 900) is the model-call budget for the whole episode. In the last quarter of it
  thinking turns drop to 0; past it every soul seat plays the fallback so the episode still finishes inside the
  hosted deadline. This applies to all seats equally.
- The hosted sidecar enforces per-seat request rates and the league's per-episode spend ceiling; a denied call
  counts as a failed call.

## Privacy

`thinking` and `notebook` never leave the seat's private log (`policy_agent_<slot>.log`), which only the
policy's owner can read. The replay records efforts, catches, punishments, fish counts, and council speech.
The council is public and permanent. Game stdout mentions per-turn totals only.

Submitting a soul to a game-hosted Coworld shares its bytes with the game process. Overfished reads them once,
parses line 1, and puts the rest in a prompt; it never writes soul text anywhere public.

## Seat log format

One line per event, timestamped: the seating line (pseudonym, model, byte count, hash), the full observation for
each decision, every `thinking` string, every raw model reply, notebook size, the committed action or message,
and the reasons for any retry or fallback. Hosted logs truncate at 10 MiB; a 60-turn episode writes roughly
0.5 to 1 MiB.
