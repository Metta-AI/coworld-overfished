"""Seat names and policy tags. Seats see names, never policy names or models.

Episode identity draws distinct first names per episode. Persistent identity derives a stable first name from
a hash of the policy's display name, so a policy keeps its name from one episode to the next while the name
itself says nothing about who wrote it or which model it runs. The replay carries a short hash of the display
name instead of the name itself, so a viewer cannot read a seat's role off its policy name.
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
    "Asha", "Bala", "Charu", "Dhruv", "Esha", "Girish", "Hema", "Ishan",
    "Jaya", "Kavi", "Leela", "Madhav", "Riya", "Omkar", "Pavan", "Rani",
)


def _digest(display_name: str) -> bytes:
    return hashlib.sha256(display_name.encode("utf-8")).digest()


def policy_tag(display_name: str) -> str:
    """Eight hex characters standing in for the policy's display name wherever a viewer could see it."""
    return _digest(display_name).hex()[:8]


def assign_pseudonyms(seed: int, count: int) -> list[str]:
    if count > len(PSEUDONYMS):
        raise ValueError(f"at most {len(PSEUDONYMS)} seats are supported")
    names = list(PSEUDONYMS)
    random.Random(f"names:{seed}").shuffle(names)
    return names[:count]


def persistent_pseudonym(display_name: str) -> str:
    return PSEUDONYMS[int.from_bytes(_digest(display_name)[:4], "big") % len(PSEUDONYMS)]


def assign_persistent_pseudonyms(display_names: list[str]) -> list[str]:
    """One stable name per policy, unique within the episode. When two seats hash to the same name (a
    duplicated filler seat, or a collision) the later seat takes its own stable second choice, then walks the
    list from there, so a displaced policy usually keeps one alternate name rather than a random one."""
    if len(display_names) > len(PSEUDONYMS):
        raise ValueError(f"at most {len(PSEUDONYMS)} seats are supported")
    taken: set[str] = set()
    names: list[str] = []
    for display in display_names:
        digest = _digest(display)
        index = int.from_bytes(digest[:4], "big") % len(PSEUDONYMS)
        if PSEUDONYMS[index] in taken:
            index = int.from_bytes(digest[4:8], "big") % len(PSEUDONYMS)
        while PSEUDONYMS[index] in taken:
            index = (index + 1) % len(PSEUDONYMS)
        taken.add(PSEUDONYMS[index])
        names.append(PSEUDONYMS[index])
    return names
