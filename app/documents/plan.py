"""The strict, validated and sanitised DocumentPlan for Agent 4.

Agent 4 *may* propose:

* a cover subtitle,
* a customer-safe executive summary,
* section ordering,
* customer-safe explanatory text,
* chart captions,
* a layout recommendation.

Agent 4 *must not* create or alter the quotation identifier or version, the
customer identity, product identifiers, quantities, unit prices, total prices,
the currency, the validity date, the Incoterm, delivery assumptions, the
approval status, the approver identity or any policy status. None of those
values can be expressed through the plan schema, and the renderer reads them
from :mod:`app.documents.context` only.

Every string that survives validation is sanitised before it can reach a
template: HTML tags, script content, template expressions, URLs and filesystem
paths are removed, never escaped-and-kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from app.agents.schemas import Agent4DocumentPlanResponse
from app.config import CUSTOMER_PROHIBITED_FIELDS

#: Version of the plan contract itself. Persisted with every document.
DOCUMENT_PLAN_VERSION = "documentplan-v1"

#: The closed set of sections a customer quotation document may contain.
DEFAULT_SECTION_IDS: tuple[str, ...] = (
    "cover",
    "customer_details",
    "line_items",
    "commercial_summary",
    "customer_summary",
    "charts",
    "terms",
)

DEFAULT_SECTION_HEADINGS: Mapping[str, str] = {
    "cover": "Quotation",
    "customer_details": "Customer and delivery",
    "line_items": "Products and configuration",
    "commercial_summary": "Commercial summary",
    "customer_summary": "Summary",
    "charts": "Quotation composition",
    "terms": "Terms and disclaimer",
}

#: The closed set of charts a customer document may render.
ALLOWED_CHART_IDS: tuple[str, ...] = (
    "category_composition",
    "quantity_breakdown",
)

#: Layout names the template understands. Anything else falls back.
ALLOWED_LAYOUTS: tuple[str, ...] = ("standard", "compact")

MAX_SUMMARY_LENGTH = 1200
MAX_NARRATIVE_LENGTH = 600
MAX_CAPTION_LENGTH = 160
MAX_SUBTITLE_LENGTH = 120


class DocumentPlanError(ValueError):
    """Raised when a proposed plan cannot be made safe."""


# -- sanitisation -----------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]*>")
_TEMPLATE_RE = re.compile(r"(\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}|\$\{.*?\})", re.S)
_URL_RE = re.compile(r"(?i)\b(?:https?|ftp|file|data|javascript|vbscript)\s*:\S*")
_WINDOWS_PATH_RE = re.compile(r"(?i)\b[a-z]:\\\\?[^\s]*")
_UNC_PATH_RE = re.compile(r"\\\\[^\s]+")
_POSIX_PATH_RE = re.compile(r"(?<![\w.])(?:\.{1,2})?/[\w.\-]+(?:/[\w.\-]+)+")
_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[ \t]+")

#: Terms that must never appear in a customer document, whatever the source.
CUSTOMER_FORBIDDEN_TERMS: tuple[str, ...] = (
    "gross margin",
    "margin",
    "estimated cost",
    "cost of goods",
    "cogs",
    "unit cost",
    "threshold",
    "35%",
    "policy version",
    "commercialpolicyversion",
    "rule id",
    "rule_id",
    "override",
    "justification",
    "rejection note",
    "revision note",
    "workbook",
    "worksheet",
    "data source cell",
    "comparable",
    "price floor",
    "minimum price",
    "prompt",
    "system prompt",
    "internal only",
    "internal note",
)


def sanitize_plan_text(value: object, *, max_length: int) -> str:
    """Return plain, template-safe text or an empty string.

    The function strips rather than escapes: an AI-proposed string can never
    become executable HTML, JavaScript, a template expression, a URL or a
    filesystem path in the rendered document.
    """

    if value is None:
        return ""
    text = str(value)
    text = _CONTROL_RE.sub(" ", text)
    text = _TEMPLATE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    text = _URL_RE.sub(" ", text)
    text = _WINDOWS_PATH_RE.sub(" ", text)
    text = _UNC_PATH_RE.sub(" ", text)
    text = _POSIX_PATH_RE.sub(" ", text)
    text = text.replace("<", " ").replace(">", " ").replace("&", " and ")
    text = text.replace("{", " ").replace("}", " ").replace("\\", " ")
    lines = [
        _WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()
    ]
    text = "\n".join(line for line in lines if line).strip()
    if len(text) > max_length:
        text = text[:max_length].rstrip()
    return text


def contains_internal_disclosure(text: str) -> bool:
    """True when ``text`` mentions an internal-only concept."""

    lowered = text.casefold()
    if any(term in lowered for term in CUSTOMER_FORBIDDEN_TERMS):
        return True
    for name in CUSTOMER_PROHIBITED_FIELDS:
        phrase = name.replace("_", " ")
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            return True
    return False


def _safe_or_empty(value: object, *, max_length: int) -> str:
    text = sanitize_plan_text(value, max_length=max_length)
    return "" if contains_internal_disclosure(text) else text


# -- the validated plan -----------------------------------------------------


@dataclass(frozen=True)
class DocumentSection:
    section_id: str
    heading: str
    narrative: str = ""


@dataclass(frozen=True)
class DocumentPlan:
    """A validated, sanitised, render-ready plan."""

    sections: tuple[DocumentSection, ...]
    cover_subtitle: str = ""
    executive_summary: str = ""
    customer_summary: str = ""
    chart_captions: Mapping[str, str] = field(default_factory=dict)
    layout: str = "standard"
    plan_version: str = DOCUMENT_PLAN_VERSION
    ai_generated: bool = False
    provider: str = "deterministic"
    fallback_used: bool = True
    fallback_reason: str = ""

    @property
    def section_ids(self) -> tuple[str, ...]:
        return tuple(section.section_id for section in self.sections)

    def heading(self, section_id: str) -> str:
        for section in self.sections:
            if section.section_id == section_id:
                return section.heading
        return DEFAULT_SECTION_HEADINGS.get(section_id, section_id.title())

    def narrative(self, section_id: str) -> str:
        for section in self.sections:
            if section.section_id == section_id:
                return section.narrative
        return ""

    def caption(self, chart_id: str) -> str:
        return self.chart_captions.get(chart_id, "")

    def describe(self) -> dict[str, object]:
        return {
            "plan_version": self.plan_version,
            "layout": self.layout,
            "sections": list(self.section_ids),
            "ai_generated": self.ai_generated,
            "provider": self.provider,
            "fallback_used": self.fallback_used,
            "fallback_reason": self.fallback_reason,
        }


def deterministic_document_plan(
    *,
    section_ids: Sequence[str] = DEFAULT_SECTION_IDS,
    customer_summary: str = "",
    provider: str = "deterministic",
    fallback_reason: str = "",
) -> DocumentPlan:
    """The always-available plan. Never depends on an AI provider."""

    allowed = [
        section_id for section_id in section_ids if section_id in DEFAULT_SECTION_IDS
    ] or list(DEFAULT_SECTION_IDS)
    sections = tuple(
        DocumentSection(
            section_id=section_id,
            heading=DEFAULT_SECTION_HEADINGS.get(section_id, section_id.title()),
        )
        for section_id in allowed
    )
    return DocumentPlan(
        sections=sections,
        customer_summary=_safe_or_empty(
            customer_summary, max_length=MAX_SUMMARY_LENGTH
        ),
        layout="standard",
        provider=provider,
        ai_generated=False,
        fallback_used=True,
        fallback_reason=fallback_reason,
    )


def build_document_plan(
    response: Agent4DocumentPlanResponse | None,
    *,
    allowed_section_ids: Sequence[str] = DEFAULT_SECTION_IDS,
    provider: str = "deterministic",
    ai_generated: bool = False,
    fallback_reason: str = "",
) -> DocumentPlan:
    """Validate and sanitise an Agent 4 proposal into a safe plan.

    Any section that is unknown, duplicated or missing causes the whole plan
    to fall back to the deterministic plan. Text that fails sanitisation is
    dropped field by field rather than rendered.
    """

    allowed = tuple(
        section_id
        for section_id in allowed_section_ids
        if section_id in DEFAULT_SECTION_IDS
    ) or DEFAULT_SECTION_IDS
    if response is None:
        return deterministic_document_plan(
            section_ids=allowed,
            provider=provider,
            fallback_reason=fallback_reason or "no plan proposed",
        )

    proposed = [section.section_id for section in response.sections]
    if len(set(proposed)) != len(proposed) or set(proposed) != set(allowed):
        return deterministic_document_plan(
            section_ids=allowed,
            provider=provider,
            fallback_reason="proposed sections do not match the approved set",
        )

    sections = tuple(
        DocumentSection(
            section_id=section.section_id,
            heading=_safe_or_empty(section.heading, max_length=120)
            or DEFAULT_SECTION_HEADINGS.get(
                section.section_id, section.section_id.title()
            ),
            narrative=_safe_or_empty(
                section.narrative, max_length=MAX_NARRATIVE_LENGTH
            ),
        )
        for section in response.sections
    )
    captions = {}
    for caption in response.chart_captions:
        if caption.chart_id not in ALLOWED_CHART_IDS:
            continue
        text = _safe_or_empty(caption.caption, max_length=MAX_CAPTION_LENGTH)
        if text:
            captions[caption.chart_id] = text
    layout = response.layout_recommendation.strip().casefold()
    if layout not in ALLOWED_LAYOUTS:
        layout = "standard"
    return DocumentPlan(
        sections=sections,
        cover_subtitle=_safe_or_empty(
            response.cover_subtitle, max_length=MAX_SUBTITLE_LENGTH
        ),
        executive_summary=_safe_or_empty(
            response.executive_summary, max_length=MAX_SUMMARY_LENGTH
        ),
        customer_summary=_safe_or_empty(
            response.customer_safe_summary, max_length=MAX_SUMMARY_LENGTH
        ),
        chart_captions=captions,
        layout=layout,
        provider=provider,
        ai_generated=bool(ai_generated),
        fallback_used=not ai_generated,
        fallback_reason=fallback_reason,
    )


def plan_text_values(plan: DocumentPlan) -> Iterable[str]:
    """Every free-text value in the plan, for leakage regression tests."""

    yield plan.cover_subtitle
    yield plan.executive_summary
    yield plan.customer_summary
    for section in plan.sections:
        yield section.heading
        yield section.narrative
    yield from plan.chart_captions.values()
