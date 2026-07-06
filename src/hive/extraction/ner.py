"""GLiNER2 zero-shot NER (fyp.txt L3).

Labels the entity types we care about with no fine-tuning: bank name,
account number, phone, Telegram ID, etc. Original contribution: financial
NER is absent from both reference frameworks.
"""

from __future__ import annotations

from hive.state import HVI

LABELS = ["bank name", "bank account number", "phone number", "telegram id", "person name"]


def extract_entities(text: str, source_msg_id: int) -> list[HVI]:
    """Run GLiNER2 over `text` and return typed HVIs.

    TODO(L3): load GLiNER2 model once at startup; map labels -> HVI.kind.
    """
    raise NotImplementedError
