import pytest

from overfished.config import DEFAULT_MODEL_ALIASES, GameConfig
from overfished.engine import Action, Engine
from overfished.llm import extract_json, parse_action
from overfished.scripted import SCRIPTED_NAMES, scripted_policy, soul_effort
from overfished.soul import SOUL_MAX_BYTES, SoulError, parse_soul

ALIASES = dict(DEFAULT_MODEL_ALIASES)
SCRIPTED = set(SCRIPTED_NAMES)


def test_alias_resolves():
    soul = parse_soul(b"#!opus\nBe kind.", ALIASES, SCRIPTED)
    assert soul.model == "anthropic/claude-opus-5"
    assert soul.text == "Be kind."
    assert not soul.scripted


def test_full_slug_passes_through():
    soul = parse_soul(b"#!moonshotai/kimi-k3\n\nfish", ALIASES, SCRIPTED)
    assert soul.model == "moonshotai/kimi-k3"


def test_scripted_soul():
    soul = parse_soul(b"#!scripted/steady\neffort: 0.25\n", ALIASES, SCRIPTED)
    assert soul.scripted and soul.scripted_name == "steady"
    assert scripted_policy("steady", soul.text).effort == 0.25
    assert soul_effort("no effort here") == 0.4


@pytest.mark.parametrize(
    "data, fragment",
    [
        (b"", "empty"),
        (b"no shebang line\n", "line 1"),
        (b"#!banana\n", "unknown model alias"),
        (b"#!scripted/nope\n", "unknown scripted"),
        (b"#!Vendor/Model With Spaces\n", "neither"),
        (b"#!opus\n\x00", "NUL"),
        (b"#!opus\n" + b"\xff\xfe", "UTF-8"),
        (b"#!opus\n" + b"x" * SOUL_MAX_BYTES, "cap"),
    ],
)
def test_rejected_souls(data, fragment):
    with pytest.raises(SoulError, match=fragment):
        parse_soul(data, ALIASES, SCRIPTED)


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('Sure!\n```json\n{"effort": 0.5, "punish": []}\n```\nDone.') == {
        "effort": 0.5,
        "punish": [],
    }
    assert extract_json("no json here") is None
    assert extract_json('{"a": 1} trailing {"b": 2}') == {"a": 1}


def engine() -> Engine:
    config = GameConfig.model_validate(
        {
            "tokens": ["a", "b", "c"],
            "players": [{"name": "x"}, {"name": "y"}, {"name": "z"}],
            "seed": 5,
            "turns": {"lo": 3, "hi": 3},
        }
    )
    return Engine(config, 5)


def test_parse_action_variants():
    e = engine()
    other = e.pseudonyms[1]
    action = parse_action({"effort": "60%", "punish": [{"target": other.lower(), "fish": 2}]}, e, 0)
    assert isinstance(action, Action)
    assert action.effort == 0.6
    assert action.punish[0].target == 1 and action.punish[0].fish == 2
    assert parse_action({"effort": 45}, e, 0).effort == 0.45
    gifted = parse_action(
        {"effort": 0.3, "gift": [{"target": other, "fish": 2}, {"target": other, "fish": 0}]}, e, 0
    )
    assert (
        isinstance(gifted, Action)
        and gifted.gift[0].target == 1
        and gifted.gift[0].fish == 2
        and len(gifted.gift) == 1
    )
    assert "gift.target" in parse_action(
        {"effort": 0.3, "gift": [{"target": e.pseudonyms[0], "fish": 1}]}, e, 0
    )
    assert parse_action({"effort": 0.3, "punish": [{"target": other, "fish": 0}]}, e, 0).punish == []


def test_parse_action_rejections():
    e = engine()
    assert "effort" in parse_action({"effort": "lots"}, e, 0)
    assert "effort" in parse_action({"effort": 2.0}, e, 0)
    assert "target" in parse_action({"effort": 0.5, "punish": [{"target": e.pseudonyms[0]}]}, e, 0)
    assert "target" in parse_action({"effort": 0.5, "punish": [{"target": "Nobody"}]}, e, 0)
    assert "whole" in parse_action(
        {"effort": 0.5, "punish": [{"target": e.pseudonyms[2], "fish": 1.5}]}, e, 0
    )


def test_manifest_declares_named_players_inline():
    """The platform's check (coworld.manifest_validation._declares_named_players) needs an inline object schema."""
    import json
    from pathlib import Path

    manifest = json.loads(
        (Path(__file__).resolve().parent.parent / "coworld_manifest_template.json").read_text()
    )
    players = manifest["game"]["config_schema"]["properties"]["players"]
    assert players["type"] == "array"
    items = players["items"]
    assert items["type"] == "object" and items["properties"]["name"]["type"] == "string"
    assert "$ref" not in json.dumps(items)


def test_vote_parser_rejects_self_unknown_and_wrong_reinstatement():
    from test_engine import config

    from overfished.engine import CouncilVote, Engine
    from overfished.llm import parse_vote

    engine = Engine(config(), 7)
    assert parse_vote({"vote": None}, engine, 0) == CouncilVote()
    assert parse_vote({"vote": engine.pseudonyms[1]}, engine, 0) == CouncilVote(target=1)
    for value in [engine.pseudonyms[0], "unknown", 1, True, [], {}]:
        assert isinstance(parse_vote({"vote": value}, engine, 0), str)
    assert isinstance(parse_vote({}, engine, 0), str)
    engine.expelled = 1
    engine.expulsion_used = True
    assert isinstance(parse_vote({"vote": engine.pseudonyms[2]}, engine, 0), str)
    assert parse_vote({"vote": engine.pseudonyms[1]}, engine, 0) == CouncilVote(target=1)


def test_teacher_pseudonym_targets_roundtrip_through_ordinary_native_parser():
    import json

    from overfished.engine import Gift, Punishment
    from overfished.llm import ActionReply, SeatBrain, parse_decision_reply, teacher_action_response
    from overfished.scripted import ScriptedView

    game = engine()
    soul = parse_soul(b"#!scripted/enforcer\neffort: 0.4", ALIASES, SCRIPTED)
    brain = SeatBrain(0, soul, "ordinary private system")
    action = Action(effort=0.4, punish=[Punishment(target=1, fish=2)], gift=[Gift(target=2, fish=1)])
    text = teacher_action_response(action, game, brain)
    wire = json.loads(text)
    assert "auto" not in wire
    assert wire["punish"][0]["target"] == game.pseudonyms[1]
    assert wire["gift"][0]["target"] == game.pseudonyms[2]
    parsed = parse_decision_reply(text, brain, game, False)
    assert isinstance(parsed, ActionReply) and parsed.action == action
    assert isinstance(parse_action(action.model_dump(), game, 0), str)

    enforcer = scripted_policy("enforcer", soul.text).act(
        ScriptedView(slot=0, num_players=3, own_fish=5, last_catches=(1, 20, 2))
    )
    assert enforcer.punish == [Punishment(target=1, fish=1)]
    parsed_enforcer = parse_decision_reply(teacher_action_response(enforcer, game, brain), brain, game, False)
    assert isinstance(parsed_enforcer, ActionReply) and parsed_enforcer.action == enforcer
