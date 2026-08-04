from __future__ import annotations

import logging
import re
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.doctemplate import LayoutError

from app.config import DEMO_QUOTATION_VALIDITY_DAYS
from app.output_context import (
    OutputGenerationError,
    build_output_context,
    format_money,
)
from app.quotation_models import DocumentOutput, QuotationWorkflowState


PDF_MIME_TYPE = "application/pdf"
LOGGER = logging.getLogger(__name__)


class DocumentGenerationError(OutputGenerationError):
    pass


def safe_quotation_filename(quotation_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", quotation_id.strip())
    safe_id = safe_id.strip(".-_") or "quotation"
    return f"{safe_id}-quotation.pdf"


def generate_quotation_pdf(
    state: QuotationWorkflowState,
    *,
    logo_path: str | Path | None = None,
    as_of: date | None = None,
    validity_days: int = DEMO_QUOTATION_VALIDITY_DAYS,
) -> DocumentOutput:
    context = build_output_context(
        state,
        require_approved=True,
        as_of=as_of,
        validity_days=validity_days,
    )
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Quotation {context.quotation_id}",
        author="Quotation Demo",
        subject=(
            f"Approved total {format_money(context.total_price, context.currency)}"
        ),
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="QuotationTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=24,
            leading=28,
            textColor=colors.HexColor("#18212F"),
            alignment=TA_CENTER,
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeading",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=colors.HexColor("#18212F"),
            spaceBefore=8,
            spaceAfter=5,
        )
    )
    story: list[Any] = []
    logo = Path(logo_path) if logo_path is not None else None
    if logo is not None and logo.is_file():
        story.extend([Image(str(logo), width=42 * mm, height=15 * mm), Spacer(1, 4)])
    elif logo is not None:
        LOGGER.info("Optional quotation logo is unavailable: %s", logo.name)
    story.append(Paragraph("QUOTATION", styles["QuotationTitle"]))

    story.append(_section("Quotation metadata", styles))
    story.append(
        _key_value_table(
            (
                ("Quotation ID", context.quotation_id),
                ("Date", context.quotation_date.isoformat()),
                ("Validity date", context.validity_date.isoformat()),
                ("Status", context.approval_status.replace("_", " ").upper()),
            ),
            styles,
        )
    )
    story.append(_section("Customer", styles))
    story.append(
        _key_value_table(
            (
                ("Customer name", context.customer_name),
                ("Delivery location", context.delivery_location),
                ("Region", context.region),
            ),
            styles,
        )
    )

    story.append(_section("Product configuration", styles))
    story.append(
        _styled_table(
            [
                [
                    _paragraph("Product ID", styles, bold=True),
                    _paragraph("Description", styles, bold=True),
                    _paragraph("Qty", styles, bold=True),
                    _paragraph("Unit price", styles, bold=True),
                    _paragraph("Subtotal", styles, bold=True),
                ],
                [
                    _paragraph(context.product_id, styles),
                    _paragraph(context.product_description, styles),
                    _paragraph(str(context.quantity), styles),
                    _paragraph(
                        format_money(context.final_unit_price, context.currency),
                        styles,
                    ),
                    _paragraph(
                        format_money(context.total_price, context.currency),
                        styles,
                    ),
                ],
            ],
            col_widths=(30 * mm, 63 * mm, 12 * mm, 32 * mm, 32 * mm),
            header=True,
        )
    )

    story.append(_section("Commercial summary", styles))
    story.append(
        _key_value_table(
            (
                ("Currency", context.currency),
                (
                    "Subtotal",
                    format_money(context.total_price, context.currency),
                ),
                ("Total", format_money(context.total_price, context.currency)),
                ("Incoterm", context.incoterm),
                ("Delivery assumption", context.delivery_assumption),
            ),
            styles,
        )
    )
    story.append(_section("Approval", styles))
    story.append(
        _key_value_table(
            (
                (
                    "Approved status",
                    context.approval_status.replace("_", " ").upper(),
                ),
                ("Approver", context.approver or "Demo approver"),
                ("Approved timestamp", context.approved_at),
            ),
            styles,
        )
    )

    story.append(_section("Terms and disclaimer", styles))
    story.append(
        _paragraph(
            "This is a demo quotation and is subject to final company "
            f"confirmation. Pricing is valid until {context.validity_date.isoformat()} "
            "unless replaced by a confirmed company quotation. Delivery remains "
            "subject to final order acceptance and scheduling.",
            styles,
        )
    )

    def canvas_maker(*args: Any, **kwargs: Any) -> canvas.Canvas:
        kwargs["pageCompression"] = 0
        pdf_canvas = canvas.Canvas(*args, **kwargs)
        pdf_canvas.setTitle(f"Quotation {_pdf_safe_text(context.quotation_id)}")
        pdf_canvas.setAuthor("Quotation Demo")
        pdf_canvas.setSubject(
            "Approved total "
            + _pdf_safe_text(
                format_money(context.total_price, context.currency)
            )
        )
        return pdf_canvas

    try:
        document.build(story, canvasmaker=canvas_maker)
    except (OSError, TypeError, ValueError, LayoutError) as error:
        raise DocumentGenerationError(
            "The quotation PDF could not be generated."
        ) from error
    return DocumentOutput(
        filename=safe_quotation_filename(context.quotation_id),
        mime_type=PDF_MIME_TYPE,
        bytes_data=buffer.getvalue(),
    )


def _section(title: str, styles) -> Paragraph:
    return Paragraph(_pdf_safe_text(title), styles["SectionHeading"])


def _paragraph(value: str, styles, *, bold: bool = False) -> Paragraph:
    text = escape(_pdf_safe_text(value))
    if bold:
        text = f"<b>{text}</b>"
    return Paragraph(text, styles["BodyText"])


def _key_value_table(rows, styles) -> Table:
    data = [
        [
            _paragraph(label, styles, bold=True),
            _paragraph(value or "-", styles),
        ]
        for label, value in rows
    ]
    return _styled_table(data, col_widths=(42 * mm, 127 * mm))


def _styled_table(data, *, col_widths, header: bool = False) -> Table:
    table = Table(data, colWidths=col_widths, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D8DEE8")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    if header:
        commands.extend(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#18212F")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ]
        )
    table.setStyle(TableStyle(commands))
    return table


def _pdf_safe_text(value: object) -> str:
    return str(value).encode("cp1252", errors="replace").decode("cp1252")
