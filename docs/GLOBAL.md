# Spectator stream and replay format

## Routes

| Route | Purpose |
| --- | --- |
| `GET /healthz` | 200 once the game is ready. |
| `GET /client/global` | The live viewer (same page as the replay viewer, in live mode). |
| `/global` (WebSocket) | Live public state; see messages below. Pings are answered with matching pongs. |
| `GET /client/player?slot=N&token=T` | A read-only private page for one seat that streams its log. Wrong token: 403. |
| `/player?slot=N&token=T` (WebSocket) | Private log stream for that seat. |
| `GET /client/replay`, `/replay`, `/replay.json` | Container replay fallback when started with `COGAME_LOAD_REPLAY_URI`. |

The manifest declares a static replay viewer bundle, so hosted replays open without the container.

## Live messages on `/global`

Every message is a JSON object with a `type`:

| `type` | Payload | When |
| --- | --- | --- |
| `snapshot` | `phase`, `live`, `replay` (the replay document so far) | On connect. |
| `speech` | `before_turn`, `round`, `speeches[]` | After each council speaking round. |
| `commune` | `commune` (a full council record) | After the council's last round. |
| `turn` | `turn` (a turn record) | After each fishing turn resolves. |
| `end` | `scores[]` | After results are written. |

## Replay document

Written uncompressed as JSON to `COGAME_SAVE_REPLAY_URI`; the platform gzips the public browser copy, and the
viewer sniffs the gzip magic rather than trusting the URL. A 60-turn, 8-seat episode is roughly 60 to 120 KB.

```json
{
  "schema": "overfished-replay/1",
  "seed": 7,
  "game": {"turns": 60, "commune_every": 5, "commune_rounds": 2, "commune_at_start": true,
           "boat_capacity": 25, "punishments_public": true},
  "lake": {"capacity": 1193.0, "growth_rate": 0.27, "collapse_threshold": 139.5, "initial_stock": 976.4},
  "players": [{"slot": 0, "pseudonym": "Padma", "policy": "villager", "model": "anthropic/claude-opus-5"}],
  "turns": [{"t": 1, "stock_before": 976.4, "effort": [0.4, 1.0], "catch": [8, 20],
             "punish": [{"frm": 0, "to": 1, "fish": 1}], "fish": [7, 19], "stock_after": 939.1, "auto": []}],
  "communes": [{"before_turn": 1, "rounds": [[{"slot": 0, "text": "...", "auto": false}]]}],
  "scores": [89, 191]
}
```

- `lake` is the sampled hidden model. Spectators and analysts see it; seats never do.
- `turns[].effort` is per seat, in [0, 1]. Efforts are private in-game and public in the replay.
- `turns[].auto` lists seats whose action was the fallback. `speeches[].auto` marks a fallback (silent) message.
- `players[].model` and the `models` array in results are present when `reveal_models` is true (the default).
- Private thinking and notebooks are never in the replay.

## Results

`results.json` carries `scores` (fish held per seat, by slot), `pseudonyms`, `turns_played`, `final_stock`,
`capacity`, `collapsed` (the lake ended below its point of no return), `total_catch`, `models` when revealed, and
an `llm` block with call and token counts.

## The viewer

`viewer/index.html` plus `viewer.css` and `viewer.js` are inlined into one file by `tools/build_replay_viewer.sh`.

- Reads the replay URL from `#replay=` then `?replay=`; with neither it goes live on `/client/global` or loads
  `/replay.json` on `/client/replay`.
- Plays the whole episode in about five minutes: councils get roughly 40% of the time, fishing turns the rest,
  then loops. Space pauses, arrows seek five seconds, the speed button cycles 0.5× to 4×, the timeline marks
  councils in gold.
- `?chrome=off` hides the ledger panel and transport for thumbnails. `?t=<seconds>` starts playback there and
  `?paused=1` starts paused, for screenshots.
- Posts the Coworld readiness messages (`loading`, `phase`, `ready`, `error`) to its parent frame.
- The painting shows the true stock as a school of fish and a cartouche; both are labelled as hidden from the
  fishers. Boats travel out in proportion to effort, catches float above them, punishments are terracotta arcs.
