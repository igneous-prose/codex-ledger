from __future__ import annotations

import html
import json
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from codex_ledger.reports.schema import ReportValidationError, load_report_file
from codex_ledger.storage.output import write_bytes_output, write_text_output
from codex_ledger.utils.hashing import sha256_file, sha256_text
from codex_ledger.utils.json import canonical_json


def render_heatmap(
    *,
    report_path: Path,
    output_path: Path,
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    payload = load_report_file(report_path)
    if payload.get("schema_version") != "phase4-aggregate-report-v1":
        raise ReportValidationError("Heatmap rendering requires an aggregate report JSON artifact")

    buckets = payload["data"]["period_buckets"]
    cell_size = 36
    margin = 24
    header_height = 84
    footer_height = 48
    width = max(320, margin * 2 + max(1, len(buckets)) * cell_size)
    height = header_height + footer_height + cell_size
    image = Image.new("RGB", (width, height), "#f8f4e9")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()

    draw.text((margin, 16), "Codex Ledger Heatmap", fill="#1f2937", font=font)
    draw.text(
        (margin, 32),
        f"{payload['filters']['period']} as of {payload['filters']['as_of']}",
        fill="#374151",
        font=font,
    )
    pricing = payload["pricing"]
    coverage_text = (
        "cost omitted"
        if not pricing["included"]
        else f"{pricing['selected_rule_set_id']} / {pricing['coverage_status']}"
    )
    draw.text((margin, 48), coverage_text, fill="#6b7280", font=font)

    max_tokens = max((int(item["total_tokens"]) for item in buckets), default=1)
    y = header_height
    for index, bucket in enumerate(buckets):
        x = margin + index * cell_size
        intensity = int(255 - (int(bucket["total_tokens"]) / max_tokens) * 155)
        fill = (255, intensity, 121)
        draw.rectangle((x, y, x + cell_size - 4, y + cell_size - 4), fill=fill, outline="#d1d5db")
        if pricing["included"]:
            coverage_status = _coverage_for_bucket(bucket)
            stripe = {"full": "#14532d", "partial": "#d97706", "none": "#6b7280"}.get(
                coverage_status,
                "#6b7280",
            )
            draw.rectangle(
                (x, y + cell_size - 8, x + cell_size - 4, y + cell_size - 4), fill=stripe
            )
        label = bucket["date"][-2:]
        draw.text((x + 10, y + 10), label, fill="#111827", font=font)

    png_buffer = BytesIO()
    image.save(png_buffer, format="PNG", compress_level=9)
    target = write_bytes_output(output_path, png_buffer.getvalue())
    sidecar = _write_sidecar(
        report_path=report_path,
        report_payload=payload,
        output_path=target,
        sidecar_path=sidecar_path,
        renderer_kind="heatmap",
    )
    return {"output_path": str(target), "sidecar_path": str(sidecar)}


def render_workspace_html(
    *,
    report_path: Path,
    output_path: Path,
    sidecar_path: Path | None = None,
) -> dict[str, Any]:
    payload = load_report_file(report_path)
    if payload.get("schema_version") != "phase4-workspace-report-v1":
        raise ReportValidationError(
            "Workspace HTML rendering requires a workspace report JSON artifact"
        )

    rows = []
    for item in payload["data"]["workspaces"]:
        coverage_label = item.get("coverage_status", item.get("cost_status", "omitted"))
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item['workspace_label']))}</td>"
            f"<td>{int(item['total_tokens'])}</td>"
            f"<td>{int(item['session_count'])}</td>"
            f"<td>{int(item['agent_run_count'])}</td>"
            f"<td>{html.escape(str(item['top_model']))}</td>"
            f"<td>{html.escape(str(coverage_label))}</td>"
            f"<td>{html.escape(str(item.get('reference_usd_estimate', 'n/a')))}</td>"
            "</tr>"
        )

    pricing = payload["pricing"]
    pricing_text = (
        html.escape(str(pricing["warnings"][0]))
        if not pricing["included"]
        else html.escape(
            f"{pricing['selected_rule_set_id']} / {pricing['coverage_status']} / "
            f"{pricing['reference_usd_estimate']} {pricing['currency']}"
        )
    )
    document = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Codex Ledger Workspace Report</title>\n"
        "<style>"
        "body{font-family:Georgia,serif;background:#f8f4e9;color:#1f2937;margin:24px;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{border:1px solid #d1d5db;padding:8px;text-align:left;}"
        "th{background:#efe7d4;}"
        ".meta{margin-bottom:16px;color:#4b5563;}"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<h1>Workspace Report</h1>\n"
        f'<div class="meta">{html.escape(payload["filters"]["period"])} as of '
        f"{html.escape(payload['filters']['as_of'])} / "
        f"redaction={html.escape(str(payload['filters']['redaction_mode']))}</div>\n"
        f'<div class="meta">pricing={pricing_text}</div>\n'
        "<table>\n"
        "<thead><tr><th>Workspace</th><th>Tokens</th><th>Sessions</th><th>Agent Runs</th>"
        "<th>Top Model</th><th>Coverage</th><th>Reference USD</th></tr></thead>\n"
        "<tbody>\n"
        f"{''.join(rows)}\n"
        "</tbody>\n"
        "</table>\n"
        "</body>\n"
        "</html>\n"
    )

    target = write_text_output(output_path, document)
    sidecar = _write_sidecar(
        report_path=report_path,
        report_payload=payload,
        output_path=target,
        sidecar_path=sidecar_path,
        renderer_kind="workspace_html",
    )
    return {"output_path": str(target), "sidecar_path": str(sidecar)}


def _write_sidecar(
    *,
    report_path: Path,
    report_payload: dict[str, Any],
    output_path: Path,
    sidecar_path: Path | None,
    renderer_kind: str,
) -> Path:
    target = (
        output_path.with_suffix(output_path.suffix + ".provenance.json")
        if sidecar_path is None
        else sidecar_path.expanduser()
    )
    pricing = report_payload.get("pricing")
    if isinstance(pricing, dict):
        selected_rule_set_id = pricing.get("selected_rule_set_id")
        coverage_status = pricing.get("coverage_status")
        currency = pricing.get("currency")
        reference_estimate = pricing.get("reference_usd_estimate")
        priced_token_total = pricing.get("priced_token_total")
        unpriced_token_total = pricing.get("unpriced_token_total")
    else:
        selected_rule_set_id = None
        coverage_status = None
        currency = None
        reference_estimate = None
        priced_token_total = None
        unpriced_token_total = None
    payload = {
        "renderer_kind": renderer_kind,
        "source_report_name": report_path.name,
        "source_report_sha256": sha256_file(report_path),
        "source_report_schema_version": report_payload.get("schema_version"),
        "report_generator_version": report_payload.get("generator_version"),
        "report_generated_at_utc": report_payload.get("generated_at_utc"),
        "redaction_mode": (
            report_payload.get("filters", {}).get("redaction_mode")
            if isinstance(report_payload.get("filters"), dict)
            else None
        ),
        "selected_pricing_rule_set_id": selected_rule_set_id,
        "pricing_coverage_status": coverage_status,
        "pricing_currency": currency,
        "pricing_reference_estimate": reference_estimate,
        "pricing_priced_token_total": priced_token_total,
        "pricing_unpriced_token_total": unpriced_token_total,
        "render_output_name": output_path.name,
        "render_output_sha256": sha256_file(output_path),
        "manifest_id": sha256_text(
            canonical_json(
                {
                    "renderer_kind": renderer_kind,
                    "source_report_sha256": sha256_file(report_path),
                    "render_output_sha256": sha256_file(output_path),
                }
            )
        )[:32],
    }
    return write_text_output(target, json_pretty(payload))


def _coverage_for_bucket(bucket: dict[str, Any]) -> str:
    priced = int(bucket.get("priced_token_total", 0))
    unpriced = int(bucket.get("unpriced_token_total", 0))
    if priced > 0 and unpriced == 0:
        return "full"
    if priced > 0 and unpriced > 0:
        return "partial"
    return "none"


def json_pretty(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
