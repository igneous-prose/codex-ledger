from __future__ import annotations

from pathlib import Path

from codex_ledger.domain.records import ImportCandidate


def default_codex_source_roots() -> tuple[Path, Path]:
    return (
        Path("~/.codex/sessions").expanduser(),
        Path("~/.codex/archived_sessions").expanduser(),
    )


def discover_local_rollout_files() -> list[ImportCandidate]:
    candidates: list[ImportCandidate] = []
    for root in default_codex_source_roots():
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.jsonl")):
            if path.is_file():
                candidates.append(
                    ImportCandidate(source_path=path, source_kind="local_rollout_file")
                )
    return candidates
