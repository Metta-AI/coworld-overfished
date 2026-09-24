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
| `glm-flash` | `z-ai/glm-5.3-flash` |
| `gpt` | `openai/gpt-5.5` |
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

## What the seat is told

Only the mechanics block: the episode length as a range, never the draw; score (fish held at the end; league standing is the average across episodes, rank
within an episode counts for nothing), the lake (hidden, regrows, has a capacity and a point of no return,
differs every episode), fishing (effort 0 to 1, the boat's full-lake catch for this episode, catches public,
efforts private), punishment (the burn ratio, visibility), the council procedure, privacy, and the reply
format, stable policy hashes, and private cross-episode scratchpads. No strategy and no vocabulary of coalitions, quotas, promises or threats is supplied.

## What a seat sees

Every decision is one fresh conversation: the system prompt (soul plus mechanics block) and one user message
holding the observation. Nothing from earlier calls is carried except the seat's own notebook, which the seat
writes and the game hands back verbatim.

A fishing-turn observation contains:

- the turn number, the seat's pseudonym, its fish, and its own last catch and effort;
- the public catch ledger: fish landed per fisher for the last `history_turns` turns and fish held now;
- recent punishments (sender, target, amount when `punishments_public` is true) and recent gifts;
- the seat's own private effort and catch history;
- the last two councils in full;
- the notebook;
- when the next council is.

A council observation contains the same ledger material plus the speaking order, the seat's position in it, who
still speaks after it this round, and everything said in this council so far, round by round. Speaking is
sequential: the second speaker reads the first speaker's message before writing its own. The first speaker
rotates by one seat each council.

Pseudonyms are drawn per episode and held fixed for the episode. A seat never sees policy names, models, the
lake's numbers, anyone's effort, or anyone's luck. A catch is effort x boat capacity x fullness x a private luck
factor (0.8 to 1.2, redrawn per boat per turn), so catch per unit effort is a noisy reading of the lake and a
public catch is a noisy reading of effort. Boat capacity, lake size, growth and the point of no return all change
per episode. Under `identity: persistent` (the league variants) the seat's name is stable across episodes.
Every observation includes a roster mapping fisher names to `sha256:<full digest>` identifiers computed from
actual soul file bytes. The same bytes share an identifier and scratchpad, including duplicate seats;
renaming or reseating a policy does not change it. Changing any soul bytes changes the identifier.
The replay's `players[].policy` and results' `policy_ids` use these same identifiers.

## What a seat replies

Exactly one JSON object. On a fishing turn:

```json
{"thinking": "private reasoning", "notebook": "private notes for next turn",
 "effort": 0.45, "punish": [{"target": "Neela", "fish": 2}], "gift": [{"target": "Ravi", "fish": 3}]}
```

`gift` moves up to `gift_max` (5) fish per turn in total from the seat to named fishers; entries beyond the
budget or the seat's holdings are clipped. Gifts resolve after the catch and before punishments.

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

- Every call carries OpenRouter's `reasoning` parameter (`llm.reasoning`, default `{"effort": "low"}`) so a
  reasoning model such as Kimi K3 cannot spend the whole `llm.max_output_tokens` (default 4000) on hidden thinking
  and return nothing. Put deliberation in the visible `thinking` field and the private thinking turns instead.

- Fishing turns: all seats decide in parallel; each seat makes its calls sequentially (at most
  `llm.max_calls_per_decision`, default 4). Councils: seats speak one at a time.
- `llm.timeout_seconds` (60) bounds one call; `llm.decision_seconds` (75) bounds a whole decision including
  thinking turns and retries. Either expiring means the fallback for that decision.
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

## Persistent scratchpads

Each model policy gets one private read before the opening council (or before fishing when councils are
disabled) and one optional private write after the last fishing turn. The read call receives the whole saved
scratchpad and public policy roster, then returns `{"notebook": "notes for this episode"}`. The notebook's
existing character limit still applies; the scratchpad is not supplied again during play or at the final write.
The final call sees the final holdings, recent public history, roster, and current private notebook. It returns
`{"scratchpad": "replacement"}`, `{"scratchpad_append": "text to append"}`, or `{}` to make no change. An empty
replacement clears the scratchpad. Fishing and council replies cannot modify persistent memory.

The cap is 1,000,000 UTF-8 bytes, enforced on the resulting text. There is one model call per boundary with no
retry; model context and output limits still apply. Appends allow memory to grow without rewriting it all in
one response. Failures, invalid replies, and oversized updates retain the previous contents. The game reserves
one decision budget for the final write; a completely exhausted wall budget skips model calls. Scripted
baselines do not use scratchpads.

Scratchpads are stored privately under `--scratchpad-dir` (local default `runs/scratchpads`) or
`OVERFISHED_SCRATCHPAD_DIR`. Use a distinct directory for each pool or experiment. Hosted startup requires
that environment variable and a durable shared filesystem mounted by the runner; an episode-local directory
cannot persist across pods. No hosted volume or platform persistence API is provisioned by this game.

Writes are locked and atomic. Concurrent episodes and duplicate seats read their own starting snapshots;
appends merge, while a replacement is rejected if another write changed the stored text since that snapshot.
Within an episode writes commit in slot order. Scratchpad contents are never added to public artifacts.

The headless training bridge has no soul-file roster or persistent scratchpad interface; it does not advertise
these memory mechanics. Its replay policy tags retain the existing display-name hashes.
