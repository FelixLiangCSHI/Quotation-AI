"""Typed domain layer: DTOs and the workflow-state codec."""

from __future__ import annotations

from app.domain.dto import (
    PRODUCT_LINE_ITEM_TYPES,
    AuditEventDTO,
    LineItemDTO,
    LineItemType,
    QuotationDTO,
    QuotationSummaryDTO,
    UserDTO,
)
from app.domain.workflow_state_codec import (
    STATE_SCHEMA_VERSION,
    WorkflowStateCodecError,
    dump_workflow_state,
    load_workflow_state,
)

__all__ = [
    "PRODUCT_LINE_ITEM_TYPES",
    "STATE_SCHEMA_VERSION",
    "AuditEventDTO",
    "LineItemDTO",
    "LineItemType",
    "QuotationDTO",
    "QuotationSummaryDTO",
    "UserDTO",
    "WorkflowStateCodecError",
    "dump_workflow_state",
    "load_workflow_state",
]
