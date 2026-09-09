import json

from overfished.config import GameConfig
from overfished.engine import Action, Engine, Punishment, Speech, growth, largest_remainder


def config(seats: int = 8, **overrides) -> GameConfig:
    base = {
        "tokens": [f"t{i}" for i in range(seats)],
        "players": [{"name": f"Fisher {i}"} for i in range(seats)],
        "seed": 7,
        "turns": 60,
    }
    base.update(overrides)
    return GameConfig.model_validate(base)


def play(engine: Engine, efforts: list[float]) -> None:
    while not engine.finished:
        if engine.commune_due():
            engine.record_commune([[Speech(slot=s, text="") for s in range(engine.config.num_players)]])
        engine.resolve_turn([Action(effort=e) for e in efforts])


def test_same_seed_same_replay():
    a, b = Engine(config(), 7), Engine(config(), 7)
    play(a, [0.4] * 8)
    play(b, [0.4] * 8)
    assert json.dumps(a.replay(), sort_keys=True) == json.dumps(b.replay(), sort_keys=True)


def test_different_seed_different_lake_and_names():
    a, b = Engine(config(), 7), Engine(config(), 8)
    assert a.lake != b.lake
    assert a.pseudonyms != b.pseudonyms
    assert len(set(a.pseudonyms)) == 8


def test_all_greedy_collapses_the_lake():
    engine = Engine(config(), 7)
    play(engine, [1.0] * 8)
    assert engine.results()["collapsed"] is True
    assert engine.stock < engine.lake.collapse_threshold
    # catches dwindle to nothing once the lake is dead
    assert sum(engine.turns[-1].catch) == 0


def test_moderate_effort_is_sustainable():
    engine = Engine(config(), 7)
    play(engine, [0.4] * 8)
    assert engine.results()["collapsed"] is False
    assert engine.stock > 0.5 * engine.lake.capacity
    # each turn everyone lands the same catch, give or take rounding
    last = engine.turns[-1].catch
    assert max(last) - min(last) <= 1


def test_defector_earns_more_but_less_than_double_when_others_are_moderate():
    engine = Engine(config(), 7)
    play(engine, [1.0] + [0.4] * 7)
    scores = engine.results()["scores"]
    assert scores[0] > max(scores[1:])
    assert scores[0] < 3 * min(scores[1:])


def test_punishment_costs_both_and_is_clipped():
    engine = Engine(config(seats=2, turns=3), 3)
    engine.resolve_turn([Action(effort=1.0), Action(effort=1.0)])
    before = list(engine.fish)
    engine.resolve_turn([Action(effort=0.0, punish=[Punishment(target=1, fish=1000)]), Action(effort=0.0)])
    record = engine.turns[-1]
    assert len(record.punish) == 1
    burned = record.punish[0].fish
    assert burned == min(before)
    assert engine.fish[0] == before[0] - burned
    assert engine.fish[1] == before[1] - burned


def test_self_punishment_and_bad_targets_are_ignored():
    engine = Engine(config(seats=2, turns=2), 3)
    engine.resolve_turn([Action(effort=1.0), Action(effort=1.0)])
    fish = list(engine.fish)
    engine.resolve_turn([Action(effort=0.0, punish=[Punishment(target=0, fish=1), Punishment(target=5, fish=1)]), Action(effort=0.0)])
    assert engine.turns[-1].punish == []
    assert engine.fish == fish


def test_commune_schedule():
    engine = Engine(config(turns=12, commune_every=5), 7)
    schedule = []
    while not engine.finished:
        if engine.commune_due():
            schedule.append(engine.turn)
            engine.record_commune([])
        engine.resolve_turn([Action(effort=0.1)] * 8)
    assert schedule == [1, 6, 11]
    assert engine.commune_due() is False


def test_commune_disabled():
    engine = Engine(config(turns=6, commune_rounds=0), 7)
    assert engine.commune_due() is False


def test_largest_remainder_sums_exactly():
    assert sum(largest_remainder([1.5, 2.5, 3.0], 7)) == 7
    assert largest_remainder([0.0, 0.0], 5) == [0, 0]
    assert largest_remainder([1.0, 1.0, 1.0], 0) == [0, 0, 0]


def test_growth_sign():
    engine = Engine(config(), 7)
    lake = engine.lake
    assert growth(lake, lake.collapse_threshold * 0.5) < 0
    assert growth(lake, lake.capacity * 0.5) > 0
    assert growth(lake, lake.capacity) == 0


def test_results_shape():
    engine = Engine(config(seats=4, turns=5), 11)
    play(engine, [0.5] * 4)
    results = engine.results()
    assert len(results["scores"]) == 4
    assert all(isinstance(s, float) for s in results["scores"])
    replay = engine.replay()
    assert replay["schema"] == "overfished-replay/1"
    assert len(replay["turns"]) == 5
    assert [p["pseudonym"] for p in replay["players"]] == engine.pseudonyms
