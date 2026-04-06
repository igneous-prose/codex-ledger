from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from codex_ledger import __version__
from codex_ledger.paths import archive_home_layout, ensure_archive_home_layout, resolve_archive_home
from codex_ledger.storage.migrations import apply_migrations, default_database_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-ledger",
        description="Local-first, auditable usage ledger for Codex session artifacts.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect local archive-home and expected discovery paths.",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON.",
    )
    doctor_parser.set_defaults(handler=run_doctor)

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="Create archive-home directories and apply bundled SQL migrations.",
    )
    migrate_parser.add_argument(
        "--database",
        type=Path,
        help="Override the SQLite database path.",
    )
    migrate_parser.set_defaults(handler=run_migrate)

    return parser


def run_doctor(args: argparse.Namespace) -> int:
    home = resolve_archive_home()
    layout = archive_home_layout(home)
    expected_layout = {name: str(path) for name, path in layout.items()}
    source_roots = [
        {
            "path": str(Path("~/.codex/sessions").expanduser()),
            "exists": Path("~/.codex/sessions").expanduser().exists(),
        },
        {
            "path": str(Path("~/.codex/archived_sessions").expanduser()),
            "exists": Path("~/.codex/archived_sessions").expanduser().exists(),
        },
    ]
    payload = {
        "archive_home": str(home),
        "archive_home_exists": home.exists(),
        "database_path": str(default_database_path(home)),
        "expected_layout": expected_layout,
        "source_roots": source_roots,
    }

    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"Archive home: {payload['archive_home']}")
    print(f"Archive home exists: {payload['archive_home_exists']}")
    print(f"Database path: {payload['database_path']}")
    for name, path in expected_layout.items():
        print(f"{name}: {path}")
    for source in source_roots:
        print(f"source: {source['path']} (exists={source['exists']})")
    return 0


def run_migrate(args: argparse.Namespace) -> int:
    if args.database is not None:
        database_path = args.database.expanduser().resolve(strict=False)
        database_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        archive_home = resolve_archive_home()
        ensure_archive_home_layout(archive_home)
        database_path = default_database_path(archive_home)

    applied = apply_migrations(database_path)
    if applied:
        print(f"Applied migrations to {database_path}:")
        for name in applied:
            print(f"- {name}")
    else:
        print(f"No pending migrations for {database_path}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    return int(handler(args))
