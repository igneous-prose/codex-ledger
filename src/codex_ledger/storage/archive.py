from __future__ import annotations

import os
from pathlib import Path

from codex_ledger.utils.hashing import sha256_file


def stored_raw_relpath(provider: str, source_kind: str, content_hash: str, suffix: str) -> str:
    extension = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ""
    return f"{provider}/{source_kind}/{content_hash[:2]}/{content_hash}{extension}"


def archive_raw_file(
    archive_raw_root: Path,
    source_path: Path,
    provider: str,
    source_kind: str,
) -> tuple[str, str, int]:
    content_hash = sha256_file(source_path)
    size_bytes = source_path.stat().st_size
    stored_relpath = stored_raw_relpath(provider, source_kind, content_hash, source_path.suffix)
    target_path = archive_raw_root / stored_relpath
    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_path.read_bytes())
        target_path.chmod(0o444)
    else:
        target_size = target_path.stat().st_size
        if target_size != size_bytes:
            raise ValueError(f"Archived file size mismatch for {target_path}")
        os.chmod(target_path, 0o444)
    return content_hash, stored_relpath, size_bytes
