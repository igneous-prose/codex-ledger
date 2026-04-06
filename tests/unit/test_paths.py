from __future__ import annotations

from pathlib import Path

from codex_ledger.paths import DEFAULT_ARCHIVE_HOME, archive_home_layout, resolve_archive_home


def test_resolve_archive_home_uses_default_when_env_missing() -> None:
    assert resolve_archive_home({}) == DEFAULT_ARCHIVE_HOME.resolve(strict=False)


def test_resolve_archive_home_honors_env_override(tmp_path: Path) -> None:
    target = tmp_path / "custom-home"
    assert resolve_archive_home({"CODEX_LEDGER_HOME": str(target)}) == target.resolve(strict=False)


def test_archive_home_layout_uses_expected_directories(tmp_path: Path) -> None:
    layout = archive_home_layout(tmp_path)
    assert layout == {
        "raw": tmp_path / "raw",
        "ledger": tmp_path / "ledger",
        "pricing": tmp_path / "pricing",
        "reports": tmp_path / "reports",
        "state": tmp_path / "state",
    }
