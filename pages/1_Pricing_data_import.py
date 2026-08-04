"""Pricing data import — upload, map, review and publish an offline SAP export.

No live SAP connection exists. The only input is an offline Excel export that
an authorised internal user uploads. The uploaded workbook is never used
directly as the runtime pricing source: it must pass file validation, sheet
selection, column mapping, normalisation, row validation, error quarantine and
explicit user confirmation before it can be published, and a separate explicit
action is required to activate it.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from app.ingestion.config import SUPPORTED_EXTENSIONS, load_ingestion_config
from app.ingestion.mapping import (
    ColumnMappingProfile,
    MappingError,
    suggest_mapping,
)
from app.ingestion.pipeline import (
    DatasetPlan,
    ImportSession,
    IngestionError,
    extract_mapped_rows,
    run_import,
    start_import,
)
from app.ingestion.report import (
    render_validation_report_csv,
    render_validation_report_markdown,
)
from app.ingestion.repository import (
    DuplicateImportError,
    PricingDataRepository,
    PricingDataRepositoryError,
)
from app.ingestion.schemas import DatasetKind, get_schema
from app.ingestion.storage import LocalWorkbookStorage
from app.ingestion.validation import validate_rows
from app.ingestion.workbook import WorkbookValidationError
from app.services.workflow_session import ensure_schema

SESSION_KEY = "pricing_import_session"
PREVIEW_KEY = "pricing_import_preview"

DATASET_LABELS = {kind: get_schema(kind).title for kind in DatasetKind}
NOT_MAPPED = "— not mapped —"
IGNORE_SHEET = "— ignore this sheet —"


st.set_page_config(page_title="Pricing data import", layout="wide")


@st.cache_resource
def _repository() -> PricingDataRepository:
    ensure_schema()
    return PricingDataRepository()


def main() -> None:
    st.title("Pricing data import")
    st.caption(
        "Offline SAP Excel export only. The application never connects to SAP."
    )

    upload_tab, review_tab, versions_tab = st.tabs(
        ["1. Upload and map", "2. Review and publish", "3. Versions"]
    )
    with upload_tab:
        _render_upload()
    with review_tab:
        _render_review()
    with versions_tab:
        _render_versions()


def _render_upload() -> None:
    config = load_ingestion_config()
    st.info(
        "Supported formats: "
        + ", ".join(SUPPORTED_EXTENSIONS)
        + f". Maximum size: {config.max_upload_bytes // (1024 * 1024)} MB. "
        "Password-protected workbooks are rejected.",
        icon=":material/info:",
    )

    uploaded = st.file_uploader(
        "Offline SAP Excel export",
        type=[extension.lstrip(".") for extension in SUPPORTED_EXTENSIONS],
        accept_multiple_files=False,
    )
    if uploaded is None:
        return

    try:
        session = start_import(
            uploaded.name,
            uploaded.getvalue(),
            config=config,
            storage=LocalWorkbookStorage(config=config),
        )
    except WorkbookValidationError as error:
        st.error(str(error), icon=":material/block:")
        return

    st.session_state[SESSION_KEY] = session
    st.success(
        f"{session.workbook.filename} accepted "
        f"({session.workbook.size_bytes:,} bytes).",
        icon=":material/check_circle:",
    )
    for warning in session.workbook.warnings:
        st.warning(warning, icon=":material/warning:")
    st.caption(f"File hash (SHA-256): `{session.workbook.content_hash}`")

    duplicate = _repository().find_version_by_checksum(
        session.workbook.content_hash
    )
    if duplicate is not None:
        st.warning(
            f"This exact workbook was already imported as {duplicate.label!r}. "
            "Re-importing is blocked unless you explicitly confirm it.",
            icon=":material/content_copy:",
        )

    _render_sheet_mapping(session)


def _render_sheet_mapping(session: ImportSession) -> None:
    st.subheader("Sheet selection and column mapping")
    saved_profiles = {
        profile.name: profile
        for profile in _repository().list_mapping_profiles()
    }
    plans: list[DatasetPlan] = []
    errors: list[str] = []

    for sheet_name in session.sheet_names:
        with st.expander(f"Sheet: {sheet_name}", expanded=False):
            choice = st.selectbox(
                "Canonical dataset",
                [IGNORE_SHEET] + [DATASET_LABELS[kind] for kind in DatasetKind],
                key=f"dataset::{sheet_name}",
            )
            if choice == IGNORE_SHEET:
                continue
            kind = next(
                item for item in DatasetKind if DATASET_LABELS[item] == choice
            )

            header_row = int(
                st.number_input(
                    "Header row",
                    min_value=1,
                    value=1,
                    step=1,
                    key=f"header::{sheet_name}",
                )
            )
            preview = session.preview_sheet(
                sheet_name, header_row=header_row, max_rows=10
            )
            if not preview.headers:
                st.error("No header row could be read from this sheet.")
                continue

            reusable = st.selectbox(
                "Reusable mapping profile",
                [NOT_MAPPED] + sorted(saved_profiles),
                key=f"profile::{sheet_name}",
            )
            if reusable == NOT_MAPPED:
                defaults = suggest_mapping(preview.headers, kind)
            else:
                defaults = dict(saved_profiles[reusable].field_to_header)

            schema = get_schema(kind)
            options = [NOT_MAPPED] + list(preview.headers)
            overrides: dict[str, str] = {}
            columns = st.columns(2)
            for index, canonical in enumerate(schema.fields):
                default = defaults.get(canonical.name, "")
                selected = columns[index % 2].selectbox(
                    canonical.name + (" *" if canonical.required else ""),
                    options,
                    index=options.index(default) if default in options else 0,
                    key=f"map::{sheet_name}::{canonical.name}",
                    help=", ".join(canonical.aliases) or None,
                )
                if selected != NOT_MAPPED:
                    overrides[canonical.name] = selected

            profile = ColumnMappingProfile(
                name=f"{kind.value}:{sheet_name}",
                dataset_kind=kind,
                sheet_name=sheet_name,
                header_row=header_row,
                field_to_header=overrides,
            )
            try:
                profile.validate()
            except MappingError as error:
                errors.append(f"{sheet_name}: {error}")
                st.error(str(error), icon=":material/error:")
                continue

            st.caption("Transformed preview")
            st.dataframe(
                _preview_records(session, profile),
                use_container_width=True,
            )

            profile_name = st.text_input(
                "Save this mapping as",
                value=profile.name,
                key=f"save-name::{sheet_name}",
            )
            if st.button(
                "Save mapping profile",
                key=f"save::{sheet_name}",
                icon=":material/save:",
            ):
                _repository().save_mapping_profile(
                    ColumnMappingProfile(
                        name=profile_name,
                        dataset_kind=profile.dataset_kind,
                        sheet_name=profile.sheet_name,
                        header_row=profile.header_row,
                        field_to_header=dict(profile.field_to_header),
                    ),
                    created_by="internal",
                )
                st.success(f"Saved mapping profile {profile_name!r}.")

            plans.append(DatasetPlan(kind, profile))

    if not plans:
        st.info("Map at least one sheet to continue.", icon=":material/info:")
        return

    if st.button(
        "Validate workbook",
        type="primary",
        icon=":material/rule:",
        disabled=bool(errors),
    ):
        try:
            import_preview = run_import(session, plans)
        except (IngestionError, MappingError) as error:
            st.error(str(error), icon=":material/error:")
            return
        st.session_state[PREVIEW_KEY] = import_preview
        st.success(
            "Validation complete. Open the review tab before publishing.",
            icon=":material/check_circle:",
        )


def _preview_records(
    session: ImportSession,
    profile: ColumnMappingProfile,
    limit: int = 5,
) -> list[dict[str, Any]]:
    rows, row_numbers = extract_mapped_rows(session.workbook, profile)
    result = validate_rows(
        rows[:limit],
        profile,
        row_numbers=row_numbers[:limit],
        config=session.config,
    )
    ordered = sorted(
        result.valid_rows + result.warning_rows + result.rejected_rows,
        key=lambda row: row.row_number,
    )
    return [
        {
            "row": row.row_number,
            "status": "rejected" if row.rejected else "ok",
            **{name: str(value) for name, value in row.values.items()},
        }
        for row in ordered
    ]


def _render_review() -> None:
    import_preview = st.session_state.get(PREVIEW_KEY)
    if import_preview is None:
        st.info("Upload and validate a workbook first.", icon=":material/info:")
        return

    counts = import_preview.counts
    columns = st.columns(4)
    columns[0].metric("Valid rows", counts["valid"])
    columns[1].metric("Warning rows", counts["warning"])
    columns[2].metric("Rejected rows", counts["rejected"])
    columns[3].metric("Rows read", counts["total"])

    for warning in import_preview.warnings:
        st.warning(warning, icon=":material/warning:")

    st.subheader("Per-dataset results")
    st.dataframe(
        [
            {
                "dataset": staged.dataset_kind.value,
                "sheet": staged.sheet_name,
                **staged.result.counts,
            }
            for staged in import_preview.datasets
        ],
        use_container_width=True,
    )

    if import_preview.has_rejections:
        st.error(
            "Rejected rows are quarantined and will not be published.",
            icon=":material/block:",
        )
        st.dataframe(
            [
                {
                    "dataset": staged.dataset_kind.value,
                    "row": row.row_number,
                    "issue": issue.code.value,
                    "field": issue.field_name,
                    "message": issue.message,
                }
                for staged in import_preview.datasets
                for row in staged.result.rejected_rows
                for issue in row.issues
            ],
            use_container_width=True,
        )

    short_hash = import_preview.workbook.content_hash[:12]
    report_columns = st.columns(2)
    report_columns[0].download_button(
        "Download validation report (CSV)",
        data=render_validation_report_csv(import_preview),
        file_name=f"validation_report_{short_hash}.csv",
        mime="text/csv",
        icon=":material/download:",
    )
    report_columns[1].download_button(
        "Download validation report (Markdown)",
        data=render_validation_report_markdown(import_preview),
        file_name=f"validation_report_{short_hash}.md",
        mime="text/markdown",
        icon=":material/download:",
    )

    st.subheader("Confirm and publish")
    st.caption(
        "Publishing creates a new pricing data version. It does NOT change the "
        "active version; activation is a separate explicit action."
    )
    label = st.text_input("Version label", value=f"import-{short_hash[:8]}")
    uploader = st.text_input("Uploaded by", value="internal.user")
    notes = st.text_area("Notes", value="")
    confirmed = st.checkbox(
        "I have reviewed the validation results and confirm this import."
    )
    force = st.checkbox(
        "Re-import this workbook even though its file hash already exists.",
        value=False,
    )

    if st.button(
        "Publish version",
        type="primary",
        icon=":material/publish:",
        disabled=not confirmed,
    ):
        repository = _repository()
        try:
            version = repository.create_version_from_preview(
                import_preview,
                label=label,
                uploaded_by=uploader,
                notes=notes,
                allow_duplicate_hash=force,
            )
            published = repository.publish(version.id)
        except DuplicateImportError as error:
            st.error(str(error), icon=":material/content_copy:")
            return
        except PricingDataRepositoryError as error:
            st.error(str(error), icon=":material/error:")
            return
        st.success(
            f"Published {published.label!r} with {published.row_count} rows. "
            "Activate it on the versions tab when you are ready.",
            icon=":material/check_circle:",
        )
        st.session_state.pop(PREVIEW_KEY, None)


def _render_versions() -> None:
    repository = _repository()
    active = repository.get_active_version()
    if active is None:
        st.warning(
            "No pricing data version is active. The pricing engine falls back "
            "to the synthetic development dataset.",
            icon=":material/warning:",
        )
        if st.button(
            "Register the synthetic fallback version",
            icon=":material/science:",
        ):
            repository.register_synthetic_fallback()
            st.rerun()
    else:
        st.success(
            f"Active version: {active.label} "
            f"({active.row_count} rows, published {active.published_at}).",
            icon=":material/verified:",
        )

    versions = repository.list_versions()
    if not versions:
        st.info("No pricing data versions exist yet.", icon=":material/info:")
        return

    st.dataframe(
        [
            {
                "label": version.label,
                "status": version.status,
                "active": version.is_active,
                "source file": version.source_filename,
                "file hash": version.checksum[:12],
                "uploaded by": version.uploaded_by,
                "uploaded at": version.uploaded_at,
                "published at": version.published_at,
                "rows": version.row_count,
                "warnings": version.warning_row_count,
                "rejected": version.rejected_row_count,
            }
            for version in versions
        ],
        use_container_width=True,
    )

    publishable = {
        version.label: version
        for version in versions
        if version.status == "published"
    }
    if not publishable:
        return
    selected = st.selectbox("Version to activate", sorted(publishable))
    st.caption(
        "Activation is explicit. Nothing switches the active pricing dataset "
        "automatically."
    )
    if st.button("Activate selected version", icon=":material/play_arrow:"):
        try:
            repository.activate(publishable[selected].id)
        except PricingDataRepositoryError as error:
            st.error(str(error), icon=":material/error:")
            return
        st.rerun()


main()
