from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from codex_ledger.utils.hashing import sha256_file

ALLOWED_TOKEN_FIELDS = {"input_tokens", "cached_input_tokens", "output_tokens"}
ALLOWED_CACHED_INPUT_BEHAVIORS = {"subtract_from_input", "independent_only"}


class PricingRuleValidationError(ValueError):
    """Raised when a pricing rule file is invalid."""


@dataclass(frozen=True)
class TokenMapping:
    input_tokens_field: str
    cached_input_tokens_field: str | None
    output_tokens_field: str
    cached_input_behavior: str


@dataclass(frozen=True)
class PriceRule:
    rule_id: str
    provider: str
    model_id: str
    effective_from_utc: str | None
    effective_to_utc: str | None
    input_usd_per_1m: Decimal
    cached_input_usd_per_1m: Decimal | None
    output_usd_per_1m: Decimal
    stability: str
    confidence: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class PricingRuleSet:
    rule_set_id: str
    pricing_plane: str
    currency: str
    version: str
    effective_from_utc: str | None
    effective_to_utc: str | None
    stability: str
    confidence: str
    token_mapping: TokenMapping
    provenance: dict[str, Any]
    source_path: str
    source_hash: str
    rules: tuple[PriceRule, ...]


@dataclass(frozen=True)
class RuleSelection:
    status: str
    reason: str
    rule: PriceRule | None


@dataclass(frozen=True)
class BundledRuleFile:
    rule_set_id: str
    logical_source_path: str
    filesystem_path: Path
    source_hash: str


def available_rule_set_ids() -> tuple[str, ...]:
    _validate_repo_rule_mirror()
    return tuple(sorted(_bundled_rule_files_by_id()))


def list_rule_files() -> tuple[Path, ...]:
    _validate_repo_rule_mirror()
    return tuple(
        item.filesystem_path
        for item in sorted(_bundled_rule_files_by_id().values(), key=lambda item: item.rule_set_id)
    )


def load_rule_set(rule_set_id: str) -> PricingRuleSet:
    bundled = _bundled_rule_files_by_id().get(rule_set_id)
    if bundled is None:
        raise PricingRuleValidationError(f"Unknown pricing rule set: {rule_set_id}")
    _validate_repo_rule_mirror()
    return load_rule_file(bundled.filesystem_path, logical_source_path=bundled.logical_source_path)


def load_rule_file(path: Path, *, logical_source_path: str | None = None) -> PricingRuleSet:
    document = _load_json(path)
    token_mapping = _parse_token_mapping(document.get("token_mapping"))
    rules = tuple(_parse_rule(item) for item in _expect_list(document, "rules"))

    rule_set = PricingRuleSet(
        rule_set_id=_expect_str(document, "rule_set_id"),
        pricing_plane=_expect_str(document, "pricing_plane"),
        currency=_expect_str(document, "currency"),
        version=_expect_str(document, "version"),
        effective_from_utc=_optional_str(document.get("effective_from_utc")),
        effective_to_utc=_optional_str(document.get("effective_to_utc")),
        stability=_expect_str(document, "stability"),
        confidence=_expect_str(document, "confidence"),
        token_mapping=token_mapping,
        provenance=_expect_dict(document, "provenance"),
        source_path=str(path) if logical_source_path is None else logical_source_path,
        source_hash=sha256_file(path),
        rules=rules,
    )
    _validate_rule_set(rule_set)
    return rule_set


def select_rule(
    *,
    rule_set: PricingRuleSet,
    provider: str,
    model_id: str | None,
    event_ts_utc: str | None,
) -> RuleSelection:
    if model_id is None:
        return RuleSelection(status="unknown_model", reason="missing_model_id", rule=None)
    if event_ts_utc is None:
        return RuleSelection(
            status="unknown_pricing",
            reason="missing_event_timestamp",
            rule=None,
        )

    model_rules = [
        rule for rule in rule_set.rules if rule.provider == provider and rule.model_id == model_id
    ]
    if not model_rules:
        return RuleSelection(
            status="unsupported_model",
            reason="no_matching_model_rule",
            rule=None,
        )

    active_rules = [rule for rule in model_rules if _is_rule_active(rule, event_ts_utc)]
    if not active_rules:
        return RuleSelection(
            status="unknown_pricing",
            reason="no_effective_rule_for_event_timestamp",
            rule=None,
        )
    if len(active_rules) > 1:
        raise PricingRuleValidationError(
            f"Multiple active pricing rules for {provider}:{model_id} at {event_ts_utc}"
        )
    return RuleSelection(status="priced", reason="matched_rule", rule=active_rules[0])


def _validate_rule_set(rule_set: PricingRuleSet) -> None:
    seen_rule_ids: set[str] = set()
    windows: dict[tuple[str, str], list[tuple[datetime, datetime | None, str]]] = {}

    for rule in rule_set.rules:
        if rule.rule_id in seen_rule_ids:
            raise PricingRuleValidationError(f"Duplicate rule_id: {rule.rule_id}")
        seen_rule_ids.add(rule.rule_id)
        if rule.input_usd_per_1m < Decimal("0"):
            raise PricingRuleValidationError(f"Negative input price: {rule.rule_id}")
        if rule.output_usd_per_1m < Decimal("0"):
            raise PricingRuleValidationError(f"Negative output price: {rule.rule_id}")
        if rule.cached_input_usd_per_1m is not None and rule.cached_input_usd_per_1m < Decimal("0"):
            raise PricingRuleValidationError(f"Negative cached input price: {rule.rule_id}")

        window_key = (rule.provider, rule.model_id)
        windows.setdefault(window_key, []).append(
            (
                _to_datetime(rule.effective_from_utc),
                _to_datetime_or_none(rule.effective_to_utc),
                rule.rule_id,
            )
        )

    for key, value in windows.items():
        sorted_windows = sorted(value, key=lambda item: item[0])
        for index, current in enumerate(sorted_windows[:-1]):
            next_window = sorted_windows[index + 1]
            current_end = current[1]
            next_start = next_window[0]
            if current_end is None or current_end > next_start:
                raise PricingRuleValidationError(
                    "Overlapping effective windows for "
                    f"{key[0]}:{key[1]} between {current[2]} and {next_window[2]}"
                )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PricingRuleValidationError(str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise PricingRuleValidationError(f"Invalid JSON in {path}: {exc.msg}") from exc

    if not isinstance(document, dict):
        raise PricingRuleValidationError(f"Pricing rule file must be an object: {path}")
    if document.get("schema_version") != "pricing-rule-set-v1":
        raise PricingRuleValidationError(f"Unsupported schema_version in {path}")
    return document


def _parse_token_mapping(payload: Any) -> TokenMapping:
    if not isinstance(payload, dict):
        raise PricingRuleValidationError("token_mapping must be an object")

    input_field = _expect_str(payload, "input_tokens_field")
    cached_field = _optional_str(payload.get("cached_input_tokens_field"))
    output_field = _expect_str(payload, "output_tokens_field")
    behavior = _expect_str(payload, "cached_input_behavior")

    for field in (input_field, output_field):
        if field not in ALLOWED_TOKEN_FIELDS:
            raise PricingRuleValidationError(f"Unsupported token field mapping: {field}")
    if cached_field is not None and cached_field not in ALLOWED_TOKEN_FIELDS:
        raise PricingRuleValidationError(f"Unsupported token field mapping: {cached_field}")
    if behavior not in ALLOWED_CACHED_INPUT_BEHAVIORS:
        raise PricingRuleValidationError(f"Unsupported cached_input_behavior: {behavior}")

    return TokenMapping(
        input_tokens_field=input_field,
        cached_input_tokens_field=cached_field,
        output_tokens_field=output_field,
        cached_input_behavior=behavior,
    )


def _parse_rule(payload: Any) -> PriceRule:
    if not isinstance(payload, dict):
        raise PricingRuleValidationError("Each pricing rule must be an object")
    return PriceRule(
        rule_id=_expect_str(payload, "rule_id"),
        provider=_expect_str(payload, "provider"),
        model_id=_expect_str(payload, "model_id"),
        effective_from_utc=_optional_str(payload.get("effective_from_utc")),
        effective_to_utc=_optional_str(payload.get("effective_to_utc")),
        input_usd_per_1m=_decimal_from_value(payload.get("input_usd_per_1m"), "input_usd_per_1m"),
        cached_input_usd_per_1m=_optional_decimal_from_value(
            payload.get("cached_input_usd_per_1m"),
            "cached_input_usd_per_1m",
        ),
        output_usd_per_1m=_decimal_from_value(
            payload.get("output_usd_per_1m"),
            "output_usd_per_1m",
        ),
        stability=_expect_str(payload, "stability"),
        confidence=_expect_str(payload, "confidence"),
        provenance=_expect_dict(payload, "provenance"),
    )


def _is_rule_active(rule: PriceRule, event_ts_utc: str) -> bool:
    event_dt = _to_datetime(event_ts_utc)
    start = _to_datetime(rule.effective_from_utc)
    end = _to_datetime_or_none(rule.effective_to_utc)
    if event_dt < start:
        return False
    if end is not None and event_dt >= end:
        return False
    return True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@lru_cache(maxsize=1)
def _bundled_rule_files_by_id() -> dict[str, BundledRuleFile]:
    files: dict[str, BundledRuleFile] = {}
    for path in _resource_rule_files():
        document = _load_json(path)
        rule_set_id = _expect_str(document, "rule_set_id")
        if rule_set_id in files:
            raise PricingRuleValidationError(f"Duplicate bundled rule_set_id: {rule_set_id}")
        files[rule_set_id] = BundledRuleFile(
            rule_set_id=rule_set_id,
            logical_source_path=f"package:codex_ledger.pricing/rules_data/{path.name}",
            filesystem_path=path,
            source_hash=sha256_file(path),
        )
    return files


def _resource_rule_files() -> tuple[Path, ...]:
    resource_root = resources.files("codex_ledger.pricing").joinpath("rules_data")
    resource_paths = []
    for item in sorted(resource_root.iterdir(), key=lambda ref: ref.name):
        if item.name.endswith(".json"):
            resource_paths.append(Path(str(item)))
    return tuple(resource_paths)


def _repo_rule_files() -> tuple[Path, ...]:
    repo_rule_dir = _repo_rule_dir()
    if not repo_rule_dir.exists():
        return ()
    return tuple(path for path in sorted(repo_rule_dir.glob("*.json")) if path.is_file())


def _validate_repo_rule_mirror() -> None:
    repo_rule_dir = _repo_rule_dir()
    repo_files = _repo_rule_files()
    if not repo_files:
        if repo_rule_dir.exists():
            raise PricingRuleValidationError(
                "Repo pricing rule mirror is empty; expected bundled mirror files under "
                f"{repo_rule_dir}"
            )
        return

    bundled_by_filename = {
        item.filesystem_path.name: item for item in _bundled_rule_files_by_id().values()
    }
    repo_by_filename = {path.name: path for path in repo_files}

    unexpected = sorted(set(repo_by_filename) - set(bundled_by_filename))
    if unexpected:
        raise PricingRuleValidationError(
            "Unpackaged repo pricing rule files are not allowed: " + ", ".join(unexpected)
        )

    for filename, bundled in bundled_by_filename.items():
        repo_path = repo_by_filename.get(filename)
        if repo_path is None:
            raise PricingRuleValidationError(
                "Repo pricing rule mirror is incomplete; missing "
                f"{filename} for bundled rule set {bundled.rule_set_id}"
            )
        repo_hash = sha256_file(repo_path)
        if repo_hash != bundled.source_hash:
            raise PricingRuleValidationError(
                "Repo pricing rule mirror does not match bundled rule data for "
                f"{bundled.rule_set_id}: {filename}"
            )


def _repo_rule_dir() -> Path:
    return _repo_root() / "pricing" / "rules"


def _to_datetime(value: str | None) -> datetime:
    if value is None:
        raise PricingRuleValidationError("effective_from_utc is required")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate).astimezone(UTC)


def _to_datetime_or_none(value: str | None) -> datetime | None:
    if value is None:
        return None
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(candidate).astimezone(UTC)


def _expect_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or value == "":
        raise PricingRuleValidationError(f"Missing string field: {key}")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value != "":
        return value
    raise PricingRuleValidationError("Optional string field must be null or non-empty string")


def _expect_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise PricingRuleValidationError(f"Missing object field: {key}")
    return value


def _expect_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise PricingRuleValidationError(f"Missing list field: {key}")
    return value


def _decimal_from_value(value: Any, field_name: str) -> Decimal:
    if isinstance(value, str):
        try:
            return Decimal(value)
        except ArithmeticError as exc:
            raise PricingRuleValidationError(f"Invalid decimal for {field_name}") from exc
    raise PricingRuleValidationError(f"Missing decimal field: {field_name}")


def _optional_decimal_from_value(value: Any, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _decimal_from_value(value, field_name)
