from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


class ReportValidationError(ValueError):
    """Raised when a report payload or file does not match its schema."""


MAX_REPORT_FILE_BYTES = 16 * 1024 * 1024


SCHEMA_FILENAME_BY_VERSION = {
    "phase4-aggregate-report-v1": "aggregate-report-v1.schema.json",
    "phase4-workspace-report-v1": "workspace-report-v1.schema.json",
    "phase2.1-agent-diagnostics-v1": "agent-diagnostics-v1.schema.json",
    "phase4-explain-report-v1": "explain-report-v1.schema.json",
}


def validate_report_payload(payload: dict[str, Any]) -> None:
    schema = load_schema_for_payload(payload)
    _validate_against_schema(payload, schema, path="$")


def load_report_file(report_path: Path) -> dict[str, Any]:
    size_bytes = report_path.stat().st_size
    if size_bytes > MAX_REPORT_FILE_BYTES:
        raise ReportValidationError(
            "report file exceeds configured limit "
            f"({size_bytes} bytes > {MAX_REPORT_FILE_BYTES} bytes)"
        )
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ReportValidationError(f"Invalid JSON in {report_path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ReportValidationError(f"Report JSON must be an object: {report_path}")
    validate_report_payload(payload)
    return payload


def stable_report_json(payload: dict[str, Any]) -> str:
    validate_report_payload(payload)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def schema_filename_for_version(schema_version: str) -> str:
    try:
        return SCHEMA_FILENAME_BY_VERSION[schema_version]
    except KeyError as exc:
        raise ReportValidationError(f"Unknown report schema_version: {schema_version}") from exc


def load_schema_for_payload(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or schema_version == "":
        raise ReportValidationError("Report payload is missing schema_version")
    return load_schema(schema_filename_for_version(schema_version))


def load_schema(schema_filename: str) -> dict[str, Any]:
    repo_path = Path(__file__).resolve().parents[3] / "schemas" / "reports" / schema_filename
    if repo_path.exists():
        return _load_json(repo_path)

    resource_path = resources.files("codex_ledger.reports").joinpath(
        "schemas_data", schema_filename
    )
    return _load_json(Path(str(resource_path)))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportValidationError(f"Missing schema file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportValidationError(f"Invalid schema JSON in {path}: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ReportValidationError(f"Schema file must be an object: {path}")
    return payload


def _validate_against_schema(value: Any, schema: dict[str, Any], *, path: str) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        _validate_type(value, expected_type, path=path)

    if "const" in schema and value != schema["const"]:
        raise ReportValidationError(f"{path}: expected const {schema['const']!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise ReportValidationError(f"{path}: expected one of {schema['enum']!r}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    raise ReportValidationError(f"{path}: missing required key {key!r}")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    _validate_against_schema(value[key], child_schema, path=f"{path}.{key}")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_against_schema(item, item_schema, path=f"{path}[{index}]")


def _validate_type(value: Any, expected_type: str, *, path: str) -> None:
    checks = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    if expected_type in checks and not checks[expected_type]:
        raise ReportValidationError(f"{path}: expected {expected_type}")
