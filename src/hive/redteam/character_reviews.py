"""Append-only operator assessments, separate from read-only evaluation artifacts."""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.redteam.character import (
    RUBRIC_VERSION,
    empty_assessment,
    response_turns,
    validate_turn_scores,
)


class ReviewConflict(ValueError):
    """The transcript or current review changed while the form was open."""


def assessment_for_record(record: dict[str, Any]) -> dict[str, Any]:
    target = record.get(
        "target_response_turns", (record.get("run_metadata") or {}).get("max_turns")
    )
    target = target if type(target) is int and target > 0 else None
    transcript = record.get("transcript") or []
    result = empty_assessment(
        str(record.get("persona") or ""),
        transcript,
        target,
        record.get("persona_prompt"),
    )
    existing = record.get("character_assessment")
    if not isinstance(existing, dict):
        return result
    if (
        existing.get("source_sha256") != result["source_sha256"]
        or existing.get("rubric_version") != RUBRIC_VERSION
    ):
        return {**result, "status": "error", "error": "Assessment does not match this transcript"}
    if existing.get("status") == "automated":
        try:
            scores = validate_turn_scores(existing.get("turns"), response_turns(transcript))
        except ValueError:
            return {**result, "status": "error", "error": "Stored assessment is invalid"}
        return {**existing, **result, "status": "automated", "turns": scores}
    return {
        **result,
        "status": existing.get("status", "not_assessed"),
        "error": existing.get("error"),
    }


class CharacterReviewStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()

    def history(self, run_id: str) -> list[dict[str, Any]]:
        if len(run_id) != 16 or any(c not in "0123456789abcdef" for c in run_id):
            raise ValueError("Invalid evaluation ID")
        reviews = []
        for path in (self.root / run_id).glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("Invalid saved character review")
            reviews.append(value)
        return sorted(reviews, key=lambda item: (item["reviewed_utc"], item["id"]))

    def save(self, run_id: str, record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        base = assessment_for_record(record)
        if payload.get("source_sha256") != base["source_sha256"]:
            raise ReviewConflict("Transcript changed; reopen the evaluation before reviewing")
        reviewer, note = payload.get("reviewer"), payload.get("note")
        if not isinstance(reviewer, str) or not 2 <= len(reviewer.strip()) <= 120:
            raise ValueError("Enter the reviewer's name (2–120 characters)")
        if not isinstance(note, str) or not 3 <= len(note.strip()) <= 2000:
            raise ValueError("Enter a review note (3–2000 characters)")
        turns = response_turns(record.get("transcript") or [])
        if not turns:
            raise ValueError("No delivered HIVE responses to review")
        scores = validate_turn_scores(payload.get("turns"), turns)
        with self._lock:
            history = self.history(run_id)
            previous_id = history[-1]["id"] if history else None
            if payload.get("previous_review_id") != previous_id:
                raise ReviewConflict("Another review was saved; reopen this evaluation")
            review = {
                "id": uuid.uuid4().hex,
                "run_id": run_id,
                "previous_review_id": previous_id,
                "reviewer": reviewer.strip(),
                "note": note.strip(),
                "reviewed_utc": datetime.now(UTC).isoformat(),
                "assessment": {**base, "status": "human_reviewed", "turns": scores, "error": None},
            }
            directory = self.root / run_id
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / f"{review['id']}.tmp"
            try:
                with temporary.open("x", encoding="utf-8") as handle:
                    json.dump(review, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.replace(directory / f"{review['id']}.json")
            finally:
                temporary.unlink(missing_ok=True)
            return review
