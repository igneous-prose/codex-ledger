from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from codex_ledger.domain.records import ImportCandidate
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.pricing.service import recalculate_event_costs
from codex_ledger.reconcile.service import reconcile_reference
from codex_ledger.render.service import render_heatmap, render_workspace_html
from codex_ledger.reports.aggregate import build_aggregate_report
from codex_ledger.reports.artifacts import write_report_artifact
from codex_ledger.reports.schema import ReportValidationError, load_report_file, stable_report_json
from codex_ledger.reports.workspaces import build_workspace_report
from codex_ledger.verify.service import verify_ledger, verify_reports
from tests.test_support import fixture_path, open_database

RULE_SET_ID = "reference_usd_openai_standard_2026_04_07"


def test_report_artifact_writing_is_deterministic(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    payload = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
    )

    first = write_report_artifact(payload, tmp_path / "first.json")
    second = write_report_artifact(payload, tmp_path / "second.json")

    assert first.read_text(encoding="utf-8") == stable_report_json(payload)
    assert first.read_bytes() == second.read_bytes()
    assert load_report_file(first) == payload


def test_report_schema_validation_rejects_invalid_saved_json(tmp_path: Path) -> None:
    report_path = tmp_path / "invalid-report.json"
    report_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-07T00:00:00Z",
                "generator_version": "0.1.0a0",
                "filters": {},
                "timezone": "UTC",
                "pricing": {},
                "data": {},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ReportValidationError, match="schema_version"):
        load_report_file(report_path)


def test_heatmap_render_is_deterministic_and_tracks_provenance(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    payload = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
    )
    report_path = write_report_artifact(payload, tmp_path / "aggregate.json")

    first = render_heatmap(
        report_path=report_path,
        output_path=tmp_path / "heatmap-one.png",
        sidecar_path=tmp_path / "heatmap-one.sidecar.json",
    )
    second = render_heatmap(
        report_path=report_path,
        output_path=tmp_path / "heatmap-two.png",
        sidecar_path=tmp_path / "heatmap-two.sidecar.json",
    )

    first_png = Path(first["output_path"])
    second_png = Path(second["output_path"])
    assert first_png.read_bytes() == second_png.read_bytes()

    sidecar = json.loads(Path(first["sidecar_path"]).read_text(encoding="utf-8"))
    assert sidecar["renderer_kind"] == "heatmap"
    assert sidecar["source_report_name"] == "aggregate.json"
    assert sidecar["source_report_schema_version"] == "phase4-aggregate-report-v1"
    assert sidecar["selected_pricing_rule_set_id"] == RULE_SET_ID
    assert sidecar["pricing_coverage_status"] == "full"
    assert sidecar["pricing_priced_token_total"] == 14
    assert sidecar["pricing_unpriced_token_total"] == 0
    assert str(report_path) not in json.dumps(sidecar, sort_keys=True)


def test_workspace_html_render_preserves_redacted_privacy_defaults(tmp_path: Path) -> None:
    archive_home, workspace_root, nested = _import_absolute_workspace_snapshot(tmp_path)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    payload = build_workspace_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 21),
    )
    report_path = write_report_artifact(payload, tmp_path / "workspace.json")

    result = render_workspace_html(
        report_path=report_path,
        output_path=tmp_path / "workspace.html",
        sidecar_path=tmp_path / "workspace.sidecar.json",
    )

    html_output = Path(result["output_path"]).read_text(encoding="utf-8")
    sidecar_output = Path(result["sidecar_path"]).read_text(encoding="utf-8")
    assert str(workspace_root) not in html_output
    assert str(nested) not in html_output
    assert str(workspace_root) not in sidecar_output
    assert str(nested) not in sidecar_output
    sidecar = json.loads(sidecar_output)
    assert sidecar["renderer_kind"] == "workspace_html"
    assert sidecar["redaction_mode"] == "redacted"
    assert sidecar["source_report_name"] == "workspace.json"


def test_verify_services_pass_on_clean_snapshot(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    assert verify_ledger(archive_home)["ok"] is True
    assert verify_reports(archive_home)["ok"] is True


def test_verify_services_catch_intentional_pricing_mismatch(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        connection.execute(
            """
            UPDATE cost_estimates
            SET amount = NULL
            WHERE rule_set_id = ?
              AND estimate_status = 'priced'
            """,
            (RULE_SET_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    assert verify_ledger(archive_home)["ok"] is False
    assert verify_reports(archive_home)["ok"] is False


def test_reconcile_reference_surfaces_diffs(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    current = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
    )
    reference = json.loads(json.dumps(current))
    reference["data"]["selected_period_totals"]["total_tokens"] = 999
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(
        json.dumps(reference, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    payload = reconcile_reference(
        archive_home=archive_home,
        input_path=reference_path,
    )

    assert payload["ok"] is False
    assert payload["diffs"] == [
        {
            "field": "total_tokens",
            "reference": 999,
            "current": 14,
        }
    ]


def _import_fixture_batch(archive_home: Path, fixture_names: tuple[str, ...]) -> Path:
    grouped: dict[str, list[ImportCandidate]] = {
        "local_rollout_file": [],
        "imported_json_report": [],
    }
    for name in fixture_names:
        source_kind = "imported_json_report" if name.endswith(".json") else "local_rollout_file"
        grouped[source_kind].append(ImportCandidate(fixture_path(name), source_kind))

    for source_kind, candidates in grouped.items():
        if not candidates:
            continue
        run_import_batch(
            archive_home=archive_home,
            candidates=tuple(candidates),
            provider="codex",
            host="imported_json" if source_kind == "imported_json_report" else "standalone_cli",
            source_kind=source_kind,
            full_backfill=False,
        )
    return archive_home


def _import_absolute_workspace_snapshot(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive_home = tmp_path / "archive"
    workspace_root = tmp_path / "private-workspace"
    nested = workspace_root / "app"
    nested.mkdir(parents=True)
    (workspace_root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    rollout = tmp_path / "absolute-rollout.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-21T09:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "private-session",
                            "timestamp": "2026-04-21T08:59:55Z",
                            "cwd": str(workspace_root),
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-21T09:00:01Z",
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "private-turn",
                            "cwd": str(nested),
                            "model": "gpt-5.4",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-21T09:00:02Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 3,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 1,
                                    "reasoning_output_tokens": 1,
                                    "total_tokens": 4,
                                }
                            },
                        },
                    },
                    sort_keys=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    run_import_batch(
        archive_home=archive_home,
        candidates=(ImportCandidate(rollout, "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )
    return archive_home, workspace_root, nested
