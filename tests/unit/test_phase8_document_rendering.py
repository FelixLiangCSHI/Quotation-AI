"""Phase 8 unit tests: trusted context, branded rendering and asset control."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.documents.assets import (
    AssetError,
    FontConfiguration,
    load_font_configuration,
    resolve_asset,
    resolve_logo,
)
from app.documents.charts import build_charts, render_bar_chart_svg
from app.documents.context import (
    DocumentContextError,
    build_customer_document_context,
)
from app.documents.plan import build_document_plan, deterministic_document_plan
from app.documents.renderer import (
    DocumentRenderError,
    render_quotation_html,
    render_quotation_pdf,
    safe_document_filename,
)
from app.agents.schemas import Agent4DocumentPlanResponse
from app.documents.plan import DEFAULT_SECTION_IDS
from app.quotation_models import (
    ApprovalRecord,
    ApprovalStatus,
    LineItemCategory,
    QuotationDraft,
    QuotationLineItem,
    QuotationWorkflowState,
)
from tests.fixtures.phase6_helpers import make_decided_state

#: Every token that must never appear in a customer artefact.
INTERNAL_TOKENS = (
    "gross margin",
    "margin",
    "60000",
    "estimated cost",
    "35%",
    "35.0",
    "threshold",
    "POL-MARGIN-MVP-001",
    "COMM-MARGIN",
    "policy version",
    "override",
    "justification",
    "workbook",
    "comparable",
)


def approved_state(
    *,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    decision: str = "pass",
    margin: str | None = "40.00",
    optional_line: bool = False,
) -> QuotationWorkflowState:
    state = QuotationWorkflowState(
        draft=QuotationDraft(
            quotation_id="Q-PHASE8-1",
            customer_name="Northwind Diagnostics",
            region="eu",
            currency="USD",
            incoterm="DAP Milan",
            delivery_location="Milan, Italy",
            requested_delivery_date=(
                datetime.now(timezone.utc) + timedelta(days=60)
            ).date(),
        )
    )
    state.draft.line_items = [
        QuotationLineItem(
            line_id="L1",
            product_id="SYN-MAIN-1",
            description="Synthetic imaging system",
            category=LineItemCategory.MAIN_PRODUCT,
            quantity=1,
            unit_price=70000.0,
            estimated_unit_cost=40000.0,
        ),
        QuotationLineItem(
            line_id="L2",
            product_id="SYN-ACC-1",
            description="Detector grid",
            category=LineItemCategory.ACCESSORY,
            quantity=2,
            unit_price=10000.0,
        ),
        QuotationLineItem(
            line_id="L3",
            product_id="SYN-SVC-1",
            description="Installation and commissioning",
            category=LineItemCategory.SERVICE,
            quantity=1,
            unit_price=10000.0,
        ),
    ]
    if optional_line:
        state.draft.line_items.append(
            QuotationLineItem(
                line_id="L4",
                product_id="SYN-OPT-1",
                description="Extended warranty",
                category=LineItemCategory.WARRANTY,
                quantity=1,
                unit_price=5000.0,
                is_optional=True,
            )
        )
    make_decided_state(state, status=decision, margin=margin)
    state.approval = ApprovalRecord(
        status=status,
        actor="mia.manager",
        actor_role="sales_manager",
        timestamp=datetime(2026, 3, 1, tzinfo=timezone.utc),
        reason="Strategic account override justification recorded internally.",
    )
    return state


# -- context ----------------------------------------------------------------


def test_context_totals_use_every_line():
    context = build_customer_document_context(approved_state(), quotation_version=4)
    assert context.total == Decimal("100000.00")
    assert context.quotation_version == 4
    assert len(context.line_items) == 3
    assert context.validity_date > context.quotation_date


def test_optional_lines_are_excluded_from_the_total():
    context = build_customer_document_context(approved_state(optional_line=True))
    assert context.total == Decimal("100000.00")
    assert context.optional_total == Decimal("5000.00")
    assert context.has_optional_items is True


@pytest.mark.parametrize(
    "status",
    [
        ApprovalStatus.NOT_READY,
        ApprovalStatus.PENDING_REVIEW,
        ApprovalStatus.REJECTED,
        ApprovalStatus.REVISION_REQUESTED,
    ],
)
def test_unapproved_states_cannot_build_a_context(status):
    with pytest.raises(DocumentContextError):
        build_customer_document_context(approved_state(status=status))


def test_stale_validation_cannot_build_a_context():
    state = approved_state()
    state.validation_stale = True
    with pytest.raises(DocumentContextError):
        build_customer_document_context(state)


def test_override_approval_is_allowed():
    context = build_customer_document_context(
        approved_state(status=ApprovalStatus.APPROVED_WITH_OVERRIDE)
    )
    assert context.approval_status == "approved_with_override"


def test_context_composition_is_revenue_only():
    context = build_customer_document_context(approved_state())
    composition = dict(context.category_composition())
    assert composition["Product"] == Decimal("70000.00")
    assert composition["Accessory"] == Decimal("20000.00")
    assert composition["Service"] == Decimal("10000.00")
    assert dict(context.quantity_breakdown())["Accessory"] == 2


# -- charts -----------------------------------------------------------------


def test_charts_expose_no_internal_value():
    context = build_customer_document_context(approved_state())
    charts = build_charts(context)
    assert {chart.chart_id for chart in charts} == {
        "category_composition",
        "quantity_breakdown",
    }
    markup = " ".join(render_bar_chart_svg(chart) for chart in charts)
    assert "<script" not in markup
    lowered = markup.casefold()
    for token in ("margin", "cost", "60000", "threshold", "comparable"):
        assert token not in lowered


# -- rendering --------------------------------------------------------------


def test_branded_html_contains_every_required_element():
    context = build_customer_document_context(approved_state(), quotation_version=2)
    html = render_quotation_html(context, deterministic_document_plan())
    for expected in (
        "COMPANY LOGO",
        "Northwind Diagnostics",
        "Q-PHASE8-1",
        context.quotation_date.isoformat(),
        context.validity_date.isoformat(),
        "SYN-MAIN-1",
        "SYN-ACC-1",
        "SYN-SVC-1",
        "70,000.00",
        "100,000.00",
        "USD",
        "DAP Milan",
        "Quotation total",
        "Document version",
        "counter(page)",
    ):
        assert expected in html


def test_rendered_html_has_no_internal_disclosure_and_no_script():
    context = build_customer_document_context(approved_state())
    html = render_quotation_html(context, deterministic_document_plan())
    text = re.sub(r"<[^>]*>", " ", re.sub(r"<style.*?</style>", " ", html, flags=re.S))
    lowered = text.casefold()
    for token in INTERNAL_TOKENS:
        assert token.casefold() not in lowered, token
    assert "<script" not in html.casefold()
    assert "javascript:" not in html.casefold()


def test_rendered_html_makes_no_external_request():
    context = build_customer_document_context(approved_state())
    html = render_quotation_html(context, deterministic_document_plan())
    without_ns = html.replace('xmlns="http://www.w3.org/2000/svg"', "")
    assert "http://" not in without_ns
    assert "https://" not in without_ns
    assert "src=" not in without_ns or "data:" in without_ns
    assert "@import" not in html
    assert "file://" not in html


def test_hostile_agent_text_cannot_execute_in_the_template():
    context = build_customer_document_context(approved_state())
    response = Agent4DocumentPlanResponse.model_validate(
        {
            "sections": [
                {
                    "section_id": section_id,
                    "heading": section_id.title(),
                    "narrative": "",
                }
                for section_id in DEFAULT_SECTION_IDS
            ],
            "executive_summary": (
                "<script>fetch('http://evil.invalid')</script>{{ 7*7 }} "
                "A complete configured system."
            ),
        }
    )
    plan = build_document_plan(response, ai_generated=True, provider="mock")
    html = render_quotation_html(context, plan)
    assert "<script" not in html.casefold()
    assert "evil.invalid" not in html
    assert "49" not in html.split("A complete configured system")[0][-40:]
    assert "A complete configured system" in html


def test_reportlab_fallback_always_produces_a_pdf():
    context = build_customer_document_context(approved_state(), quotation_version=7)
    rendered = render_quotation_pdf(
        context, deterministic_document_plan(), preferred_engines=("reportlab",)
    )
    assert rendered.engine == "reportlab"
    assert rendered.content.startswith(b"%PDF")
    assert len(rendered.content) > 2000
    assert rendered.template_version == "branded-v1"
    assert rendered.filename == "Q-PHASE8-1-v7-quotation.pdf"


def test_unknown_engines_degrade_to_reportlab():
    context = build_customer_document_context(approved_state())
    rendered = render_quotation_pdf(
        context,
        deterministic_document_plan(),
        preferred_engines=("weasyprint", "playwright", "reportlab"),
    )
    assert rendered.content.startswith(b"%PDF")


def test_no_engine_available_raises():
    context = build_customer_document_context(approved_state())
    with pytest.raises(DocumentRenderError):
        render_quotation_pdf(
            context, deterministic_document_plan(), preferred_engines=("nonexistent",)
        )


def test_unknown_template_is_refused():
    context = build_customer_document_context(approved_state())
    with pytest.raises(DocumentRenderError):
        render_quotation_html(
            context, deterministic_document_plan(), template_name="../../etc/passwd"
        )


# -- filenames and assets ---------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["../../etc/passwd", "Q/../../evil", "Q-1\x00.pdf", "  ..  ", "C:\\evil\\x"],
)
def test_output_filenames_are_safe(raw):
    name = safe_document_filename(raw)
    assert "/" not in name and "\\" not in name and ".." not in name
    assert name.endswith(".pdf")


@pytest.mark.parametrize(
    "reference",
    [
        "../secret.png",
        "/etc/passwd",
        "sub/dir/logo.png",
        "https://evil.invalid/logo.png",
        "logo.exe",
        "logo.pdf",
    ],
)
def test_asset_references_outside_the_repository_are_refused(reference, tmp_path):
    with pytest.raises(AssetError):
        resolve_asset(reference, environment={"DOCUMENT_ASSET_ROOT": str(tmp_path)})


def test_missing_asset_returns_none(tmp_path):
    assert (
        resolve_asset(
            "logo.png", environment={"DOCUMENT_ASSET_ROOT": str(tmp_path)}
        )
        is None
    )


def test_approved_asset_is_resolved(tmp_path):
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    resolved = resolve_asset(
        "logo.png", environment={"DOCUMENT_ASSET_ROOT": str(tmp_path)}
    )
    assert resolved is not None and resolved.name == "logo.png"


def test_hostile_logo_configuration_is_ignored(tmp_path):
    assert (
        resolve_logo(
            environment={
                "DOCUMENT_ASSET_ROOT": str(tmp_path),
                "DOCUMENT_LOGO_ASSET": "../../etc/passwd",
            }
        )
        is None
    )


# -- fonts ------------------------------------------------------------------


def test_missing_font_degrades_gracefully():
    config = load_font_configuration({"DOCUMENT_FONT_REGULAR_PATH": "/no/such.ttf"})
    assert config.family == "Helvetica"
    assert config.fallback_used is True
    assert config.supports_non_western is False


def test_non_western_text_still_renders_with_the_fallback_font():
    state = approved_state()
    state.draft.customer_name = "北方诊断有限公司"
    state.draft.line_items[0].description = "合成成像系统"
    context = build_customer_document_context(state)
    rendered = render_quotation_pdf(
        context, deterministic_document_plan(), preferred_engines=("reportlab",)
    )
    assert rendered.content.startswith(b"%PDF")
    assert any("font" in warning.casefold() for warning in rendered.warnings)


def test_no_font_file_is_committed_to_the_repository():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    fonts = [
        path
        for path in root.rglob("*")
        if path.suffix.casefold() in {".ttf", ".otf", ".woff", ".woff2"}
        and ".git" not in path.parts
        and "site-packages" not in path.parts
    ]
    assert fonts == []


def test_font_configuration_description_is_secret_free():
    described = FontConfiguration().describe()
    assert set(described) == {
        "family",
        "regular_configured",
        "bold_configured",
        "fallback_used",
        "fallback_reason",
    }
