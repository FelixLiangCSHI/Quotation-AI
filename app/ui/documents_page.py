"""Documents — the customer-facing output, kept separate from internal data.

Only approved quotations produce a customer document, and the customer
document never carries cost, margin, threshold or approval-rule information.
This page makes that boundary explicit in the interface.
"""

from __future__ import annotations

import streamlit as st

from app.auth.provider import PermissionDeniedError
from app.auth.roles import Permission
from app.services.document_service import (
    DocumentService,
    DocumentServiceError,
)

__all__ = ["render"]


def render(user) -> None:
    st.title(":material/description: Documents")
    if not (
        user.has_permission(Permission.VIEW_OWN_QUOTATIONS)
        or user.has_permission(Permission.VIEW_APPROVAL_TASKS)
    ):
        st.error(
            "Your role does not include document access.",
            icon=":material/block:",
        )
        return

    st.caption(
        "Customer output contains the approved quotation only. Cost, margin, "
        "thresholds and approval rules stay in the internal views."
    )

    quotation_id = st.text_input(
        "Quotation ID", key="documents_quotation_id"
    ).strip()
    if not quotation_id:
        st.info(
            "Enter a quotation reference to list its generated documents.",
            icon=":material/search:",
        )
        return

    service = DocumentService()
    try:
        documents = service.list_documents(quotation_id, user=user)
    except PermissionDeniedError as error:
        st.error(str(error), icon=":material/block:")
        return
    except DocumentServiceError as error:
        st.error(str(error), icon=":material/error:")
        return
    except Exception:  # noqa: BLE001 - unknown quotation or storage problem
        st.error(
            "No document could be listed for that quotation reference.",
            icon=":material/search_off:",
        )
        return

    if not documents:
        st.info(
            "No document has been generated yet. A customer document becomes "
            "available once the quotation is approved.",
            icon=":material/draft:",
        )
        return

    for metadata in documents:
        with st.container(border=True):
            st.markdown(f"**{metadata.filename}**")
            st.caption(
                f"Audience: {metadata.audience} · Type: "
                f"{metadata.document_type} · Version "
                f"{metadata.quotation_version} · Status: {metadata.status}"
            )
            st.caption(
                f"Generated {metadata.generated_at} · "
                f"{metadata.byte_size} bytes · checksum "
                f"{metadata.file_hash[:12]}"
            )
            if metadata.audience != "customer":
                st.caption(
                    "Internal document — not for customer distribution."
                )
                continue
            if st.button(
                "Prepare download",
                icon=":material/download:",
                key=f"download_{metadata.document_id}",
            ):
                _offer_download(service, user, metadata)


def _offer_download(service: DocumentService, user, metadata) -> None:
    try:
        generated = service.download_customer_document(
            metadata.document_id, user=user
        )
    except PermissionDeniedError as error:
        st.error(str(error), icon=":material/block:")
        return
    except DocumentServiceError as error:
        st.error(str(error), icon=":material/error:")
        return
    st.download_button(
        "Download customer document",
        data=generated.content,
        file_name=generated.filename,
        mime=generated.mime_type,
        key=f"download_button_{metadata.document_id}",
    )
