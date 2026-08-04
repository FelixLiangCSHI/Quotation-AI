from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.natural_language import parse_quote_request
from app.recommender import QuoteRecommender, render_recommendation_text
from app.serialization import to_customer_jsonable


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
def get_recommender() -> QuoteRecommender:
    return QuoteRecommender()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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