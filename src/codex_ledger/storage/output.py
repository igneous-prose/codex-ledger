from __future__ import annotations

import os
from pathlib import Path

TRUSTED_OUTPUT_SYMLINK_ALIASES = {
    Path("/tmp"): Path("/private/tmp"),
    Path("/var"): Path("/private/var"),
}


def prepare_output_path(output_path: Path) -> Path:
    target = _normalized_output_path(output_path)
    _assert_no_untrusted_symlink_path_prefix(target.parent, label="output path ancestor")
    target.parent.mkdir(parents=True, exist_ok=True)
    _assert_no_untrusted_symlink_path_prefix(target.parent, label="output path ancestor")
    if target.is_symlink():
        raise ValueError(f"Refusing to write output through symlink: {target}")
    return target


def write_bytes_output(output_path: Path, data: bytes) -> Path:
    target = prepare_output_path(output_path)
    _write_bytes_no_follow(target, data)
    return target


def write_text_output(output_path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    return write_bytes_output(output_path, text.encode(encoding))


def _normalized_output_path(output_path: Path) -> Path:
    expanded = output_path.expanduser()
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return Path(os.path.normpath(absolute))


def _assert_no_untrusted_symlink_path_prefix(path: Path, *, label: str) -> None:
    current = path.anchor and Path(path.anchor) or Path(".")
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.is_symlink():
            if _is_trusted_output_symlink_alias(current):
                continue
            raise ValueError(f"Refusing to use symlinked {label}: {current}")


def _write_bytes_no_follow(target: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    file_descriptor = os.open(target, flags | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            file_descriptor = -1
            handle.write(data)
    finally:
        if file_descriptor != -1:
            os.close(file_descriptor)


def _is_trusted_output_symlink_alias(path: Path) -> bool:
    expected_target = TRUSTED_OUTPUT_SYMLINK_ALIASES.get(path)
    if expected_target is None:
        return False
    return path.resolve(strict=True) == expected_target
