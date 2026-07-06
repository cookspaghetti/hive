"""The four configurable personas (fyp.txt L2).

Each persona supplies a system-prompt fragment and pacing hints. Personas map
to the most common scam-target profiles found in the survey data.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    key: str
    label: str
    system_prompt: str


PERSONAS: dict[str, Persona] = {
    "confused_elderly": Persona(
        "confused_elderly",
        "Confused elderly person",
        "TODO: backstory, speech register, believable naivety.",
    ),
    "naive_young_adult": Persona(
        "naive_young_adult",
        "Naive young adult",
        "TODO",
    ),
    "overseas_worker": Persona(
        "overseas_worker",
        "Overseas Malaysian worker",
        "TODO",
    ),
    "small_business_owner": Persona(
        "small_business_owner",
        "Small business owner",
        "TODO",
    ),
}


def get_persona(key: str) -> Persona:
    return PERSONAS[key]
