"""Single-page AI Quotation Assistant demo.

Run locally with ``streamlit run demo_app.py`` (or point the Streamlit
Community Cloud main file at ``demo_app.py``).

Everything is local: the requirement parsing and configuration matching reuse
the existing offline modules, all state lives in ``st.session_state`` and the
data is synthetic. Only the discount rate drives the approval decision — cost,
COGS, margin and profit are intentionally not part of this page.
"""

from __future__ import annotations

import uuid

import pandas as pd
import streamlit as st

from app.demo_assistant import GREETING, DemoQuotationAssistant
from app.demo_quotation import (
    DISCOUNT_APPROVAL_THRESHOLD,
    approval_status,
    build_approval_description,
    build_customer_pdf,
    build_quotation_excel,
    build_quotation_lines,
    compute_totals,
    empty_configuration,
    format_money,
    with_line_totals,
)

EDITABLE_COLUMNS = ("Quantity", "Quotation Unit Price")


def _configure_page() -> None:
    st.set_page_config(
        page_title="AI Quotation Assistant",
        page_icon=":material/request_quote:",
        layout="wide",
    )


@st.cache_resource(show_spinner=False)
def _assistant() -> DemoQuotationAssistant:
    return DemoQuotationAssistant()


def _init_state() -> None:
    state = st.session_state
    state.setdefault(
        "messages", [{"role": "assistant", "content": GREETING}]
    )
    state.setdefault("configuration", empty_configuration())
    state.setdefault("quotation_lines", [])
    state.setdefault("quotation_id", f"Q-DEMO-{uuid.uuid4().hex[:6].upper()}")
    state.setdefault("manager_approval_status", "NOT_SUBMITTED")


def _reset_manager_approval() -> None:
    """A changed discount invalidates any previous manager decision."""

    st.session_state["manager_approval_status"] = "NOT_SUBMITTED"


def _render_chat() -> None:
    st.subheader("Sales conversation")
    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Describe what the customer needs")
    if not prompt:
        return

    st.session_state["messages"].append({"role": "user", "content": prompt})
    result = _assistant().handle_message(
        prompt, st.session_state["configuration"]
    )
    st.session_state["configuration"] = result.configuration
    st.session_state["messages"].append(
        {"role": "assistant", "content": result.reply}
    )

    if result.configuration_ready and result.discount_rate is not None:
        st.session_state["quotation_lines"] = build_quotation_lines(
            result.configuration, result.discount_rate
        )
        _reset_manager_approval()
    st.rerun()


def _render_configuration_summary(config: dict) -> None:
    st.subheader("Configuration summary")
    if not config.get("main_product"):
        st.info(
            "No configuration yet. Describe the customer requirement in the "
            "chat on the left.",
            icon=":material/chat:",
        )
        return

    left, right = st.columns(2)
    left.markdown(f"**Customer**\n\n{config.get('customer_name') or '—'}")
    left.markdown(f"**Region**\n\n{config.get('region') or '—'}")
    left.markdown(f"**Currency**\n\n{config.get('currency') or '—'}")
    main_product = config.get("main_product") or "—"
    main_description = config.get("main_product_description") or ""
    right.markdown(f"**Main product**\n\n{main_product} {main_description}")
    right.markdown(f"**Quantity**\n\n{config.get('quantity') or '—'}")
    accessories = config.get("accessories") or []
    right.markdown(
        "**Accessories**\n\n"
        + (
            ", ".join(item.get("description", "") for item in accessories)
            if accessories
            else "—"
        )
    )
    st.markdown(
        "**Configuration description**\n\n"
        f"{config.get('configuration_description') or '—'}"
    )


def _lines_to_frame(lines: list[dict]) -> pd.DataFrame:
    rows = [with_line_totals(line) for line in lines]
    return pd.DataFrame(
        {
            "Product Code": [row["product_code"] for row in rows],
            "Description": [row["description"] for row in rows],
            "Quantity": [row["quantity"] for row in rows],
            "List Unit Price": [row["list_unit_price"] for row in rows],
            "Quotation Unit Price": [
                row["quotation_unit_price"] for row in rows
            ],
            "List Total": [row["list_line_total"] for row in rows],
            "Quotation Total": [row["quotation_line_total"] for row in rows],
        }
    )


def _frame_to_lines(frame: pd.DataFrame, lines: list[dict]) -> list[dict]:
    updated: list[dict] = []
    for index, line in enumerate(lines):
        if index >= len(frame):
            break
        row = frame.iloc[index]
        merged = dict(line)
        merged["quantity"] = int(row["Quantity"] or 0)
        merged["quotation_unit_price"] = float(row["Quotation Unit Price"] or 0)
        updated.append(with_line_totals(merged))
    return updated


def _render_quotation_table() -> list[dict]:
    lines = st.session_state["quotation_lines"]
    st.subheader("Quotation table")
    if not lines:
        st.info(
            "The quotation appears here once the configuration and the "
            "discount rate are confirmed in the chat.",
            icon=":material/table_view:",
        )
        return []

    disabled = [
        column
        for column in _lines_to_frame(lines).columns
        if column not in EDITABLE_COLUMNS
    ]
    edited = st.data_editor(
        _lines_to_frame(lines),
        key="quotation_editor",
        hide_index=True,
        use_container_width=True,
        disabled=disabled,
        column_config={
            "List Unit Price": st.column_config.NumberColumn(format="%.2f"),
            "Quotation Unit Price": st.column_config.NumberColumn(
                format="%.2f", min_value=0.0
            ),
            "Quantity": st.column_config.NumberColumn(min_value=0, step=1),
            "List Total": st.column_config.NumberColumn(format="%.2f"),
            "Quotation Total": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    updated = _frame_to_lines(edited, lines)
    if updated != lines:
        st.session_state["quotation_lines"] = updated
        # Editing the quotation only resets the manager decision; the
        # conversation and the configuration stay untouched.
        _reset_manager_approval()
        st.rerun()
    return updated


def _render_approval(config: dict, lines: list[dict]) -> None:
    st.subheader("Discount approval")
    if not lines:
        st.caption("No quotation to evaluate yet.")
        return

    totals = compute_totals(lines)
    status = approval_status(totals["discount_rate"])
    currency = config.get("currency") or "USD"

    first, second, third = st.columns(3)
    first.metric("List total", format_money(currency, totals["list_total"]))
    second.metric(
        "Quotation total", format_money(currency, totals["quotation_total"])
    )
    third.metric("Discount rate", f"{totals['discount_rate']:.1%}")
    st.caption(f"Approval threshold: {DISCOUNT_APPROVAL_THRESHOLD:.1%}")

    manager_status = st.session_state["manager_approval_status"]
    if status == "AUTO_APPROVED":
        st.success(
            "**Automatically approved** — the discount rate is within the "
            f"{DISCOUNT_APPROVAL_THRESHOLD:.0%} Sales approval authority.",
            icon=":material/check_circle:",
        )
    else:
        st.warning(
            "**Manager approval required** — the discount rate exceeds the "
            f"{DISCOUNT_APPROVAL_THRESHOLD:.0%} Sales approval authority.",
            icon=":material/gavel:",
        )
        _render_approval_request(config, lines, totals, manager_status)

    _render_outputs(config, lines, totals, status)


def _render_approval_request(
    config: dict, lines: list[dict], totals: dict, manager_status: str
) -> None:
    quotation_id = st.session_state["quotation_id"]
    st.text_area(
        "Approval description",
        value=build_approval_description(quotation_id, config, totals),
        height=320,
    )

    if st.button("Send for Approval — Demo", type="primary"):
        st.session_state["manager_approval_status"] = "PENDING"
        st.rerun()

    if manager_status == "PENDING":
        st.info(
            "Approval request submitted\n\nApprover: Sales Director\n\n"
            "Status: Pending approval",
            icon=":material/hourglass_top:",
        )
    elif manager_status == "APPROVED":
        st.success("Approved by Sales Director", icon=":material/verified:")
    elif manager_status == "REVISION_REQUESTED":
        st.warning(
            "Revision requested by Sales Director", icon=":material/edit_note:"
        )
    elif manager_status == "REJECTED":
        st.error(
            "Quotation rejected by Sales Director", icon=":material/block:"
        )

    with st.expander("Manager Demo Controls"):
        approve, revise, reject = st.columns(3)
        if approve.button("Approve", use_container_width=True):
            st.session_state["manager_approval_status"] = "APPROVED"
            st.rerun()
        if revise.button("Request Revision", use_container_width=True):
            st.session_state["manager_approval_status"] = "REVISION_REQUESTED"
            st.rerun()
        if reject.button("Reject", use_container_width=True):
            st.session_state["manager_approval_status"] = "REJECTED"
            st.rerun()


def _render_outputs(
    config: dict, lines: list[dict], totals: dict, status: str
) -> None:
    st.subheader("Output actions")
    quotation_id = st.session_state["quotation_id"]
    internal = status != "AUTO_APPROVED"
    workbook = build_quotation_excel(
        quotation_id, config, lines, totals, internal=internal
    )
    excel_label = (
        "Download Internal Approval Excel"
        if internal
        else "Download Quotation Excel"
    )
    prefix = "Approval_" if internal else "Quotation_"

    excel_column, pdf_column = st.columns(2)
    excel_column.download_button(
        excel_label,
        data=workbook,
        file_name=f"{prefix}{quotation_id}.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
    )

    pdf_unlocked = (
        status == "AUTO_APPROVED"
        or st.session_state["manager_approval_status"] == "APPROVED"
    )
    if pdf_unlocked:
        pdf_column.download_button(
            "Download Customer PDF",
            data=build_customer_pdf(quotation_id, config, lines, totals),
            file_name=f"Quotation_{quotation_id}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )
    else:
        pdf_column.button(
            "Download Customer PDF",
            disabled=True,
            use_container_width=True,
        )
        pdf_column.caption(
            "The customer PDF will be available after manager approval."
        )


def main() -> None:
    _configure_page()
    _init_state()

    st.title("AI Quotation Assistant")
    st.caption(
        "Configure products, prepare quotations and check discount approval."
    )

    chat_column, quotation_column = st.columns([1, 1.4], gap="large")
    with chat_column:
        _render_chat()
    with quotation_column:
        config = st.session_state["configuration"]
        _render_configuration_summary(config)
        st.divider()
        lines = _render_quotation_table()
        st.divider()
        _render_approval(config, lines)


main()
