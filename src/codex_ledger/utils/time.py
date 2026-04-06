from __future__ import annotations

from datetime import UTC, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def normalize_timestamp(value: str | None) -> str | None:
    if value is None or value == "":
        return None

    candidate = value
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"

    parsed = datetime.fromisoformat(candidate)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")
