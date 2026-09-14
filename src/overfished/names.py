"""Seat names. Seats see these, never policy names or models.

Episode identity draws distinct first names per episode. Persistent identity derives a stable
"First Surname" from the policy's display name, so a policy keeps its name from one episode to the
next while the name itself says nothing about who wrote it or which model it runs.
"""

from __future__ import annotations

import hashlib
import random

# Short first names drawn from lake, lotus, and river vocabulary so they read well in dialogue.
PSEUDONYMS: tuple[str, ...] = (
    "Kamal", "Neela", "Hansa", "Meena", "Padma", "Tara", "Ravi", "Sarasi",
    "Jalaj", "Indu", "Kairav", "Manasa", "Nalini", "Ambu", "Pushkar", "Varsha",
    "Arun", "Bindu", "Chand", "Devika", "Gauri", "Hari", "Ila", "Kiran",
    "Lata", "Mohan", "Nanda", "Prem", "Rohan", "Sita", "Uma", "Vikram",
)

SURNAMES: tuple[str, ...] = (
    "Reedwater", "Lotusbank", "Ferncove", "Mistmere", "Stillpool", "Gladeshore",
    "Dawnbay", "Moonshoal", "Cranewing", "Silverfin", "Hillrun", "Palmshade",
)


def assign_pseudonyms(seed: int, count: int) -> list[str]:
    if count > len(PSEUDONYMS):
        raise ValueError(f"at most {len(PSEUDONYMS)} seats are supported")
    names = list(PSEUDONYMS)
    random.Random(f"names:{seed}").shuffle(names)
    return names[:count]


def persistent_pseudonym(display_name: str) -> str:
    digest = hashlib.sha256(display_name.encode("utf-8")).digest()
    first = PSEUDONYMS[digest[0] % len(PSEUDONYMS)]
    surname = SURNAMES[digest[1] % len(SURNAMES)]
    return f"{first} {surname}"


def assign_persistent_pseudonyms(display_names: list[str]) -> list[str]:
    """One stable name per policy; a clash inside one episode (a duplicated filler seat, or a hash
    collision) moves the later seat along the surname list so names stay unique within the episode."""
    taken: set[str] = set()
    names: list[str] = []
    for display in display_names:
        name = persistent_pseudonym(display)
        first, surname = name.split(" ", 1)
        index = SURNAMES.index(surname)
        while name in taken:
            index = (index + 1) % len(SURNAMES)
            name = f"{first} {SURNAMES[index]}"
        taken.add(name)
        names.append(name)
    return names
