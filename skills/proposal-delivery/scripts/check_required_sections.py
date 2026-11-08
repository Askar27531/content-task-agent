"""Pure deliverable validation; no model requests, database writes, or shell execution."""
from __future__ import annotations

REQUIRED = ("title", "objective", "audience", "strategy", "content", "schedule", "budget", "source_ids")


def validate_payload(payload: dict, known_source_ids: set[str]) -> dict:
    missing = [key for key in REQUIRED if key not in payload or payload[key] is None or payload[key] == "" or payload[key] == []]
    source_ids = payload.get("source_ids", [])
    if not isinstance(source_ids, list):
        return {"is_valid": False, "missing_fields": missing, "invalid_references": ["source_ids 必须是列表"]}
    invalid = [str(source) for source in source_ids if source not in known_source_ids]
    return {"is_valid": not missing and not invalid, "missing_fields": missing, "invalid_references": invalid}
