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
    assert extract_json('Sure!\n```json\n{"effort": 0.5, "punish": []}\n```\nDone.') == {"effort": 0.5, "punish": []}
    assert extract_json("no json here") is None
    assert extract_json('{"a": 1} trailing {"b": 2}') == {"a": 1}


def engine() -> Engine:
    config = GameConfig.model_validate(
        {"tokens": ["a", "b", "c"], "players": [{"name": "x"}, {"name": "y"}, {"name": "z"}], "seed": 5, "turns": 3}
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
    assert parse_action({"effort": 0.3, "punish": [{"target": other, "fish": 0}]}, e, 0).punish == []


def test_parse_action_rejections():
    e = engine()
    assert "effort" in parse_action({"effort": "lots"}, e, 0)
    assert "effort" in parse_action({"effort": 2.0}, e, 0)
    assert "target" in parse_action({"effort": 0.5, "punish": [{"target": e.pseudonyms[0]}]}, e, 0)
    assert "target" in parse_action({"effort": 0.5, "punish": [{"target": "Nobody"}]}, e, 0)
    assert "whole" in parse_action({"effort": 0.5, "punish": [{"target": e.pseudonyms[2], "fish": 1.5}]}, e, 0)
