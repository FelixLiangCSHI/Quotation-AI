"""Strict Pydantic response schemas for Agent 1-4.

Every schema forbids unknown fields so that a provider cannot smuggle extra
keys (for example a price or an approval status) into the workflow.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Agent 1: requirement understanding -----------------------------------


class ExtractedRequirement(StrictSchema):
    field_name: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=200)
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class Agent1RequirementResponse(StrictSchema):
    requirements: list[ExtractedRequirement] = Field(default_factory=list)
    product_interpretation: str = Field(default="", max_length=1000)
    missing_questions: list[str] = Field(default_factory=list, max_length=20)
    recommendation_rationale: str = Field(default="", max_length=2000)


# --- Agent 2: pricing narrative -------------------------------------------


class Agent2PricingResponse(StrictSchema):
    evidence_summary: str = Field(default="", max_length=2000)
    analysis_explanation: str = Field(default="", max_length=2000)
    risks: list[str] = Field(default_factory=list, max_length=20)


# --- Agent 3: email wording -----------------------------------------------


class Agent3EmailResponse(StrictSchema):
    email_type: str = Field(min_length=1, max_length=64)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20000)


# --- Agent 4: document plan -----------------------------------------------


class DocumentSectionPlan(StrictSchema):
    section_id: str = Field(min_length=1, max_length=64)
    heading: str = Field(min_length=1, max_length=200)
    narrative: str = Field(default="", max_length=2000)


class ChartCaptionPlan(StrictSchema):
    chart_id: str = Field(min_length=1, max_length=64)
    caption: str = Field(default="", max_length=300)


class Agent4DocumentPlanResponse(StrictSchema):
    """Agent 4 may propose presentation only.

    Every field here is narrative or ordering. No trusted commercial value,
    identifier, status or path can be expressed through this schema.
    """

    sections: list[DocumentSectionPlan] = Field(min_length=1, max_length=20)
    customer_safe_summary: str = Field(default="", max_length=2000)
    cover_subtitle: str = Field(default="", max_length=200)
    executive_summary: str = Field(default="", max_length=2000)
    chart_captions: list[ChartCaptionPlan] = Field(
        default_factory=list, max_length=10
    )
    layout_recommendation: str = Field(default="standard", max_length=64)
