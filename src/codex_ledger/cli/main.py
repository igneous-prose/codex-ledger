from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from codex_ledger import __version__
from codex_ledger.ingest.service import (
    import_codex_json_report,
    summarize_doctor_status,
    sync_local_codex,
)
from codex_ledger.paths import ensure_archive_home_layout, resolve_archive_home
from codex_ledger.pricing.service import (
    build_pricing_coverage,
    format_pricing_coverage_table,
    format_pricing_recalc_table,
    recalculate_event_costs,
)
from codex_ledger.reconcile.service import format_reconcile_table, reconcile_reference
from codex_ledger.render.service import render_heatmap, render_workspace_html
from codex_ledger.reports.agents import (
    build_agent_report,
    explain_agent_run,
    format_agent_explain_table,
    format_agent_report_table,
)
from codex_ledger.reports.aggregate import (
    build_aggregate_report,
    format_aggregate_report_table,
)
from codex_ledger.reports.artifacts import write_report_artifact
from codex_ledger.reports.explain import (
    explain_day,
    explain_model,
    explain_workspace,
    format_explain_table,
)
from codex_ledger.reports.schema import ReportValidationError, load_report_file
from codex_ledger.reports.workspaces import (
    build_workspace_report,
    format_workspace_report_table,
)
from codex_ledger.storage.migrations import apply_migrations, default_database_path
from codex_ledger.verify.service import format_verify_table, verify_ledger, verify_reports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-ledger",
        description="Local-first, auditable usage ledger for Codex session artifacts.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser(
        "sync",
        help="Import local Codex rollout files into the canonical ledger.",
    )
    sync_parser.add_argument(
        "--full-backfill",
        action="store_true",
        help="Reprocess already archived raw files instead of skipping known hashes.",
    )
    sync_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    sync_parser.set_defaults(handler=run_sync)

    import_parser = subparsers.add_parser(
        "import",
        help="Import explicit files into the canonical ledger.",
    )
    import_subparsers = import_parser.add_subparsers(dest="import_command")
    import_codex_parser = import_subparsers.add_parser(
        "codex-json",
        help="Import an explicit Codex JSON report file.",
    )
    import_codex_parser.add_argument(
        "--input", type=Path, required=True, help="Path to the JSON file."
    )
    import_codex_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    import_codex_parser.set_defaults(handler=run_import_codex_json)

    price_parser = subparsers.add_parser(
        "price",
        help="Recalculate or inspect event-level pricing estimates.",
    )
    price_subparsers = price_parser.add_subparsers(dest="price_command")
    price_recalc_parser = price_subparsers.add_parser(
        "recalc",
        help="Recalculate deterministic event-level estimates for one pricing rule set.",
    )
    price_recalc_parser.add_argument(
        "--rule-set",
        required=True,
        help="Pricing rule set identifier.",
    )
    price_recalc_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    price_recalc_parser.set_defaults(handler=run_price_recalc)

    price_coverage_parser = price_subparsers.add_parser(
        "coverage",
        help="Inspect priced versus unpriced event coverage for one pricing rule set.",
    )
    price_coverage_parser.add_argument(
        "--rule-set",
        required=True,
        help="Pricing rule set identifier.",
    )
    price_coverage_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    price_coverage_parser.add_argument(
        "--redaction-mode",
        choices=("redacted", "alias", "full"),
        default="redacted",
        help="Workspace label representation.",
    )
    price_coverage_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    price_coverage_parser.set_defaults(handler=run_price_coverage)

    report_parser = subparsers.add_parser(
        "report",
        help="Read diagnostic summaries from the canonical ledger.",
    )
    report_subparsers = report_parser.add_subparsers(dest="report_command")
    report_aggregate_parser = report_subparsers.add_parser(
        "aggregate",
        help="Show aggregate usage totals for the selected period.",
    )
    report_aggregate_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
        required=True,
        help="UTC reporting window kind.",
    )
    report_aggregate_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_date,
        help="UTC report anchor date in YYYY-MM-DD format.",
    )
    report_aggregate_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    report_aggregate_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    report_aggregate_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    report_aggregate_parser.add_argument(
        "--output",
        type=Path,
        help="Write deterministic report JSON to this path.",
    )
    report_aggregate_parser.set_defaults(handler=run_report_aggregate)

    report_workspace_parser = report_subparsers.add_parser(
        "workspace",
        help="Show per-workspace usage totals for the selected period.",
    )
    report_workspace_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
        required=True,
        help="UTC reporting window kind.",
    )
    report_workspace_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_date,
        help="UTC report anchor date in YYYY-MM-DD format.",
    )
    report_workspace_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    report_workspace_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    report_workspace_parser.add_argument(
        "--redaction-mode",
        choices=("redacted", "alias", "full"),
        default="redacted",
        help="Workspace label representation.",
    )
    report_workspace_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    report_workspace_parser.add_argument(
        "--output",
        type=Path,
        help="Write deterministic report JSON to this path.",
    )
    report_workspace_parser.set_defaults(handler=run_report_workspace)

    report_agents_parser = report_subparsers.add_parser(
        "agents",
        help="Show agent and subagent token and lineage diagnostics.",
    )
    report_agents_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
        required=True,
        help="UTC reporting window kind.",
    )
    report_agents_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_date,
        help="UTC report anchor date in YYYY-MM-DD format.",
    )
    report_agents_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    report_agents_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    report_agents_parser.add_argument(
        "--redaction-mode",
        choices=("redacted", "alias", "full"),
        default="redacted",
        help="Workspace label representation.",
    )
    report_agents_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    report_agents_parser.add_argument(
        "--output",
        type=Path,
        help="Write deterministic report JSON to this path.",
    )
    report_agents_parser.set_defaults(handler=run_report_agents)

    render_parser = subparsers.add_parser(
        "render",
        help="Render saved report artifacts into static delivery formats.",
    )
    render_subparsers = render_parser.add_subparsers(dest="render_command")
    render_heatmap_parser = render_subparsers.add_parser(
        "heatmap",
        help="Render an aggregate report JSON artifact as a PNG heatmap.",
    )
    render_heatmap_parser.add_argument("--report", type=Path, required=True)
    render_heatmap_parser.add_argument("--output", type=Path, required=True)
    render_heatmap_parser.add_argument("--sidecar", type=Path)
    render_heatmap_parser.set_defaults(handler=run_render_heatmap)

    render_workspace_parser = render_subparsers.add_parser(
        "workspace-html",
        help="Render a workspace report JSON artifact as static HTML.",
    )
    render_workspace_parser.add_argument("--report", type=Path, required=True)
    render_workspace_parser.add_argument("--output", type=Path, required=True)
    render_workspace_parser.add_argument("--sidecar", type=Path)
    render_workspace_parser.set_defaults(handler=run_render_workspace_html)

    explain_parser = subparsers.add_parser(
        "explain",
        help="Explain canonical lineage and provenance records.",
    )
    explain_subparsers = explain_parser.add_subparsers(dest="explain_command")
    explain_day_parser = explain_subparsers.add_parser(
        "day",
        help="Trace one UTC day back to sessions, raw artifacts, and priced events.",
    )
    explain_day_parser.add_argument(
        "--date",
        required=True,
        type=_parse_date,
        help="UTC date in YYYY-MM-DD format.",
    )
    explain_day_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    explain_day_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    explain_day_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    explain_day_parser.set_defaults(handler=run_explain_day)

    explain_workspace_parser = explain_subparsers.add_parser(
        "workspace",
        help="Trace one workspace back to sessions, raw artifacts, and priced events.",
    )
    explain_workspace_parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace key to explain.",
    )
    explain_workspace_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
        required=True,
        help="UTC reporting window kind.",
    )
    explain_workspace_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_date,
        help="UTC report anchor date in YYYY-MM-DD format.",
    )
    explain_workspace_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    explain_workspace_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    explain_workspace_parser.add_argument(
        "--redaction-mode",
        choices=("redacted", "alias", "full"),
        default="redacted",
        help="Workspace label representation.",
    )
    explain_workspace_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    explain_workspace_parser.set_defaults(handler=run_explain_workspace)

    explain_model_parser = explain_subparsers.add_parser(
        "model",
        help="Trace one model back to sessions, raw artifacts, and priced events.",
    )
    explain_model_parser.add_argument(
        "--model",
        required=True,
        help="Observed model id to explain.",
    )
    explain_model_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
        required=True,
        help="UTC reporting window kind.",
    )
    explain_model_parser.add_argument(
        "--as-of",
        required=True,
        type=_parse_date,
        help="UTC report anchor date in YYYY-MM-DD format.",
    )
    explain_model_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    explain_model_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    explain_model_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    explain_model_parser.set_defaults(handler=run_explain_model)

    explain_agent_parser = explain_subparsers.add_parser(
        "agent",
        help="Trace one agent run back to its canonical provenance.",
    )
    explain_agent_parser.add_argument(
        "--agent-run",
        required=True,
        help="Agent run key to explain.",
    )
    explain_agent_parser.add_argument(
        "--rule-set",
        help="Pricing rule set identifier. Defaults to the latest stable local rule set.",
    )
    explain_agent_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
        help="Output format.",
    )
    explain_agent_parser.add_argument(
        "--redaction-mode",
        choices=("redacted", "alias", "full"),
        default="redacted",
        help="Workspace label representation.",
    )
    explain_agent_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    explain_agent_parser.set_defaults(handler=run_explain_agent)

    verify_parser = subparsers.add_parser(
        "verify",
        help="Run read-only consistency checks against the ledger and report layer.",
    )
    verify_subparsers = verify_parser.add_subparsers(dest="verify_command")
    verify_ledger_parser = verify_subparsers.add_parser(
        "ledger",
        help="Verify ledger and pricing invariants.",
    )
    verify_ledger_parser.add_argument("--archive-home", type=Path)
    verify_ledger_parser.add_argument("--json", action="store_true", dest="as_json")
    verify_ledger_parser.set_defaults(handler=run_verify_ledger)

    verify_reports_parser = verify_subparsers.add_parser(
        "reports",
        help="Verify derived report totals against the ledger and cost estimates.",
    )
    verify_reports_parser.add_argument("--rule-set")
    verify_reports_parser.add_argument("--archive-home", type=Path)
    verify_reports_parser.add_argument("--json", action="store_true", dest="as_json")
    verify_reports_parser.set_defaults(handler=run_verify_reports)

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        help="Compare a reference summary against the current derived ledger totals.",
    )
    reconcile_subparsers = reconcile_parser.add_subparsers(dest="reconcile_command")
    reconcile_reference_parser = reconcile_subparsers.add_parser(
        "reference",
        help="Compare a reference JSON summary against current derived aggregate totals.",
    )
    reconcile_reference_parser.add_argument("--input", type=Path, required=True)
    reconcile_reference_parser.add_argument(
        "--period",
        choices=("day", "week", "month", "year"),
    )
    reconcile_reference_parser.add_argument("--as-of", type=_parse_date)
    reconcile_reference_parser.add_argument(
        "--format",
        choices=("json", "table"),
        default="table",
    )
    reconcile_reference_parser.add_argument("--archive-home", type=Path)
    reconcile_reference_parser.set_defaults(handler=run_reconcile_reference)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Inspect local archive-home and expected discovery paths.",
    )
    doctor_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
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
    migrate_parser.add_argument(
        "--archive-home",
        type=Path,
        help="Override the archive home directory for this run.",
    )
    migrate_parser.set_defaults(handler=run_migrate)

    return parser


def run_sync(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    summary, outcomes = sync_local_codex(
        archive_home=archive_home,
        full_backfill=bool(args.full_backfill),
    )
    print(f"Batch: {summary.batch_id}")
    print(f"Manifest: {summary.manifest_relpath}")
    print(f"Scanned files: {summary.scanned_file_count}")
    print(f"Imported files: {summary.imported_file_count}")
    print(f"Skipped files: {summary.skipped_file_count}")
    print(f"Failed files: {summary.failed_file_count}")
    for outcome in outcomes:
        print(f"{outcome.status}: {outcome.source_path}")
    return 0 if summary.failed_file_count == 0 else 1


def run_import_codex_json(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    summary, outcomes = import_codex_json_report(
        archive_home=archive_home,
        input_path=args.input,
    )
    print(f"Batch: {summary.batch_id}")
    for outcome in outcomes:
        print(f"{outcome.status}: {outcome.source_path}")
        if outcome.detail:
            print(f"detail: {outcome.detail}")
    return 0 if summary.failed_file_count == 0 else 1


def run_price_recalc(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = recalculate_event_costs(
        archive_home=archive_home,
        rule_set_id=args.rule_set,
    )
    print(format_pricing_recalc_table(payload))
    return 0


def run_price_coverage(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = build_pricing_coverage(
        archive_home=archive_home,
        rule_set_id=args.rule_set,
        redaction_mode=args.redaction_mode,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_pricing_coverage_table(payload))
    return 0


def run_doctor(args: argparse.Namespace) -> int:
    home = _resolve_archive_home_argument(args.archive_home)
    payload = summarize_doctor_status(home)

    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"Archive home: {payload['archive_home']}")
    print(f"Database path: {payload['database_path']}")
    print(f"History persistence: {payload['history_persistence_status']}")
    for name, path in payload["expected_layout"].items():
        print(f"{name}: {path}")
    for source in payload["source_roots"]:
        print(
            "source: "
            f"{source['path']} "
            f"(exists={source['exists']}, jsonl_count={source['jsonl_count']})"
        )
    applied = ", ".join(payload["migration_status"]["applied"]) or "none"
    pending = ", ".join(payload["migration_status"]["pending"]) or "none"
    print(f"Applied migrations: {applied}")
    print(f"Pending migrations: {pending}")
    return 0


def run_report_aggregate(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = build_aggregate_report(
        archive_home=archive_home,
        period=args.period,
        as_of=args.as_of,
        rule_set_id=args.rule_set,
    )
    _maybe_write_report_output(payload, args.output)
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_aggregate_report_table(payload))
    return 0


def run_report_workspace(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = build_workspace_report(
        archive_home=archive_home,
        period=args.period,
        as_of=args.as_of,
        rule_set_id=args.rule_set,
        redaction_mode=args.redaction_mode,
    )
    _maybe_write_report_output(payload, args.output)
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_workspace_report_table(payload))
    return 0


def run_report_agents(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = build_agent_report(
        archive_home=archive_home,
        period=args.period,
        as_of=args.as_of,
        rule_set_id=args.rule_set,
        redaction_mode=args.redaction_mode,
    )
    _maybe_write_report_output(payload, args.output)
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_agent_report_table(payload))
    return 0


def run_explain_day(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = explain_day(
        archive_home=archive_home,
        day=args.date,
        rule_set_id=args.rule_set,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_explain_table(payload))
    return 0


def run_explain_workspace(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = explain_workspace(
        archive_home=archive_home,
        workspace_key=args.workspace,
        period=args.period,
        as_of=args.as_of,
        rule_set_id=args.rule_set,
        redaction_mode=args.redaction_mode,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_explain_table(payload))
    return 0


def run_explain_model(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = explain_model(
        archive_home=archive_home,
        model_id=args.model,
        period=args.period,
        as_of=args.as_of,
        rule_set_id=args.rule_set,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_explain_table(payload))
    return 0


def run_explain_agent(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = explain_agent_run(
        archive_home=archive_home,
        agent_run_key=args.agent_run,
        rule_set_id=args.rule_set,
        redaction_mode=args.redaction_mode,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_agent_explain_table(payload))
    return 0


def run_render_heatmap(args: argparse.Namespace) -> int:
    load_report_file(args.report)
    result = render_heatmap(
        report_path=args.report,
        output_path=args.output,
        sidecar_path=args.sidecar,
    )
    print(f"Rendered heatmap: {result['output_path']}")
    print(f"Sidecar: {result['sidecar_path']}")
    return 0


def run_render_workspace_html(args: argparse.Namespace) -> int:
    load_report_file(args.report)
    result = render_workspace_html(
        report_path=args.report,
        output_path=args.output,
        sidecar_path=args.sidecar,
    )
    print(f"Rendered workspace HTML: {result['output_path']}")
    print(f"Sidecar: {result['sidecar_path']}")
    return 0


def run_verify_ledger(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = verify_ledger(archive_home)
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_verify_table(payload, label="ledger"))
    return 0 if payload["ok"] else 1


def run_verify_reports(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = verify_reports(
        archive_home=archive_home,
        rule_set_id=args.rule_set,
    )
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_verify_table(payload, label="reports"))
    return 0 if payload["ok"] else 1


def run_reconcile_reference(args: argparse.Namespace) -> int:
    archive_home = _resolve_archive_home_argument(args.archive_home)
    payload = reconcile_reference(
        archive_home=archive_home,
        input_path=args.input,
        period=args.period,
        as_of=args.as_of,
    )
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(format_reconcile_table(payload))
    return 0 if payload["ok"] else 1


def run_migrate(args: argparse.Namespace) -> int:
    if args.database is not None:
        database_path = args.database.expanduser().resolve(strict=False)
        database_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        archive_home = _resolve_archive_home_argument(args.archive_home)
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


def _resolve_archive_home_argument(archive_home: Path | None) -> Path:
    if archive_home is not None:
        return archive_home.expanduser().resolve(strict=False)
    return resolve_archive_home()


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _maybe_write_report_output(payload: dict[str, object], output_path: Path | None) -> None:
    if output_path is None:
        return
    write_report_artifact(payload, output_path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0
    try:
        return int(handler(args))
    except (ReportValidationError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
