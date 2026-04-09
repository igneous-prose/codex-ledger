from __future__ import annotations

from pathlib import Path
from typing import Any

from codex_ledger.reports.schema import stable_report_json
from codex_ledger.storage.output import write_text_output


def write_report_artifact(payload: dict[str, Any], output_path: Path) -> Path:
    return write_text_output(output_path, stable_report_json(payload))
