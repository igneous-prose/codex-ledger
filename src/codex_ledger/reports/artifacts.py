from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_ledger.reports.schema import stable_report_json


def write_report_artifact(payload: dict[str, Any], output_path: Path) -> Path:
    target = output_path.expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(stable_report_json(payload), encoding="utf-8")
    return target
