"""Downloadable validation reports.

Two formats are produced from the same staged preview: a CSV of every issue
for spreadsheet triage, and a Markdown summary for the audit record.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from app.ingestion.pipeline import ImportPreview

REPORT_COLUMNS = (
    "dataset",
    "sheet",
    "row_number",
    "disposition",
    "severity",
    "code",
    "field",
    "message",
)


def build_validation_report_rows(preview: ImportPreview) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dataset in preview.datasets:
        buckets = (
            ("valid", dataset.result.valid_rows),
            ("warning", dataset.result.warning_rows),
            ("rejected", dataset.result.rejected_rows),
        )
        for disposition, validated_rows in buckets:
            for validated in validated_rows:
                if not validated.issues:
                    rows.append(
                        {
                            "dataset": dataset.dataset_kind.value,
                            "sheet": dataset.sheet_name,
                            "row_number": validated.row_number,
                            "disposition": disposition,
                            "severity": "",
                            "code": "",
                            "field": "",
                            "message": "",
                        }
                    )
                    continue
                for issue in validated.issues:
                    rows.append(
                        {
                            "dataset": dataset.dataset_kind.value,
                            "sheet": dataset.sheet_name,
                            "row_number": validated.row_number,
                            "disposition": disposition,
                            "severity": issue.severity.value,
                            "code": issue.code.value,
                            "field": issue.field_name,
                            "message": issue.message,
                        }
                    )
    return rows


def render_validation_report_csv(preview: ImportPreview) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=REPORT_COLUMNS)
    writer.writeheader()
    for row in build_validation_report_rows(preview):
        writer.writerow(row)
    return buffer.getvalue()


def render_validation_report_markdown(preview: ImportPreview) -> str:
    counts = preview.counts
    lines = [
        "# Pricing data validation report",
        "",
        f"- Source file: `{preview.workbook.filename}`",
        f"- File hash (SHA-256): `{preview.workbook.content_hash}`",
        f"- Size: {preview.workbook.size_bytes} bytes",
        f"- Accepted (valid): {counts['valid']}",
        f"- Accepted with warnings: {counts['warning']}",
        f"- Rejected (quarantined): {counts['rejected']}",
        f"- Total rows read: {counts['total']}",
        "",
        "## Datasets",
        "",
        "| Dataset | Sheet | Valid | Warning | Rejected |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for dataset in preview.datasets:
        dataset_counts = dataset.result.counts
        lines.append(
            f"| {dataset.dataset_kind.value} | {dataset.sheet_name} | "
            f"{dataset_counts['valid']} | {dataset_counts['warning']} | "
            f"{dataset_counts['rejected']} |"
        )

    issue_rows = [
        row
        for row in build_validation_report_rows(preview)
        if row["severity"]
    ]
    lines += ["", "## Issues", ""]
    if not issue_rows:
        lines.append("No issues were found.")
    else:
        lines += [
            "| Dataset | Row | Severity | Code | Field | Message |",
            "| --- | ---: | --- | --- | --- | --- |",
        ]
        for row in issue_rows:
            message = str(row["message"]).replace("|", "\\|")
            lines.append(
                f"| {row['dataset']} | {row['row_number']} | {row['severity']} "
                f"| {row['code']} | {row['field']} | {message} |"
            )

    if preview.warnings:
        lines += ["", "## Import warnings", ""]
        lines += [f"- {warning}" for warning in preview.warnings]

    return "\n".join(lines) + "\n"


def render_validation_summary_json(preview: ImportPreview) -> str:
    return json.dumps(preview.summary(), indent=2, sort_keys=True)
