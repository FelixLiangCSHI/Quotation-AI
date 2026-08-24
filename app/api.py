from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.data_loader import (
    QuotationSnapshot,
    default_snapshot_path,
    load_snapshot,
    synthetic_snapshot_path,
)
from app.natural_language import parse_quote_request
from app.quotation_models import QuotationDraft
from app.recommender import QuoteRecommender, render_recommendation_text
from app.requirement_fields import RequirementValidationError, validate_field
from app.requirement_intake import (
    RequirementCandidate,
    confirm_pending,
    merge_candidates,
    pending_confirmations,
)
from app.rule_engine import QuotationRuleEngine
from app.serialization import to_customer_jsonable, to_jsonable


REGION_VALUES = {
    "canada": "canada",
    "ca": "canada",
    "china": "china",
    "prc": "china",
    "eu": "eu",
    "europe": "eu",
    "italy": "italy",
    "italia": "italy",
    "it": "italy",
    "other": "other",
    "us": "us",
    "usa": "us",
    "u.s.": "us",
    "united states": "us",
}


class RecommendRequest(BaseModel):
    message: str = Field(..., min_length=1)
    region: str | None = Field(default=None, min_length=1, max_length=30)
    max_accessories: int | None = Field(default=None, ge=1, le=200)


class RecommendResponse(BaseModel):
    answer: str
    recommendation: dict[str, Any]


app = FastAPI(title="Quotation Bot API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def get_snapshot() -> QuotationSnapshot:
    path = default_snapshot_path()
    if not path.exists():
        path = synthetic_snapshot_path()
    return load_snapshot(path)


@lru_cache(maxsize=1)
def get_rule_engine() -> QuotationRuleEngine:
    return QuotationRuleEngine(get_snapshot())


@lru_cache(maxsize=1)
def get_recommender() -> QuoteRecommender:
    return QuoteRecommender(snapshot=get_snapshot())


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status")
def status() -> dict[str, Any]:
    """Operational status. Contains no credential or connection string."""

    from app.operations.status import status_report

    return status_report()


@app.get("/status/{component}")
def component_status(component: str) -> dict[str, Any]:
    from app.operations.status import status_report

    report = status_report()
    entry = report["components"].get(component)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown status component")
    return entry


@app.post("/recommend", response_model=RecommendResponse)
def recommend(request: RecommendRequest) -> RecommendResponse:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="message cannot be blank")

    quote_request = parse_quote_request(message)
    region = _normalize_region(request.region)
    if region:
        quote_request = replace(quote_request, region=region)

    recommendation = get_recommender().recommend(
        quote_request,
        max_accessories=request.max_accessories,
    )
    return RecommendResponse(
        answer=render_recommendation_text(recommendation),
        recommendation=to_customer_jsonable(recommendation),
    )


def _normalize_region(region: str | None) -> str | None:
    if region is None:
        return None
    normalized = region.strip().casefold()
    if not normalized:
        return None
    canonical = REGION_VALUES.get(normalized)
    if canonical is None:
        raise HTTPException(status_code=422, detail=f"unsupported region: {region}")
    return canonical

# ---------------------------------------------------------------------------
# Tool 1: search_decision_tree
# ---------------------------------------------------------------------------


@app.get("/api/v1/decision-tree/search")
def search_decision_tree(
    query: str = Query(..., min_length=1, description="检索关键词，如产品线、型号或选项名"),
    product_id: str | None = Query(default=None, description="限定某一产品 ID 内检索"),
    option_group: str | None = Query(default=None, description="限定选项组"),
    limit: int = Query(default=10, ge=1, le=50, description="最多返回节点数"),
) -> dict[str, Any]:
    snapshot = get_snapshot()
    needle = query.strip().casefold()
    if not needle:
        raise HTTPException(status_code=422, detail="query cannot be blank")

    wanted_product = product_id.strip() if product_id else None
    wanted_group = option_group.strip().casefold() if option_group else None

    matched = []
    for option in snapshot.step_options:
        if wanted_product and option.product_id != wanted_product:
            continue
        if wanted_group and (option.option_group or "").casefold() != wanted_group:
            continue
        product = snapshot.products_by_id.get(option.product_id)
        haystack = " ".join(
            part
            for part in (
                option.step_id,
                option.product_id,
                option.option_group,
                option.short_description,
                option.raw_constraint_text,
                product.system_family if product else None,
                product.short_description if product else None,
            )
            if part
        ).casefold()
        if needle in haystack:
            matched.append(option)

    nodes = [
        {
            "step_id": option.step_id,
            "product_id": option.product_id,
            "option_group": option.option_group,
            "short_description": option.short_description,
            "raw_constraint_text": option.raw_constraint_text,
        }
        for option in matched[:limit]
    ]
    matched_product_ids = {option.product_id for option in matched}
    matched_step_ids = {option.step_id for option in matched if option.step_id}
    rule_signals = [
        {
            "rule_id": signal.rule_id,
            "message": signal.message,
            "strength": signal.strength,
        }
        for signal in snapshot.rule_signals
        if (signal.product_id and signal.product_id in matched_product_ids)
        or (signal.step_id and signal.step_id in matched_step_ids)
        or (signal.applies_to_step_id and signal.applies_to_step_id in matched_step_ids)
    ]
    return {"total": len(matched), "nodes": nodes, "rule_signals": rule_signals}


# ---------------------------------------------------------------------------
# Tool 3: validate_configuration
# ---------------------------------------------------------------------------


class ConfigurationValidateRequest(BaseModel):
    product_ids: list[str] = Field(..., description="待校验的产品 ID 列表")
    region: str | None = None
    system_family: str | None = None
    acquisition_type: str | None = None
    tube_stand_id: str | None = None
    wallstand_id: str | None = None
    table_id: str | None = None
    grid_id: str | None = None
    grid_position: str | None = None
    detector_type: str | None = None
    generator: str | None = None
    tube_spec: str | None = None
    spec_category: str | None = None


@app.post("/api/v1/configuration/validate")
def validate_configuration(request: ConfigurationValidateRequest) -> dict[str, Any]:
    result = get_rule_engine().check_configuration(
        product_ids=request.product_ids,
        region=request.region,
        system_family=request.system_family,
        acquisition_type=request.acquisition_type,
        tube_stand_id=request.tube_stand_id,
        wallstand_id=request.wallstand_id,
        table_id=request.table_id,
        grid_id=request.grid_id,
        grid_position=request.grid_position,
        detector_type=request.detector_type,
        generator=request.generator,
        tube_spec=request.tube_spec,
        spec_category=request.spec_category,
    )
    issues = [
        {
            "severity": issue.severity,
            "code": issue.code,
            "message": issue.message,
            "product_id": issue.product_id,
            "rule_id": issue.rule_id,
        }
        for issue in result.issues
    ]
    passed = not any(issue.severity == "error" for issue in result.issues)
    return {
        "passed": passed,
        "issues": issues,
        "missing_fields": list(result.missing_fields),
    }


# ---------------------------------------------------------------------------
# Tool 4: validate_requirement_fields
# ---------------------------------------------------------------------------


class RequirementCandidateInput(BaseModel):
    field_name: str = Field(..., min_length=1)
    value: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class RequirementValidateRequest(BaseModel):
    candidates: list[RequirementCandidateInput] = Field(..., min_length=1)


@app.post("/api/v1/requirements/validate")
def validate_requirement_fields(request: RequirementValidateRequest) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in request.candidates:
        try:
            value = validate_field(candidate.field_name, candidate.value)
        except RequirementValidationError as error:
            rejected.append(
                {
                    "field_name": candidate.field_name,
                    "raw_value": candidate.value,
                    "reason": str(error),
                }
            )
            continue
        accepted.append(
            {
                "field_name": candidate.field_name,
                "value": to_jsonable(value),
                "confidence": candidate.confidence,
            }
        )
    return {"accepted": accepted, "rejected": rejected}


# ---------------------------------------------------------------------------
# Tool 5: merge_requirements
# ---------------------------------------------------------------------------

# In-memory drafts keyed by session id. Suitable for a single-process local
# deployment; replace with persistent storage for multi-instance production.
_SESSION_DRAFTS: dict[str, QuotationDraft] = {}


class ConfirmationInput(BaseModel):
    field_name: str = Field(..., min_length=1)
    confirmed: bool


class RequirementMergeRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    candidates: list[RequirementCandidateInput] = Field(default_factory=list)
    confirmations: list[ConfirmationInput] = Field(default_factory=list)


@app.post("/api/v1/requirements/merge")
def merge_requirements(request: RequirementMergeRequest) -> dict[str, Any]:
    session_id = request.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=422, detail="session_id cannot be blank")

    draft = _SESSION_DRAFTS.get(session_id) or QuotationDraft(quotation_id=session_id)

    merged: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    notices: list[str] = []

    for confirmation in request.confirmations:
        try:
            outcome = confirm_pending(
                draft, confirmation.field_name, accept=confirmation.confirmed
            )
        except RequirementValidationError as error:
            rejected.append(
                {
                    "field_name": confirmation.field_name,
                    "raw_value": None,
                    "reason": str(error),
                }
            )
            continue
        draft = outcome.draft
        merged.update(outcome.applied)

    if request.candidates:
        outcome = merge_candidates(
            draft,
            [
                RequirementCandidate(
                    field_name=candidate.field_name,
                    value=candidate.value,
                    confidence=candidate.confidence,
                    source="lowcode_agent",
                )
                for candidate in request.candidates
            ],
        )
        draft = outcome.draft
        merged.update(outcome.applied)
        notices.extend(outcome.notices)
        rejected.extend(
            {
                "field_name": item.field_name,
                "raw_value": to_jsonable(item.value),
                "reason": item.reason,
            }
            for item in outcome.rejected
        )

    _SESSION_DRAFTS[session_id] = draft
    return {
        "merged": [
            {"field_name": name, "value": to_jsonable(value)}
            for name, value in merged.items()
        ],
        "pending_confirmations": [
            {
                "field_name": item.field_name,
                "value": to_jsonable(item.value),
                "confidence": item.confidence,
                "question": item.question,
            }
            for item in pending_confirmations(draft)
        ],
        "rejected": rejected,
        "notices": notices,
        "draft_snapshot": to_jsonable(draft),
    }
