"""Soul files: the only thing a player uploads.

Line 1 names the model, prefixed with `#!`, either as a short alias (`#!opus`) or a canonical OpenRouter
slug (`#!anthropic/claude-opus-5`). `#!scripted/<name>` seats a no-model baseline. Everything after
line 1 is the seat's philosophy and is prepended to its system prompt verbatim.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

SOUL_MAX_BYTES = 32_768
SCRIPTED_PREFIX = "scripted/"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$")
_ALIAS = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class SoulError(ValueError):
    """The uploaded file cannot seat a player. Terminal for that seat."""


class Soul(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    text: str

    @property
    def scripted(self) -> bool:
        return self.model.startswith(SCRIPTED_PREFIX)

    @property
    def scripted_name(self) -> str:
        return self.model[len(SCRIPTED_PREFIX) :]


def parse_soul(data: bytes, aliases: dict[str, str], scripted_names: set[str]) -> Soul:
    if len(data) == 0:
        raise SoulError("soul file is empty")
    if len(data) > SOUL_MAX_BYTES:
        raise SoulError(f"soul file is {len(data)} bytes; the cap is {SOUL_MAX_BYTES}")
    if b"\x00" in data:
        raise SoulError("soul file contains a NUL byte")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SoulError(f"soul file is not UTF-8: {error}") from None
    first, _, rest = text.lstrip("﻿").partition("\n")
    first = first.strip()
    if not first.startswith("#!"):
        raise SoulError("line 1 must start with `#!` followed by a model alias, an OpenRouter slug, or scripted/<name>")
    requested = first[2:].strip().lower()
    if requested.startswith(SCRIPTED_PREFIX):
        name = requested[len(SCRIPTED_PREFIX) :]
        if name not in scripted_names:
            raise SoulError(f"unknown scripted policy {name!r}; known: {', '.join(sorted(scripted_names))}")
        return Soul(model=requested, text=rest.strip())
    if requested in aliases:
        return Soul(model=aliases[requested], text=rest.strip())
    if _SLUG.match(requested):
        return Soul(model=requested, text=rest.strip())
    if _ALIAS.match(requested):
        raise SoulError(f"unknown model alias {requested!r}; known aliases: {', '.join(sorted(aliases))}")
    raise SoulError(f"line 1 model {requested!r} is neither an alias nor a canonical vendor/model slug")
