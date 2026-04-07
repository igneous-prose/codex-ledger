from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from codex_ledger.domain.records import ImportCandidate
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.pricing.service import recalculate_event_costs
from codex_ledger.reports.agents import build_agent_report, explain_agent_run
from codex_ledger.reports.aggregate import build_aggregate_report
from codex_ledger.reports.explain import explain_day, explain_model, explain_workspace
from codex_ledger.reports.workspaces import build_workspace_report
from codex_ledger.storage.repository import upsert_workspace_alias
from tests.test_support import fixture_path, open_database

RULE_SET_ID = "reference_usd_openai_standard_2026_04_07"


def test_aggregate_report_can_omit_cost_when_no_stable_rule_set(
    monkeypatch, tmp_path: Path
) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))

    monkeypatch.setattr("codex_ledger.reports.common._latest_stable_rule_set", lambda: None)
    payload = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
    )

    assert payload["pricing"]["included"] is False
    assert payload["pricing"]["coverage_status"] == "omitted"
    assert payload["data"]["selected_period_totals"]["cost_status"] == "omitted"


def test_aggregate_report_with_full_pricing_coverage(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "imported_report.json"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_aggregate_report(
        archive_home=archive_home,
        period="month",
        as_of=date(2026, 4, 15),
    )

    assert payload["pricing"]["selection_mode"] == "default_latest_stable"
    assert payload["pricing"]["selected_rule_set_id"] == RULE_SET_ID
    assert payload["pricing"]["coverage_status"] == "full"
    assert payload["data"]["selected_period_totals"] == {
        "event_count": 2,
        "total_tokens": 30,
        "input_tokens": 21,
        "cached_input_tokens": 3,
        "output_tokens": 9,
        "reasoning_output_tokens": 3,
        "workspace_count": 4,
        "session_count": 2,
        "agent_run_count": 2,
        "priced_token_total": 30,
        "unpriced_token_total": 0,
        "reference_usd_estimate": 0.00018075,
        "pricing_coverage_status": "full",
    }


def test_aggregate_report_shows_partial_pricing_warnings(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    _import_unknown_model_rollout(archive_home, tmp_path)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_aggregate_report(
        archive_home=archive_home,
        period="month",
        as_of=date(2026, 4, 30),
    )

    assert payload["pricing"]["coverage_status"] == "partial"
    assert payload["pricing"]["warnings"] == [
        "Pricing coverage is partial; the USD estimate is incomplete."
    ]
    assert payload["pricing"]["unsupported_or_unknown_by_model"] == [
        {
            "event_count": 1,
            "model_id": "provider/model-like/path",
            "reason": "no_matching_model_rule",
            "token_total": 9,
        }
    ]


def test_workspace_report_defaults_to_redacted_labels(tmp_path: Path) -> None:
    archive_home, workspace_root, nested = _import_absolute_workspace_snapshot(tmp_path)

    payload = build_workspace_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 21),
    )

    serialized = json.dumps(payload, sort_keys=True)
    assert str(workspace_root) not in serialized
    assert str(nested) not in serialized


def test_workspace_report_supports_alias_and_full_modes(tmp_path: Path) -> None:
    archive_home, workspace_root, _ = _import_absolute_workspace_snapshot(tmp_path)
    connection = open_database(archive_home)
    try:
        workspace_key = str(
            connection.execute("SELECT workspace_key FROM workspaces").fetchone()[0]
        )
        upsert_workspace_alias(
            connection,
            workspace_key=workspace_key,
            alias_label="workspace-alias-1",
        )
        connection.commit()
    finally:
        connection.close()

    alias_payload = build_workspace_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 21),
        redaction_mode="alias",
    )
    full_payload = build_workspace_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 21),
        redaction_mode="full",
    )

    assert alias_payload["data"]["workspaces"][0]["workspace_label"] == "workspace-alias-1"
    assert full_payload["data"]["workspaces"][0]["workspace_label"] == str(workspace_root)


def test_agent_report_reuses_phase21_observability_and_adds_pricing(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_agent_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 5),
    )

    assert payload["summary"]["matched_child_count"] == 1
    assert payload["summary"]["root_usage"]["total_tokens"] == 16
    assert payload["summary"]["subagent_usage"]["total_tokens"] == 11
    assert payload["pricing"]["coverage_status"] == "full"
    assert (
        payload["top_heavy_hitters"][0]["reference_usd_estimate"]
        >= (payload["top_heavy_hitters"][1]["reference_usd_estimate"])
    )


def test_explain_commands_trace_to_underlying_evidence(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        workspace_key = str(
            connection.execute(
                "SELECT workspace_key FROM workspaces ORDER BY workspace_key LIMIT 1"
            ).fetchone()[0]
        )
        agent_run_key = str(
            connection.execute(
                "SELECT agent_run_key FROM agent_runs "
                "WHERE lineage_key = 'session' "
                "ORDER BY agent_run_key LIMIT 1"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    day_payload = explain_day(
        archive_home=archive_home,
        day=date(2026, 4, 5),
    )
    workspace_payload = explain_workspace(
        archive_home=archive_home,
        workspace_key=workspace_key,
        period="month",
        as_of=date(2026, 4, 30),
    )
    model_payload = explain_model(
        archive_home=archive_home,
        model_id="gpt-5.4",
        period="month",
        as_of=date(2026, 4, 30),
    )
    agent_payload = explain_agent_run(
        archive_home=archive_home,
        agent_run_key=agent_run_key,
    )

    assert day_payload["source_artifacts"]
    assert day_payload["sessions"]
    assert day_payload["events"]
    assert day_payload["pricing"]["included"] is True
    assert workspace_payload["workspace_attribution"]
    assert model_payload["models"][0]["model_id"] == "gpt-5.4"
    assert agent_payload["provenance"]["stored_relpath"]
    assert agent_payload["event_summary"]["priced_token_total"] >= 0


def test_explicit_and_default_rule_set_selection_are_deterministic(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    explicit = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
        rule_set_id=RULE_SET_ID,
    )
    defaulted = build_aggregate_report(
        archive_home=archive_home,
        period="day",
        as_of=date(2026, 4, 1),
    )

    assert explicit["pricing"]["selection_mode"] == "explicit"
    assert defaulted["pricing"]["selection_mode"] == "default_latest_stable"
    assert explicit["pricing"]["selected_rule_set_id"] == RULE_SET_ID
    assert defaulted["pricing"]["selected_rule_set_id"] == RULE_SET_ID


def test_priced_and_unpriced_totals_reconcile_with_database_rows(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    _import_unknown_model_rollout(archive_home, tmp_path)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_workspace_report(
        archive_home=archive_home,
        period="month",
        as_of=date(2026, 4, 30),
    )

    connection = open_database(archive_home)
    try:
        priced_tokens = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(ue.total_tokens), 0)
                FROM usage_events AS ue
                JOIN cost_estimates AS ce
                  ON ce.event_id = ue.event_id
                WHERE ce.rule_set_id = ?
                  AND ce.estimate_status = 'priced'
                """,
                (RULE_SET_ID,),
            ).fetchone()[0]
        )
        total_tokens = int(
            connection.execute(
                "SELECT COALESCE(SUM(total_tokens), 0) FROM usage_events"
            ).fetchone()[0]
        )
    finally:
        connection.close()

    report_priced_tokens = sum(
        int(item["priced_token_total"]) for item in payload["data"]["workspaces"]
    )
    report_unpriced_tokens = sum(
        int(item["unpriced_token_total"]) for item in payload["data"]["workspaces"]
    )

    assert report_priced_tokens == priced_tokens
    assert report_priced_tokens + report_unpriced_tokens == total_tokens


def test_report_totals_reconcile_with_usage_events_and_priced_events(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "imported_report.json"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    aggregate = build_aggregate_report(
        archive_home=archive_home,
        period="month",
        as_of=date(2026, 4, 30),
    )

    connection = open_database(archive_home)
    try:
        db_totals = connection.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(total_tokens), 0)
            FROM usage_events
            WHERE total_tokens IS NOT NULL
            """
        ).fetchone()
        priced_total = float(
            connection.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM cost_estimates
                WHERE rule_set_id = ?
                  AND estimate_status = 'priced'
                """,
                (RULE_SET_ID,),
            ).fetchone()[0]
        )
    finally:
        connection.close()

    assert aggregate["data"]["selected_period_totals"]["event_count"] == int(db_totals[0])
    assert aggregate["data"]["selected_period_totals"]["total_tokens"] == int(db_totals[1])
    assert aggregate["pricing"]["reference_usd_estimate"] == priced_total


def test_report_json_is_deterministic_for_same_snapshot(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    first = {
        "aggregate": build_aggregate_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "workspace": build_workspace_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "agent": build_agent_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "explain_day": explain_day(
            archive_home=archive_home,
            day=date(2026, 4, 5),
        ),
    }
    second = {
        "aggregate": build_aggregate_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "workspace": build_workspace_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "agent": build_agent_report(
            archive_home=archive_home,
            period="month",
            as_of=date(2026, 4, 30),
        ),
        "explain_day": explain_day(
            archive_home=archive_home,
            day=date(2026, 4, 5),
        ),
    }

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


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


def _import_unknown_model_rollout(archive_home: Path, tmp_path: Path) -> None:
    rollout = tmp_path / "unknown-model-rollout.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-04-18T09:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "unknown-model-session",
                            "timestamp": "2026-04-18T08:59:55Z",
                            "cwd": "workspace-unknown-model/project",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-18T09:00:01Z",
                        "type": "turn_context",
                        "payload": {
                            "turn_id": "unknown-model-turn",
                            "cwd": "workspace-unknown-model/project/task",
                            "model": "provider/model-like/path",
                        },
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {
                        "timestamp": "2026-04-18T09:00:02Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": {
                                "last_token_usage": {
                                    "input_tokens": 7,
                                    "cached_input_tokens": 0,
                                    "output_tokens": 2,
                                    "reasoning_output_tokens": 1,
                                    "total_tokens": 9,
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
