from __future__ import annotations

import json
from pathlib import Path

from codex_ledger.domain.records import ImportCandidate
from codex_ledger.ingest.service import run_import_batch
from codex_ledger.pricing.rules import (
    PricingRuleValidationError,
    available_rule_set_ids,
    load_rule_file,
    load_rule_set,
    select_rule,
)
from codex_ledger.pricing.service import build_pricing_coverage, recalculate_event_costs
from tests.test_support import fetch_all, fixture_path, open_database

RULE_SET_ID = "reference_usd_openai_standard_2026_04_07"


def test_seeded_rule_file_loads() -> None:
    rule_set = load_rule_set(RULE_SET_ID)

    assert RULE_SET_ID in available_rule_set_ids()
    assert rule_set.pricing_plane == "reference_usd"
    assert rule_set.currency == "USD"
    assert [rule.model_id for rule in rule_set.rules] == ["gpt-5.4", "gpt-5.4-mini"]


def test_invalid_rule_file_is_rejected(tmp_path: Path) -> None:
    rule_path = tmp_path / "invalid-rule.json"
    rule_path.write_text(
        json.dumps(
            {
                "schema_version": "pricing-rule-set-v1",
                "rule_set_id": "invalid",
                "pricing_plane": "reference_usd",
                "currency": "USD",
                "version": "2026-04-07",
                "effective_from_utc": "2026-04-07T00:00:00Z",
                "effective_to_utc": None,
                "stability": "reference",
                "confidence": "high",
                "token_mapping": {
                    "input_tokens_field": "input_tokens",
                    "cached_input_tokens_field": "bad_field",
                    "output_tokens_field": "output_tokens",
                    "cached_input_behavior": "subtract_from_input",
                },
                "provenance": {"notes": "invalid"},
                "rules": [],
            }
        ),
        encoding="utf-8",
    )

    try:
        load_rule_file(rule_path)
    except PricingRuleValidationError as exc:
        assert "bad_field" in str(exc)
    else:
        raise AssertionError("expected invalid rule file to be rejected")


def test_effective_date_window_selection_is_deterministic(tmp_path: Path) -> None:
    rule_path = tmp_path / "windowed-rule.json"
    rule_path.write_text(
        json.dumps(
            {
                "schema_version": "pricing-rule-set-v1",
                "rule_set_id": "windowed",
                "pricing_plane": "reference_usd",
                "currency": "USD",
                "version": "2026-04-07",
                "effective_from_utc": "2026-01-01T00:00:00Z",
                "effective_to_utc": None,
                "stability": "reference",
                "confidence": "high",
                "token_mapping": {
                    "input_tokens_field": "input_tokens",
                    "cached_input_tokens_field": "cached_input_tokens",
                    "output_tokens_field": "output_tokens",
                    "cached_input_behavior": "subtract_from_input",
                },
                "provenance": {"notes": "window-test"},
                "rules": [
                    {
                        "rule_id": "gpt-5.4-old",
                        "provider": "codex",
                        "model_id": "gpt-5.4",
                        "effective_from_utc": "2026-01-01T00:00:00Z",
                        "effective_to_utc": "2026-02-01T00:00:00Z",
                        "input_usd_per_1m": "1.00",
                        "cached_input_usd_per_1m": "0.10",
                        "output_usd_per_1m": "5.00",
                        "stability": "reference",
                        "confidence": "high",
                        "provenance": {"notes": "old"},
                    },
                    {
                        "rule_id": "gpt-5.4-new",
                        "provider": "codex",
                        "model_id": "gpt-5.4",
                        "effective_from_utc": "2026-02-01T00:00:00Z",
                        "effective_to_utc": None,
                        "input_usd_per_1m": "2.00",
                        "cached_input_usd_per_1m": "0.20",
                        "output_usd_per_1m": "6.00",
                        "stability": "reference",
                        "confidence": "high",
                        "provenance": {"notes": "new"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rule_set = load_rule_file(rule_path)

    old_selection = select_rule(
        rule_set=rule_set,
        provider="codex",
        model_id="gpt-5.4",
        event_ts_utc="2026-01-15T12:00:00Z",
    )
    new_selection = select_rule(
        rule_set=rule_set,
        provider="codex",
        model_id="gpt-5.4",
        event_ts_utc="2026-03-01T12:00:00Z",
    )

    assert old_selection.rule is not None
    assert new_selection.rule is not None
    assert old_selection.rule.rule_id == "gpt-5.4-old"
    assert new_selection.rule.rule_id == "gpt-5.4-new"


def test_repricing_is_deterministic_for_same_rule_set(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "imported_report.json"),
    )

    first = recalculate_event_costs(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )
    second = recalculate_event_costs(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )

    assert first["status_counts"] == {"priced": 2}
    assert first["inserted_count"] == 2
    assert first["updated_count"] == 0
    assert first["unchanged_count"] == 0
    assert second["inserted_count"] == 0
    assert second["updated_count"] == 0
    assert second["unchanged_count"] == 2
    assert first["priced_amount_total"] == second["priced_amount_total"]


def test_observed_model_is_used_for_pricing_not_requested_model(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            """
            SELECT ce.amount, ce.explanation_json
            FROM cost_estimates AS ce
            JOIN usage_events AS ue
              ON ue.event_id = ce.event_id
            JOIN agent_runs AS ar
              ON ar.agent_run_key = ue.agent_run_key
            WHERE ar.agent_kind = 'subagent'
              AND ue.payload_type = 'token_count'
            """
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    explanation = json.loads(str(row[1]))
    assert row[0] == 0.000065
    assert explanation["observed_model_id"] == "gpt-5.4"
    assert explanation["requested_model_id"] == "gpt-5.4-mini"
    assert explanation["matched_rule_id"] == "gpt-5.4-standard-2026-03-05"


def test_zero_event_spawn_placeholders_do_not_receive_cost_rows(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("lineage_parent_rollout.jsonl",),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        event_count = int(connection.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0])
        token_event_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM usage_events
                WHERE total_tokens IS NOT NULL
                   OR input_tokens IS NOT NULL
                   OR cached_input_tokens IS NOT NULL
                   OR output_tokens IS NOT NULL
                """
            ).fetchone()[0]
        )
        cost_count = int(connection.execute("SELECT COUNT(*) FROM cost_estimates").fetchone()[0])
        spawn_rows = fetch_all(
            connection,
            """
            SELECT ar.lineage_key, COUNT(ue.event_id), COUNT(ce.cost_estimate_id)
            FROM agent_runs AS ar
            LEFT JOIN usage_events AS ue
              ON ue.agent_run_key = ar.agent_run_key
            LEFT JOIN cost_estimates AS ce
              ON ce.event_id = ue.event_id
            WHERE ar.lineage_key = 'spawn:child-session'
            GROUP BY ar.lineage_key
            """,
        )
    finally:
        connection.close()

    assert event_count == 4
    assert token_event_count == 1
    assert cost_count == token_event_count
    assert spawn_rows == [("spawn:child-session", 0, 0)]


def test_unknown_model_pricing_stays_explicit(tmp_path: Path) -> None:
    archive_home = tmp_path / "archive"
    _import_unknown_model_rollout(archive_home, tmp_path)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        row = connection.execute(
            "SELECT amount, estimate_status, explanation_json FROM cost_estimates"
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row[0] is None
    assert row[1] == "unsupported_model"
    explanation = json.loads(str(row[2]))
    assert explanation["observed_model_id"] == "provider/model-like/path"
    assert explanation["reason"] == "no_matching_model_rule"


def test_cached_input_tokens_are_priced_explicitly(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        row = connection.execute("SELECT amount, explanation_json FROM cost_estimates").fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row[0] == 0.0000805
    explanation = json.loads(str(row[1]))
    assert explanation["billable_token_counts"] == {
        "input_tokens": 8,
        "cached_input_tokens": 2,
        "output_tokens": 4,
    }
    assert explanation["cached_input_behavior"] == "subtract_from_input"


def test_no_duplicate_cost_rows_exist_for_same_event_and_rule_set(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    connection = open_database(archive_home)
    try:
        rows = fetch_all(
            connection,
            """
            SELECT event_id, rule_set_id, pricing_plane, COUNT(*)
            FROM cost_estimates
            GROUP BY event_id, rule_set_id, pricing_plane
            """,
        )
    finally:
        connection.close()

    assert len(rows) == 1
    assert rows[0][1:] == (RULE_SET_ID, "reference_usd", 1)


def test_pricing_coverage_reports_priced_and_unpriced_totals(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(tmp_path / "archive", ("sample_rollout.jsonl",))
    _import_unknown_model_rollout(archive_home, tmp_path)
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_pricing_coverage(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )

    assert payload["summary"] == {
        "priced_event_count": 1,
        "unpriced_event_count": 1,
        "priced_token_total": 14,
        "unpriced_token_total": 9,
        "priced_amount_total": 8.05e-05,
    }
    assert payload["unsupported_or_unknown_by_model"] == [
        {
            "event_count": 1,
            "model_id": "provider/model-like/path",
            "reason": "no_matching_model_rule",
            "token_total": 9,
        }
    ]


def test_workspace_model_and_agent_rollups_match_priced_event_totals(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    payload = build_pricing_coverage(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )
    summary_total = payload["summary"]["priced_amount_total"]

    workspace_total = sum(item["priced_amount_total"] for item in payload["coverage_by_workspace"])
    model_total = sum(item["priced_amount_total"] for item in payload["coverage_by_model"])
    agent_total = sum(item["priced_amount_total"] for item in payload["coverage_by_agent_run"])

    assert workspace_total == summary_total
    assert model_total == summary_total
    assert agent_total == summary_total


def test_pricing_coverage_is_stable_for_same_snapshot(tmp_path: Path) -> None:
    archive_home = _import_fixture_batch(
        tmp_path / "archive",
        ("sample_rollout.jsonl", "lineage_parent_rollout.jsonl", "lineage_child_rollout.jsonl"),
    )
    recalculate_event_costs(archive_home=archive_home, rule_set_id=RULE_SET_ID)

    first = build_pricing_coverage(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )
    second = build_pricing_coverage(
        archive_home=archive_home,
        rule_set_id=RULE_SET_ID,
    )

    assert first == second


def test_rebuild_from_raw_preserves_priced_event_rows(tmp_path: Path) -> None:
    archive_one = _import_fixture_batch(tmp_path / "archive-one", ("sample_rollout.jsonl",))
    recalculate_event_costs(archive_home=archive_one, rule_set_id=RULE_SET_ID)

    connection_one = open_database(archive_one)
    try:
        stored_relpath = str(
            connection_one.execute(
                "SELECT stored_relpath FROM raw_files ORDER BY stored_relpath"
            ).fetchone()[0]
        )
        cost_rows_one = fetch_all(
            connection_one,
            """
            SELECT event_id, rule_set_id, pricing_plane, amount, estimate_status, explanation_json
            FROM cost_estimates
            ORDER BY event_id
            """,
        )
        coverage_one = build_pricing_coverage(
            archive_home=archive_one,
            rule_set_id=RULE_SET_ID,
        )
    finally:
        connection_one.close()

    archive_two = tmp_path / "archive-two"
    archived_raw_path = archive_one / "raw" / stored_relpath
    run_import_batch(
        archive_home=archive_two,
        candidates=(ImportCandidate(archived_raw_path, "local_rollout_file"),),
        provider="codex",
        host="standalone_cli",
        source_kind="local_rollout_file",
        full_backfill=False,
    )
    recalculate_event_costs(archive_home=archive_two, rule_set_id=RULE_SET_ID)

    connection_two = open_database(archive_two)
    try:
        cost_rows_two = fetch_all(
            connection_two,
            """
            SELECT event_id, rule_set_id, pricing_plane, amount, estimate_status, explanation_json
            FROM cost_estimates
            ORDER BY event_id
            """,
        )
        coverage_two = build_pricing_coverage(
            archive_home=archive_two,
            rule_set_id=RULE_SET_ID,
        )
    finally:
        connection_two.close()

    assert cost_rows_one == cost_rows_two
    assert coverage_one == coverage_two


def _import_fixture_batch(archive_home: Path, fixture_names: tuple[str, ...]) -> Path:
    grouped: dict[str, list[ImportCandidate]] = {
        "local_rollout_file": [],
        "imported_json_report": [],
    }
    for name in fixture_names:
        source_kind = _source_kind_for_fixture(name)
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


def _source_kind_for_fixture(name: str) -> str:
    if name.endswith(".json"):
        return "imported_json_report"
    return "local_rollout_file"
