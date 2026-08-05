"""Template-based customer document rendering.

Architecture::

    trusted quotation context
      + validated DocumentPlan
      + approved template
      -> HTML (Jinja2, sandboxed, autoescaped)
      -> PDF (WeasyPrint or Playwright when available)
      -> PDF (ReportLab deterministic fallback, always available)

Security properties:

* templates are loaded from this package only, never from a caller-supplied
  path and never from an AI response;
* the Jinja environment is a :class:`~jinja2.sandbox.SandboxedEnvironment`
  with autoescaping, so plan text cannot execute a template expression even if
  sanitisation were bypassed;
* the HTML contains no script, no remote stylesheet, no remote font and no
  remote image, and the PDF engines are invoked with a null base URL so a
  relative or absolute reference cannot reach the filesystem or the network;
* approved images are inlined as data URIs from the controlled asset
  repository before rendering.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

from app.documents.assets import (
    FontConfiguration,
    load_font_configuration,
    resolve_logo,
)
from app.documents.charts import build_charts, render_bar_chart_svg
from app.documents.context import CustomerDocumentContext
from app.documents.plan import DocumentPlan, contains_internal_disclosure

LOGGER = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_TEMPLATE = "quotation_branded_v1.html.j2"
TEMPLATE_VERSION = "branded-v1"
PDF_MIME_TYPE = "application/pdf"

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

TERMS_TEXT = (
    "This quotation is issued subject to final company confirmation and to our "
    "standard terms and conditions of sale. Prices are quoted in the currency "
    "shown and remain valid until the validity date stated above unless "
    "replaced by a later confirmed quotation. Delivery dates are indicative "
    "and remain subject to final order acceptance, production scheduling and "
    "material availability. Taxes, duties and any charges outside the stated "
    "Incoterm are excluded unless expressly listed as a line item."
)


class DocumentRenderError(RuntimeError):
    """Raised when no rendering engine could produce a document."""


@dataclass(frozen=True)
class RenderedDocument:
    """A rendered customer document plus its non-sensitive provenance."""

    filename: str
    mime_type: str
    content: bytes
    engine: str
    template_version: str
    plan_version: str
    html: str = ""
    warnings: tuple[str, ...] = ()
    font: Mapping[str, object] = field(default_factory=dict)

    @property
    def bytes_data(self) -> bytes:
        """Backwards-compatible alias used by the email attachment path."""

        return self.content


def safe_document_filename(
    quotation_id: str, *, quotation_version: int = 1, suffix: str = "pdf"
) -> str:
    """Build an output filename that cannot escape a directory."""

    safe_id = re.sub(r"[^A-Za-z0-9_-]+", "-", str(quotation_id).strip())
    safe_id = re.sub(r"-{2,}", "-", safe_id).strip("-_")[:80] or "quotation"
    safe_suffix = re.sub(r"[^a-z0-9]+", "", str(suffix).casefold()) or "pdf"
    return f"{safe_id}-v{int(quotation_version)}-quotation.{safe_suffix}"


def _environment() -> Environment:
    env = SandboxedEnvironment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.clear()
    return env


def _money(value: Any) -> str:
    return f"{Decimal(str(value)):,.2f}"


def _logo_data_uri(logo_asset: str | None, environment) -> str:
    path = resolve_logo(logo_asset, environment=environment)
    if path is None:
        return ""
    media_type = IMAGE_MEDIA_TYPES.get(path.suffix.casefold())
    if media_type is None:
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def render_quotation_html(
    context: CustomerDocumentContext,
    plan: DocumentPlan,
    *,
    template_name: str = DEFAULT_TEMPLATE,
    include_charts: bool = True,
    logo_asset: str | None = None,
    font: FontConfiguration | None = None,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Render the approved template to HTML. Never renders a caller template."""

    if template_name != DEFAULT_TEMPLATE and not (
        TEMPLATE_DIR / template_name
    ).is_file():
        raise DocumentRenderError("Unknown document template requested.")
    font_config = font or load_font_configuration(environment)
    charts = [
        {
            "chart_id": series.chart_id,
            "title": series.title,
            "caption": plan.caption(series.chart_id),
            "svg": _markup(render_bar_chart_svg(series)),
        }
        for series in build_charts(context, enabled=include_charts)
    ]
    template = _environment().get_template(template_name)
    return template.render(
        context=context,
        plan=plan,
        charts=charts,
        money=_money,
        terms_text=TERMS_TEXT,
        template_version=TEMPLATE_VERSION,
        font_family=font_config.family,
        logo_data_uri=_logo_data_uri(logo_asset, environment),
    )


def _markup(value: str):
    from markupsafe import Markup

    return Markup(value)


# -- PDF engines ------------------------------------------------------------


def available_pdf_engines() -> tuple[str, ...]:
    """Engines usable in this deployment, best first. Never raises."""

    engines: list[str] = []
    if _module_available("weasyprint"):
        engines.append("weasyprint")
    if _module_available("playwright.sync_api"):
        engines.append("playwright")
    engines.append("reportlab")
    return tuple(engines)


def _module_available(name: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover - import machinery
        return False


def _render_with_weasyprint(html: str) -> bytes:
    from weasyprint import HTML  # type: ignore import-not-found

    # base_url=None keeps every relative reference unresolvable, so the
    # renderer cannot read a local file or reach the network.
    return HTML(string=html, base_url=None).write_pdf()


def _render_with_playwright(html: str) -> bytes:
    from playwright.sync_api import sync_playwright  # type: ignore

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--disable-web-security=false"])
        try:
            page = browser.new_page()
            # Refuse every network request: the document is fully self-contained.
            page.route("**/*", lambda route: route.abort())
            page.set_content(html, wait_until="load")
            return page.pdf(
                format="A4",
                print_background=True,
                margin={
                    "top": "16mm",
                    "bottom": "20mm",
                    "left": "18mm",
                    "right": "18mm",
                },
            )
        finally:
            browser.close()


def render_quotation_pdf(
    context: CustomerDocumentContext,
    plan: DocumentPlan,
    *,
    include_charts: bool = True,
    logo_asset: str | None = None,
    preferred_engines: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> RenderedDocument:
    """Render the approved customer PDF, degrading engine by engine."""

    font_config = load_font_configuration(environment)
    html = render_quotation_html(
        context,
        plan,
        include_charts=include_charts,
        logo_asset=logo_asset,
        font=font_config,
        environment=environment,
    )
    _assert_customer_safe(html)

    warnings: list[str] = []
    if font_config.fallback_used:
        warnings.append(font_config.fallback_reason)
    engines = tuple(preferred_engines or available_pdf_engines())
    content: bytes | None = None
    used_engine = ""
    for engine in engines:
        try:
            if engine == "weasyprint":
                content = _render_with_weasyprint(html)
            elif engine == "playwright":
                content = _render_with_playwright(html)
            elif engine == "reportlab":
                from app.documents.reportlab_fallback import (
                    render_quotation_pdf_reportlab,
                )

                content = render_quotation_pdf_reportlab(
                    context, plan, font=font_config, logo_asset=logo_asset,
                    include_charts=include_charts, environment=environment,
                )
            else:
                continue
        except Exception as error:  # noqa: BLE001 - try the next engine
            LOGGER.info("PDF engine %s unavailable: %s", engine, type(error).__name__)
            warnings.append(f"{engine} unavailable ({type(error).__name__})")
            continue
        if content:
            used_engine = engine
            break
    if not content:
        raise DocumentRenderError("No PDF rendering engine produced a document.")
    return RenderedDocument(
        filename=safe_document_filename(
            context.quotation_id, quotation_version=context.quotation_version
        ),
        mime_type=PDF_MIME_TYPE,
        content=content,
        engine=used_engine,
        template_version=TEMPLATE_VERSION,
        plan_version=plan.plan_version,
        html=html,
        warnings=tuple(warnings),
        font=font_config.describe(),
    )


def _assert_customer_safe(html: str) -> None:
    """Last-line check before any engine sees the markup."""

    if "<script" in html.casefold() or "javascript:" in html.casefold():
        raise DocumentRenderError("Rendered document contains executable content.")
    body = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    body = re.sub(r"<[^>]*>", " ", body)
    if contains_internal_disclosure(body):
        raise DocumentRenderError(
            "Rendered document would disclose internal-only information."
        )
