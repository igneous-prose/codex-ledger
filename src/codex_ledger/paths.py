from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ARCHIVE_HOME = Path("~/AI-Usage-Archive/codex-ledger").expanduser()


def resolve_archive_home(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    configured = values.get("CODEX_LEDGER_HOME")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return DEFAULT_ARCHIVE_HOME.resolve(strict=False)


def archive_home_layout(home: Path) -> dict[str, Path]:
    return {
        "raw": home / "raw",
        "ledger": home / "ledger",
        "pricing": home / "pricing",
        "reports": home / "reports",
        "state": home / "state",
    }


def ensure_archive_home_layout(home: Path) -> dict[str, Path]:
    layout = archive_home_layout(home)
    home.mkdir(parents=True, exist_ok=True)
    for path in layout.values():
        path.mkdir(parents=True, exist_ok=True)
    return layout
