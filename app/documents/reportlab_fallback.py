"""Deterministic ReportLab fallback renderer.

ReportLab is always available in this deployment, needs no browser and no
system libraries, and therefore remains the guaranteed fallback when neither
WeasyPrint nor Playwright can be used. It renders exactly the same trusted
context and the same validated plan as the HTML template, so no customer-safe
boundary depends on which engine ran.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Mapping
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.documents.assets import FontConfiguration, resolve_logo
from app.documents.charts import build_charts, chart_table_rows
from app.documents.context import CustomerDocumentContext
from app.documents.plan import DocumentPlan

LOGGER = logging.getLogger(__name__)

INK = colors.HexColor("#18212F")
RULE = colors.HexColor("#D8DEE8")
MUTED = colors.HexColor("#5A6478")


def _register_fonts(font: FontConfiguration) -> tuple[str, str]:
    """Register configured fonts, falling back gracefully when unavailable."""

    if font.regular_path is None:
        return "Helvetica", "Helvetica-Bold"
    regular_name = f"{font.family}-Regular"
    bold_name = f"{font.family}-Bold"
    try:
        if regular_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(regular_name, str(font.regular_path)))
        bold_path = font.bold_path or font.regular_path
        if bold_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(bold_name, str(bold_path)))
    except Exception as error:  # noqa: BLE001 - graceful font degradation
        LOGGER.info(
            "Configured font could not be embedded (%s); using the built-in font.",
            type(error).__name__,
        )
        return "Helvetica", "Helvetica-Bold"
    return regular_name, bold_name


def render_quotation_pdf_reportlab(
    context: CustomerDocumentContext,
    plan: DocumentPlan,
    *,
    font: FontConfiguration | None = None,
    logo_asset: str | None = None,
    include_charts: bool = True,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    font_config = font or FontConfiguration()
    regular, bold = _register_fonts(font_config)
    styles = _styles(regular, bold)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=20 * mm,
        title=f"Quotation {context.quotation_id}",
        author="Quotation",
        subject="Customer quotation",
    )
    story: list[Any] = []
    for section_id in plan.section_ids:
        builder = _SECTIONS.get(section_id)
        if builder is None:
            continue
        story.extend(
            builder(context, plan, styles, logo_asset, include_charts, environment)
        )

    footer = (
        f"Quotation {context.quotation_id} · Document version "
        f"{context.document_version} · Template branded-v1"
    )

    def _canvas(*args: Any, **kwargs: Any) -> pdf_canvas.Canvas:
        kwargs["pageCompression"] = 0
        canvas = _NumberedCanvas(*args, footer_text=footer, font_name=regular, **kwargs)
        canvas.setTitle(f"Quotation {_safe(context.quotation_id, regular)}")
        canvas.setAuthor("Quotation")
        canvas.setSubject("Customer quotation")
        return canvas

    document.build(story, canvasmaker=_canvas)
    return buffer.getvalue()


class _NumberedCanvas(pdf_canvas.Canvas):
    """Adds ``Page n of m`` and the document version to every page."""

    def __init__(self, *args: Any, footer_text: str = "", font_name: str = "Helvetica", **kwargs: Any) -> None:
        self._footer_text = footer_text
        self._footer_font = font_name
        self._saved_states: list[dict] = []
        super().__init__(*args, **kwargs)

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._saved_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_states)
        for state in self._saved_states:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int) -> None:
        self.setFont(self._footer_font, 7)
        self.setFillColor(MUTED)
        self.drawString(18 * mm, 12 * mm, _safe(self._footer_text, self._footer_font))
        self.drawRightString(
            A4[0] - 18 * mm,
            12 * mm,
            f"Page {self._pageNumber} of {total}",
        )


def _styles(regular: str, bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "QTitle",
            parent=base["Title"],
            fontName=bold,
            fontSize=22,
            leading=26,
            textColor=INK,
            alignment=TA_CENTER,
            spaceAfter=6,
        ),
        "subtitle": ParagraphStyle(
            "QSubtitle",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=11,
            leading=14,
            textColor=MUTED,
            alignment=TA_CENTER,
            spaceAfter=8,
        ),
        "heading": ParagraphStyle(
            "QHeading",
            parent=base["Heading2"],
            fontName=bold,
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "QBody",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=9.5,
            leading=13,
            textColor=INK,
        ),
        "bold": ParagraphStyle(
            "QBold",
            parent=base["BodyText"],
            fontName=bold,
            fontSize=9.5,
            leading=13,
            textColor=INK,
        ),
        "small": ParagraphStyle(
            "QSmall",
            parent=base["BodyText"],
            fontName=regular,
            fontSize=8.5,
            leading=11.5,
            textColor=MUTED,
        ),
        "_fonts": (regular, bold),
    }


def _para(text: str, styles: dict, key: str = "body") -> Paragraph:
    style = styles[key]
    return Paragraph(escape(_safe(text, style.fontName)), style)


def _safe(value: object, font_name: str) -> str:
    text = str(value)
    if font_name != "Helvetica" and not font_name.startswith("Helvetica"):
        return text
    return text.encode("cp1252", errors="replace").decode("cp1252")


def _meta_table(rows, styles) -> Table:
    data = [[_para(label, styles, "bold"), _para(value or "-", styles)] for label, value in rows]
    table = Table(data, colWidths=(45 * mm, 129 * mm), hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, RULE),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F6FA")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _money(value: Decimal) -> str:
    return f"{Decimal(str(value)):,.2f}"


# -- sections ---------------------------------------------------------------


def _cover(context, plan, styles, logo_asset, include_charts, environment):
    story: list[Any] = []
    logo = resolve_logo(logo_asset, environment=environment)
    if logo is not None and logo.suffix.casefold() in {".png", ".jpg", ".jpeg"}:
        try:
            story.extend([Image(str(logo), width=45 * mm, height=16 * mm), Spacer(1, 6)])
        except Exception as error:  # noqa: BLE001 - logo is optional
            LOGGER.info("Logo could not be drawn: %s", type(error).__name__)
    else:
        placeholder = Table([[_para("COMPANY LOGO", styles, "small")]], colWidths=(45 * mm,))
        placeholder.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.5, RULE),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )
        story.extend([placeholder, Spacer(1, 6)])
    story.append(_para(plan.heading("cover") or "Quotation", styles, "title"))
    if plan.cover_subtitle:
        story.append(_para(plan.cover_subtitle, styles, "subtitle"))
    story.append(
        _meta_table(
            (
                ("Customer", context.customer_name),
                ("Quotation reference", context.quotation_id),
                ("Quotation date", context.quotation_date.isoformat()),
                ("Valid until", context.validity_date.isoformat()),
                ("Currency", context.currency),
            ),
            styles,
        )
    )
    if plan.executive_summary:
        story.extend([Spacer(1, 6), _para(plan.executive_summary, styles)])
    return story


def _customer_details(context, plan, styles, *_args):
    story = [_para(plan.heading("customer_details"), styles, "heading")]
    story.append(
        _meta_table(
            (
                ("Customer", context.customer_name),
                ("Delivery location", context.delivery_location),
                ("Incoterm", context.incoterm),
                ("Delivery assumptions", context.delivery_assumption),
            ),
            styles,
        )
    )
    if plan.narrative("customer_details"):
        story.append(_para(plan.narrative("customer_details"), styles))
    return story


def _line_items(context, plan, styles, *_args):
    story = [_para(plan.heading("line_items"), styles, "heading")]
    data = [
        [
            _para("#", styles, "bold"),
            _para("Product", styles, "bold"),
            _para("Description", styles, "bold"),
            _para("Category", styles, "bold"),
            _para("Qty", styles, "bold"),
            _para("Unit price", styles, "bold"),
            _para("Subtotal", styles, "bold"),
        ]
    ]
    for item in context.line_items:
        description = item.description
        if item.is_optional:
            description = f"{description} (optional)"
        data.append(
            [
                _para(str(item.position), styles),
                _para(item.product_id, styles),
                _para(description, styles),
                _para(item.category_label, styles),
                _para(str(item.quantity), styles),
                _para(_money(item.unit_price), styles),
                _para(_money(item.extended_price), styles),
            ]
        )
    table = Table(
        data,
        colWidths=(8 * mm, 26 * mm, 54 * mm, 24 * mm, 12 * mm, 25 * mm, 25 * mm),
        hAlign="LEFT",
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, RULE),
                ("BACKGROUND", (0, 0), (-1, 0), INK),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (4, 1), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    if plan.narrative("line_items"):
        story.append(_para(plan.narrative("line_items"), styles))
    return story


def _commercial_summary(context, plan, styles, *_args):
    rows = [("Subtotal", f"{context.currency} {_money(context.subtotal)}")]
    if context.has_optional_items:
        rows.append(
            (
                "Optional items (not included in the total)",
                f"{context.currency} {_money(context.optional_total)}",
            )
        )
    rows.append(("Incoterm", context.incoterm))
    rows.append(("Quotation total", f"{context.currency} {_money(context.total)}"))
    story = [
        _para(plan.heading("commercial_summary"), styles, "heading"),
        _meta_table(rows, styles),
    ]
    if plan.narrative("commercial_summary"):
        story.append(_para(plan.narrative("commercial_summary"), styles))
    return story


def _customer_summary(context, plan, styles, *_args):
    text = plan.customer_summary or plan.narrative("customer_summary")
    if not text:
        return []
    return [
        _para(plan.heading("customer_summary"), styles, "heading"),
        _para(text, styles),
    ]


def _charts(context, plan, styles, _logo, include_charts, _environment):
    series = build_charts(context, enabled=include_charts)
    if not series:
        return []
    story = [_para(plan.heading("charts"), styles, "heading")]
    for chart in series:
        data = [[_para("Category", styles, "bold"), _para("Value", styles, "bold")]]
        data.extend(
            [_para(label, styles), _para(value, styles)]
            for label, value in chart_table_rows(chart)
        )
        table = Table(data, colWidths=(84 * mm, 90 * mm), hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, RULE),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)
        story.append(_para(plan.caption(chart.chart_id) or chart.title, styles, "small"))
    return story


def _terms(context, plan, styles, *_args):
    from app.documents.renderer import TERMS_TEXT

    story = [
        _para(plan.heading("terms"), styles, "heading"),
        _para(TERMS_TEXT, styles, "small"),
    ]
    if plan.narrative("terms"):
        story.append(_para(plan.narrative("terms"), styles, "small"))
    return story


_SECTIONS = {
    "cover": _cover,
    "customer_details": _customer_details,
    "line_items": _line_items,
    "commercial_summary": _commercial_summary,
    "customer_summary": _customer_summary,
    "charts": _charts,
    "terms": _terms,
}
