"""Per-episode pseudonyms. Seats see these names, never policy names or models."""

from __future__ import annotations

import random

# Sixteen short names drawn from lake, lotus, and river vocabulary so they read well in dialogue.
PSEUDONYMS: tuple[str, ...] = (
    "Kamal",
    "Neela",
    "Hansa",
    "Meena",
    "Padma",
    "Tara",
    "Ravi",
    "Sarasi",
    "Jalaj",
    "Indu",
    "Kairav",
    "Manasa",
    "Nalini",
    "Ambu",
    "Pushkar",
    "Varsha",
)


def assign_pseudonyms(seed: int, count: int) -> list[str]:
    if count > len(PSEUDONYMS):
        raise ValueError(f"at most {len(PSEUDONYMS)} seats are supported")
    names = list(PSEUDONYMS)
    random.Random(f"names:{seed}").shuffle(names)
    return names[:count]
