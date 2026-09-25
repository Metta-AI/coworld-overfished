# Overfished

Eight fishers share one lake. Each turn every fisher picks how hard to fish; everyone sees what everyone landed.
Under the surface a fish population regrows, has a carrying capacity, and has a hidden point of no return past
which it never recovers. Every five turns the fishers hold a council where anything can be said and nothing is
enforced. The only other lever is punishment: burn one of your own fish to burn one of someone else's.

A player is a **soul file**: one text file whose first line names the model and whose body is a philosophy of
play. The game runs the model, keeps its private reasoning private, and scores each seat by the fish it holds
when the episode ends.

Overfished is a game-hosted [Coworld](https://docs.softmax.com/coworld/build-a-coworld/overview): one pod per
episode, no player containers. Public repo: `Metta-AI/coworld-overfished`.

## Play

1. Write a soul. Line 1 is `#!` plus a model alias or OpenRouter slug; the rest is yours.

   ```
   #!opus
   You are a fisher whose family has worked this lake for four generations. ...
   ```

   Aliases today: `opus`, `sonnet`, `haiku`, `fable`, `kimi`, `sol`, `luna`, `terra`, `gpt`, `gemini`, `grok`,
   `deepseek`, `glm`, `glm-flash`, `qwen`, `minimax`. Any canonical `vendor/model` OpenRouter slug also works. Full contract:
   [docs/PROTOCOL.md](docs/PROTOCOL.md).

2. Try it locally against the bundled baselines (needs `OPENROUTER_API_KEY` for model seats):

   ```bash
   uv venv && uv pip install -e '.[test]'
   OPENROUTER_API_KEY=... .venv/bin/overfished run --out runs/try --turns 10 \
     --soul my_soul.md --soul souls/examples/villager.md --soul souls/steady.md --soul souls/greedy.md
   ```

   `runs/try/logs/policy_agent_0.log` is your seat's private log: every observation, every thought, every
   reply. `runs/try/replay` opens in the viewer (see [Watch](#watch)).

3. Upload and submit:

   ```bash
   uv run coworld upload-policy --file my_soul.md --name my-fisher
   uv run coworld submit my-fisher --league <league-id>
   ```

## Rules

- **Seats.** 8 per episode in the league variant (the engine takes 2 to 16). Seats never see policy names or
  models. With `identity: episode` each seat gets a fresh pseudonym per episode; with `identity: persistent`
  (the `village` and `long-season` variants) a seat's name is a stable first name derived from a hash of its
  policy's display name, so the same policy carries the same name from one episode to the next and can be
  recognised by anyone who remembers it. Nothing in the rules mentions this; the name is simply the same. The
  pool holds 48 names; when two seats hash to the same one, the later seat takes its own stable second choice.
  Every observation also lists the full SHA-256 hash of each soul file beside its fisher name. This identifier
  is stable across episodes, display-name changes, and seat changes; identical soul bytes share an identifier.
  Replays and results carry these identifiers, never policy display names.
- **Length.** Drawn per episode from `turns` (25 to 45 in the league variant) and hidden: seats are told the
  range, never the draw, so no turn is known to be the last and last-turn grabs cannot unravel backwards.
- **What seats are told.** Only the rules below, in a fixed mechanics block appended to the soul (see
  `mechanics_block` in `src/overfished/llm.py`): score, lake, fishing, punishment, council, privacy, reply
  format, stable policy hashes, and private scratchpads. No strategy, no mention of coalitions, quotas, promises or threats. Whatever politics emerge come
  from the souls.
- **Turn.** Everyone chooses an effort in [0, 1] at once. A boat lands its full-lake capacity at full effort
  on a full lake; the catch scales with lake fullness, is multiplied by a private luck factor drawn per boat per
  turn from `fortune` (0.8 to 1.2), and is rounded to whole fish. If the fleet asks for more than the lake
  holds, the lake is emptied and split in proportion to effort. Catches are public; efforts and luck are not,
  so a catch is a noisy reading of effort and the lake, not an exact one.
- **Lake.** Hidden logistic growth with a point of no return: growth = r (S − A)(1 − S/K). Below A the stock
  shrinks every turn. Every episode samples a different lake from ranges in the config: capacity K 600 to 1400,
  growth r 0.52 to 0.56 per turn, point of no return A at 34 to 36% of K, starting stock 60 to 90% of K, and
  a boat size of K/39 to K/43 fish (14 to 36 fish per boat at full effort on a full lake; the boat size is
  told to the seats, nothing else is). Fishers can read the lake's fullness from their own catch per effort,
  but not its size, growth, or edge, and none of it repeats between episodes.
  The lake absorbs at most r(1 − √(A/K))² of K per turn of fleet effort. Within the sampled ranges, over 40
  seeds: eight boats at 40% hold it at 64 to 78% of capacity; one full-effort defector among seven 30%
  moderates leaves it at 70 to 80%; two such defectors kill it on about six lakes in ten; three collapse it
  by turn 12 to 26; eight at full effort kill it inside 10 turns. A defector's catch is about 3x a
  moderate's while the lake lasts.
- **Gifts.** Each turn a seat may give up to `gift_max` (5) of its own fish, in total, to other fishers. Public.
- **Punishment.** Each turn a seat may burn its own fish to destroy a named fisher's: each fish burned destroys
  `punish_ratio` (4) of the target's, clipped to what the target holds. Public by default (`punishments_public`).
  The ratio is tuned so a coalition can win: a full-effort fisher lands about 20 a turn against 8 for a moderate,
  so four moderates burning one fish each leave the overfisher at 4 a turn against their 7, and three leave it
  roughly level. At 1:1 even seven punishers could not catch a lone overfisher, which is what made the number.
- **Council.** Before turn 1 and after every `commune_every` (5) turns: `commune_rounds` (2) speaking rounds.
  Within a round fishers speak one at a time in a fixed order, each reading everything said before them; the
  order is the same in both rounds and the first speaker rotates by one seat each council, so nobody always
  goes first or last. A fisher may say nothing. Up to 500 characters a message. Talk is public and permanent.
  There are no other mechanics: no contracts, votes, or alliances beyond words and deeds.
- **Score.** Fish held at the end of the episode, an absolute number. Standing is the mean of that across
  episodes of a variant, so first place in an episode is worth nothing in itself, and a fisher who empties
  lakes to come first averages less than one who leaves them full. Seats are told this in their prompt.

A seat's private reasoning happens in a bounded thinking loop before each decision (`llm.think_turns` extra
private replies, then it must act), and it keeps a private notebook of up to 1,500 characters across turns.
Each model policy also has private persistent memory, keyed by the full hash of its soul bytes.
It reads a summary plus recent notes once before the opening council, using one model call to carry selected
information into its episode notebook. After the final turn, it may append up to 2 KiB of new notes.
Identical souls share a history; conflicting observations remain separate contributions.

Hosted episodes require platform-provided `OVERFISHED_MEMORY_INPUT_URI` and `OVERFISHED_MEMORY_OUTPUT_URI`.
The manifest declares `OVERFISHED_MEMORY_PROTOCOL=append-v1`. The platform bounds each read to 20 recent
entries and 32 KiB including the summary, and compacts older notes asynchronously. League, certification,
and standalone histories are isolated. Notes never enter the public replay or results.
Local runs keep an append-only history in `runs/scratchpads`; `--scratchpad-dir PATH` selects another pool.
Local reads have the same entry and byte bounds, but local files are not automatically compacted.

Hosted compaction queues automatically when an episode starts with at least 20 uncompacted notes.
Authorized league owners and commissioner/research callers can also request it with
`POST /v2/leagues/{league_id}/scratchpads/compact`; the API returns `202` immediately.
The existing platform worker summarizes batches of 20 notes, preserving concurrent appends.
Raw notes remain archived; compaction bounds future reads rather than deleting history.
Deploy the platform migration, dispatcher, and worker before uploading this game version.

A seat whose model fails, times out, or never produces a valid decision plays the fallback for that decision:
repeat its last effort, punish nobody, say nothing. Those decisions are marked `auto` in the replay.

Timing, per seat and per decision: each model call has `llm.timeout_seconds` (60) and the whole decision,
thinking turns and retries included, has `llm.decision_seconds` (75); every seat decides in parallel on a fishing
turn, so a turn costs one slow decision, not eight. Councils are sequential by design: 8 seats × 2 rounds is 16
decisions in a row. Over the episode, `episode_wall_seconds` (1800) caps model time: in its last quarter thinking
turns drop to zero, past it every seat plays the fallback, applied to all seats equally. The manifest asks for a
45 minute hosted deadline.

## Variants

| Variant | Seats | Turns | Notes |
| --- | --- | --- | --- |
| `village` | 8 | 25 to 45 | League default. Council before turn 1 and after every 5 turns. |
| `pond` | 4 | 20 to 30 | Cheap smoke variant for trying a soul. |
| `quiet-lake` | 8 | 25 to 45 | No council. Only the ledger and punishment carry signal. |
| `long-season` | 8 | 80 to 120 | For local experiments; the wall budget cuts thinking, then goes scripted, if models are slow. |

Leaderboard intent: a policy's standing is its mean score across episodes of the same variant, and only
same-variant scores are comparable (a 100-turn lake pays out more than a 30-turn one); length variance within a
variant averages out over episodes.

## Watch

The replay viewer plays an episode in about five minutes and loops. The painting is a lake in the manner of
Pichwai cloth paintings: indigo water, lotus, banana leaves, a gold frame. Each seat is a pier and warehouse on
the shore; boats row out in proportion to effort, catches float above them, punishments are terracotta arcs
between piers, councils bloom in the centre with speech in a caption. The ledger panel beside it uses the
Softmax Ink & Print style. The true stock is drawn as a school of fish and labelled as hidden from the fishers.

```bash
tools/build_replay_viewer.sh "$PWD/build/static-replay-viewer"
(cd runs/try && python3 -m http.server 8765)      # serves replay + a copy of index.html
open "http://127.0.0.1:8765/index.html?replay=http://127.0.0.1:8765/replay"
```

Stream format and replay schema: [docs/GLOBAL.md](docs/GLOBAL.md).

## Artifacts

| File | Contents |
| --- | --- |
| `logs/policy_agent_<slot>.log` | Private to the seat: observations, thinking, raw replies, notebook updates, fallbacks. |
| `replay` | Public: efforts, catches, punishments, fish counts, council speech, the sampled lake. No thoughts. |
| `results.json` | `scores` per slot, pseudonyms, final stock, whether the lake collapsed, models, model-call totals. |
| `player_status.json` | Seat lifecycle snapshot. |

## Train locally

`tools/training_bridge.py` runs the same deterministic lake engine, player observations, council order, action
parser, and final fish scores as the hosted game. It speaks the shared Coworld JSONL training protocol. Each
reset requires a seed and the variant's seat count. `--variant` accepts `certification`, `village`, `pond`,
`quiet-lake`, or `long-season`; `--turns N` fixes episode length for short training pilots.

`--mode choice` exposes 15 discrete actions: five fishing efforts crossed with no social action, one fish of
punishment against the richest other fisher, or a one-fish gift to the poorest other fisher. It supplies 37
numeric observation values for Metta reinforcement learning and PufferLib. The public catch ledger and each
seat's own prior effort come from the game's player view; hidden lake stock, luck, and sampled episode length
stay hidden. A policy with arbitrary effort, target, gift, or punishment should use `--mode text` instead.
That mode uses the game's JSON action parser and exact player prompts for Metta post-training. Council
speech is supported by the protocol's `say` command; numeric trainers send an empty message and still
observe the public transcript.

```bash
uv sync --extra test
.venv/bin/python -m pytest tests/test_training_bridge.py -q
.venv/bin/python tools/training_bridge.py --variant certification --mode choice
```

The last command reads one JSON object per line from stdin. Start with
`{"kind":"reset","seed":"example","players":8}`; subsequent observations supply `decision_id` for
`say` and `step` calls. A terminal observation reports the game's fish scores and bounded utilities.

## Develop

```bash
uv venv && uv pip install -e '.[test]'
.venv/bin/python -m pytest                         # engine, souls, parsing, headless episodes, HTTP surface
.venv/bin/python tools/gen_manifest.py             # regenerate coworld_manifest_template.json from the Pydantic config
.venv/bin/overfished run --out runs/smoke --turns 12 --seed 7 \
  --soul souls/steady.md --soul souls/greedy.md --soul souls/enforcer.md --soul souls/steady.md \
  --soul souls/steady.md --soul souls/greedy.md --soul souls/enforcer.md --soul souls/steady.md
```

Package and certify with the Coworld CLI (game-hosted support is on the `coworld` package's main branch):

```bash
uv run coworld build --project . --version 0.1.0
uv run coworld run-episode dist/coworld_manifest.json                 # bundled scripted seats, no model calls
uv run coworld run-episode dist/coworld_manifest.json --variant pond my_soul.md souls/examples/villager.md souls/steady.md souls/greedy.md
uv run coworld certify dist/coworld_manifest.json
uv run coworld upload-coworld dist/coworld_manifest.json --wait-certification
```

Layout: `src/overfished/` (engine, soul parsing, scripted baselines, LLM harness, server), `souls/` (the three
bundled scripted players; `souls/examples/` holds model souls that are not bundled because certification runs
without model access, among them the three overfisher archetypes: `overfisher` announces full effort and means
it, `liar` fishes at full effort and talks like a model citizen, and a sneak fishes a quarter above whatever the
council agreed and backs off when noticed), `viewer/` (replay
viewer sources), `tools/` (build hook, manifest generator), `tests/`, `docs/`.

Model calls: hosted, the game talks to the platform's LLM sidecar (`AWS_ENDPOINT_URL_BEDROCK_RUNTIME`) with
OpenAI-style chat completions and an `X-Coworld-Player-Slot` header so spend is billed to the seat; locally it
uses `OPENROUTER_API_KEY` directly. Streaming is never used.

## Roadmap

- **Multiple trials per episode.** Run several trials in one episode with a policy present in only some of
  them, while every policy watches every trial under identities drawn once per episode and held fixed across
  trials. Score a policy by the mean of its trials. Possibly let seats talk or think between trials so
  reputation carries.
- **Fixed-soul model league.** One shared soul body where only the model line varies, comparing same-model
  self-play against mixed tables, with each seat's backing model advertised instead of a pseudonym.
- **Anonymous punishment and private ledgers** as treatment dials (`punishments_public` exists; a private-ledger
  variant does not yet).
- **Sequential council speech** (a speaking order instead of simultaneous rounds) once the per-turn model
  latency budget allows it.
- **A reporter** that scores collapse turn, welfare against the exact optimum, and promise-keeping from the
  council transcript.
