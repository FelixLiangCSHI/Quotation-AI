"""Optional customer-safe charts.

Charts are built from the trusted customer context only. They can express
quotation composition by item category, a quantity breakdown and the
product/service composition. They can never express internal cost, gross
margin, comparable prices, price floors or a policy threshold, because those
values do not exist in :class:`~app.documents.context.CustomerDocumentContext`.

The charts are generated as plain SVG markup by this module, not by a
template and never by an AI agent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence
from xml.sax.saxutils import escape

BAR_COLOURS = ("#18212F", "#2F4A6D", "#4C7BA6", "#7FA8C9", "#B3CBE0")
CHART_WIDTH = 520
BAR_HEIGHT = 22
BAR_GAP = 10
LABEL_WIDTH = 170


@dataclass(frozen=True)
class ChartSeries:
    """A customer-safe chart: labels plus non-negative magnitudes."""

    chart_id: str
    title: str
    labels: tuple[str, ...]
    values: tuple[Decimal, ...]
    value_labels: tuple[str, ...]

    @property
    def is_renderable(self) -> bool:
        return bool(self.labels) and any(value > 0 for value in self.values)


def category_composition_chart(context) -> ChartSeries:
    """Revenue share by customer-visible item category."""

    pairs = context.category_composition()
    return ChartSeries(
        chart_id="category_composition",
        title="Quotation composition by item category",
        labels=tuple(label for label, _ in pairs),
        values=tuple(Decimal(value) for _, value in pairs),
        value_labels=tuple(
            f"{context.currency} {Decimal(value):,.2f}" for _, value in pairs
        ),
    )


def quantity_breakdown_chart(context) -> ChartSeries:
    """Quantity by customer-visible item category."""

    pairs = context.quantity_breakdown()
    return ChartSeries(
        chart_id="quantity_breakdown",
        title="Quantity breakdown",
        labels=tuple(label for label, _ in pairs),
        values=tuple(Decimal(value) for _, value in pairs),
        value_labels=tuple(str(value) for _, value in pairs),
    )


def build_charts(context, *, enabled: bool = True) -> tuple[ChartSeries, ...]:
    if not enabled:
        return ()
    charts = (
        category_composition_chart(context),
        quantity_breakdown_chart(context),
    )
    return tuple(chart for chart in charts if chart.is_renderable)


def render_bar_chart_svg(series: ChartSeries) -> str:
    """Render ``series`` as a self-contained, script-free SVG bar chart."""

    if not series.is_renderable:
        return ""
    maximum = max(series.values)
    rows: list[str] = []
    height = len(series.labels) * (BAR_HEIGHT + BAR_GAP) + BAR_GAP
    plot_width = CHART_WIDTH - LABEL_WIDTH - 90
    for index, (label, value, value_label) in enumerate(
        zip(series.labels, series.values, series.value_labels)
    ):
        top = BAR_GAP + index * (BAR_HEIGHT + BAR_GAP)
        width = 0 if maximum <= 0 else int(plot_width * float(value / maximum))
        colour = BAR_COLOURS[index % len(BAR_COLOURS)]
        rows.append(
            f'<text x="0" y="{top + 15}" font-size="11" fill="#18212F">'
            f"{escape(_truncate(label))}</text>"
            f'<rect x="{LABEL_WIDTH}" y="{top}" width="{max(width, 1)}" '
            f'height="{BAR_HEIGHT}" fill="{colour}" rx="3" />'
            f'<text x="{LABEL_WIDTH + max(width, 1) + 6}" y="{top + 15}" '
            f'font-size="11" fill="#18212F">{escape(value_label)}</text>'
        )
    body = "".join(rows)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CHART_WIDTH}" '
        f'height="{height}" viewBox="0 0 {CHART_WIDTH} {height}" '
        f'role="img" aria-label="{escape(series.title)}">{body}</svg>'
    )


def chart_table_rows(series: ChartSeries) -> Sequence[tuple[str, str]]:
    """Accessible textual equivalent used by the deterministic fallback."""

    return tuple(zip(series.labels, series.value_labels))


def _truncate(label: str, limit: int = 26) -> str:
    text = str(label).strip()
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
