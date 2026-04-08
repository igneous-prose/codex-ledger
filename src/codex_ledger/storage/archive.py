from __future__ import annotations

import os
from pathlib import Path

from codex_ledger.utils.hashing import sha256_file

MAX_ARCHIVE_COPY_BYTES = 64 * 1024 * 1024


def stored_raw_relpath(provider: str, source_kind: str, content_hash: str, suffix: str) -> str:
    extension = suffix if suffix.startswith(".") else f".{suffix}" if suffix else ""
    return f"{provider}/{source_kind}/{content_hash[:2]}/{content_hash}{extension}"


def archive_raw_file(
    archive_raw_root: Path,
    source_path: Path,
    provider: str,
    source_kind: str,
) -> tuple[str, str, int]:
    size_bytes = source_path.stat().st_size
    if size_bytes > MAX_ARCHIVE_COPY_BYTES:
        raise ValueError(
            "source file exceeds configured limit "
            f"({size_bytes} bytes > {MAX_ARCHIVE_COPY_BYTES} bytes)"
        )
    content_hash = sha256_file(source_path)
    stored_relpath = stored_raw_relpath(provider, source_kind, content_hash, source_path.suffix)
    archive_root_input = archive_raw_root.expanduser()
    if archive_root_input.is_symlink():
        raise ValueError(f"Refusing to use symlinked archive root: {archive_root_input}")
    archive_root = archive_root_input.resolve(strict=False)
    target_path = archive_root / stored_relpath
    _assert_no_symlink_components(archive_root, target_path.parent)
    if target_path.is_symlink():
        raise ValueError(f"Refusing to write archived file through symlink: {target_path}")
    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlink_components(archive_root, target_path.parent)
        _stream_copy_no_follow(source_path, target_path)
        target_path.chmod(0o444)
    else:
        target_size = target_path.stat().st_size
        if target_size != size_bytes:
            raise ValueError(f"Archived file size mismatch for {target_path}")
        os.chmod(target_path, 0o444)
    return content_hash, stored_relpath, size_bytes


def _assert_no_symlink_components(archive_root: Path, target_dir: Path) -> None:
    if archive_root.is_symlink():
        raise ValueError(f"Refusing to use symlinked archive root: {archive_root}")
    current = archive_root
    for part in target_dir.relative_to(archive_root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing to use symlinked archive path: {current}")


def _stream_copy_no_follow(source_path: Path, target_path: Path) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    file_descriptor = os.open(target_path, flags | getattr(os, "O_NOFOLLOW", 0), 0o444)
    try:
        with source_path.open("rb") as source_handle, os.fdopen(file_descriptor, "wb") as target:
            file_descriptor = -1
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                target.write(chunk)
    finally:
        if file_descriptor != -1:
            os.close(file_descriptor)
