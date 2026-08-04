from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QuoteRequest:
    raw_text: str
    keywords: tuple[str, ...]
    product_ids: tuple[str, ...]
    region: str | None = None
    system_family: str | None = None
    acquisition_type: str | None = None
    customer_name: str | None = None
    quantity: int | None = None
    currency: str | None = None
    incoterm: str | None = None
    delivery_location: str | None = None
    target_price: float | None = None
    product_query: str | None = None


PRODUCT_ID_RE = re.compile(r"\b\d{7}\b")
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+/-]*|\d+(?:kw|khu|ft|cm|li)?", re.IGNORECASE)
QUANTITY_PATTERNS = (
    re.compile(
        r"\b(?:qty|quantity)(?:\s+should\s+be|\s+to)?\s*[:=]?\s*(\d+)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(\d+)\s*(?:units?|pieces?|pcs?)\b", re.IGNORECASE),
    re.compile(r"数量\s*[:：]?\s*(\d+)\s*台?", re.IGNORECASE),
)
CURRENCY_RE = re.compile(r"\b(USD|SGD|RMB|CNY|EUR)\b", re.IGNORECASE)
INCOTERM_RE = re.compile(r"\b(EXW|FCA|FOB|CIF|DAP|DDP)\b", re.IGNORECASE)
TARGET_PRICE_RE = re.compile(
    r"\b(?:target\s+price|target)\s*[:=]?\s*"
    r"(?:(USD|SGD|RMB|CNY|EUR)\s*)?"
    r"(\d[\d,]*(?:\.\d+)?)\s*([km])?"
    r"(?:\s*(USD|SGD|RMB|CNY|EUR))?\b",
    re.IGNORECASE,
)

STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "for",
    "i",
    "in",
    "is",
    "me",
    "need",
    "of",
    "one",
    "please",
    "recommend",
    "budget",
    "cost",
    "price",
    "rmb",
    "cny",
    "usd",
    "eur",
    "the",
    "to",
    "want",
    "with",
}

REGION_ALIASES = (
    ("canada", ("canada", "加拿大")),
    ("china", ("china", "prc", "中国", "中国市场")),
    ("singapore", ("singapore", "新加坡")),
    ("malaysia", ("malaysia", "马来西亚")),
    ("indonesia", ("indonesia", "印度尼西亚", "印尼")),
    ("us", ("u.s.", "us", "usa", "united states", "america", "美国", "美区")),
    ("eu", ("europe", "eu", "emea", "欧洲", "欧盟")),
)

SYSTEM_FAMILY_ALIASES = (
    ("OTC", ("otc", "overhead", "ceiling", "悬吊", "天吊", "吊架")),
    ("FMT", ("fmt", "floor mount", "floor-mounted", "floor mounted", "落地", "地轨", "立柱")),
)

ACQUISITION_ALIASES = (
    ("digital", ("digital", "dr", "数字", "数字化")),
    ("analog", ("analog", "analogue", "模拟")),
)

PHRASE_KEYWORDS = (
    ("x-ray", ("xray", "x-ray", "x ray", "x光", "x 射线", "放射")),
    ("system", ("system", "系统", "整机", "整套", "配置")),
    ("detector", ("detector", "探测器", "平板")),
    ("generator", ("generator", "发生器", "高压发生器")),
    ("tube", ("tube", "球管")),
    ("collimator", ("collimator", "限束器", "束光器")),
    ("wallstand", ("wallstand", "wall stand", "胸片架", "壁架", "立位架")),
    ("table", ("table", "摄影床", "检查床", "床")),
    ("grid", ("grid", "滤线栅")),
    ("bucky", ("bucky", "暗盒架")),
    ("wireless", ("wireless", "wifi", "wi-fi", "无线")),
    ("manual", ("manual", "手动")),
    ("motorized", ("motorized", "motorised", "电动", "马达")),
    ("focus", ("focus", "focus 35", "focus 43")),
    ("drx", ("drx", "drx plus", "drx lux")),
    ("compass", ("compass",)),
    ("low-cost", ("low cost", "cost effective", "便宜", "低价", "经济", "预算有限")),
)


def parse_quote_request(text: str) -> QuoteRequest:
    raw_text = text.strip()
    return QuoteRequest(
        raw_text=raw_text,
        keywords=_extract_keywords(raw_text),
        product_ids=tuple(dict.fromkeys(PRODUCT_ID_RE.findall(raw_text))),
        region=_extract_region(raw_text),
        system_family=_extract_system_family(raw_text),
        acquisition_type=_extract_acquisition_type(raw_text),
        customer_name=_extract_customer_name(raw_text),
        quantity=_extract_quantity(raw_text),
        currency=_extract_currency(raw_text),
        incoterm=_extract_incoterm(raw_text),
        delivery_location=_extract_delivery_location(raw_text),
        target_price=_extract_target_price(raw_text),
        product_query=_extract_product_query(raw_text),
    )


def _extract_region(text: str) -> str | None:
    normalized = text.casefold()
    for region, aliases in REGION_ALIASES:
        if _contains_any(normalized, aliases):
            return region
    return None


def _extract_system_family(text: str) -> str | None:
    normalized = text.casefold()
    for system_family, aliases in SYSTEM_FAMILY_ALIASES:
        if _contains_any(normalized, aliases):
            return system_family
    return None


def _extract_acquisition_type(text: str) -> str | None:
    normalized = text.casefold()
    for acquisition_type, aliases in ACQUISITION_ALIASES:
        if _contains_any(normalized, aliases):
            return acquisition_type
    return None


def _extract_customer_name(text: str) -> str | None:
    patterns = (
        re.compile(
            r"(?:customer\s+is|客户是)\s*[:：]?\s*(.+?)"
            r"(?=\s+(?:in|with|deliver(?:y)?|qty|quantity)\b|[.,;，；。]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bfor\s+(.+?)"
            r"(?=\s+(?:in|with|deliver(?:y)?|qty|quantity)\b|[.,;，；。]|$)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if not match:
            continue
        customer_name = match.group(1).strip(" \t:：,.;，；。")
        if customer_name and not _is_region_only(customer_name):
            return customer_name
    return None


def _extract_quantity(text: str) -> int | None:
    for pattern in QUANTITY_PATTERNS:
        match = pattern.search(text)
        if match:
            quantity = int(match.group(1))
            return quantity if quantity > 0 else None
    return None


def _extract_currency(text: str) -> str | None:
    match = CURRENCY_RE.search(text)
    return match.group(1).upper() if match else None


def _extract_incoterm(text: str) -> str | None:
    match = INCOTERM_RE.search(text)
    return match.group(1).upper() if match else None


def _extract_delivery_location(text: str) -> str | None:
    patterns = (
        re.compile(
            r"\bdeliver\s+to\s+(.+?)"
            r"(?=\s+(?:with|qty|quantity|under|at)\b|[.,;，；]|$)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bdelivery\s+location\s*[:：]\s*(.+?)"
            r"(?=\s+(?:with|qty|quantity|under|at)\b|[.,;，；]|$)",
            re.IGNORECASE,
        ),
        re.compile(r"交付到\s*(.+?)(?=[.,;，；]|$)", re.IGNORECASE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            location = match.group(1).strip(" \t:：,.;，；")
            if location:
                return location
    return None


def _extract_target_price(text: str) -> float | None:
    match = TARGET_PRICE_RE.search(text)
    if not match:
        return None
    value = float(match.group(2).replace(",", ""))
    multiplier = {"k": 1_000, "m": 1_000_000}.get(
        (match.group(3) or "").casefold(),
        1,
    )
    return value * multiplier


def _extract_product_query(text: str) -> str | None:
    product_ids = PRODUCT_ID_RE.findall(text)
    product_keywords = set(_extract_keywords(text)).intersection(
        {
            "compass",
            "detector",
            "drx",
            "focus",
            "generator",
            "system",
            "x-ray",
        }
    )
    if (
        product_ids
        or product_keywords
        or _extract_system_family(text)
        or _extract_acquisition_type(text)
        or re.search(r"\b(?:rise|revolution|evolution)\b", text, re.IGNORECASE)
    ):
        return text.strip()
    return None


def _is_region_only(value: str) -> bool:
    normalized = value.casefold().strip()
    return any(
        normalized == alias.casefold()
        for _, aliases in REGION_ALIASES
        for alias in aliases
    )


def _extract_keywords(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    keywords: list[str] = []

    for keyword, aliases in PHRASE_KEYWORDS:
        if _contains_any(normalized, aliases):
            keywords.append(keyword)

    for value in (_extract_system_family(text), _extract_acquisition_type(text)):
        if value:
            keywords.append(value.casefold())

    for token in WORD_RE.findall(normalized):
        clean_token = token.strip(".,;:!?()[]{}\"'").casefold()
        if (
            clean_token
            and len(clean_token) > 1
            and clean_token not in STOP_WORDS
            and not clean_token.isdigit()
            and not PRODUCT_ID_RE.fullmatch(clean_token)
        ):
            keywords.append(clean_token)

    return tuple(dict.fromkeys(keywords))


def _contains_any(text: str, aliases: tuple[str, ...]) -> bool:
    for alias in aliases:
        normalized_alias = alias.casefold()
        if re.fullmatch(r"[a-z0-9. -]+", normalized_alias):
            pattern = r"(?<![a-z0-9])" + re.escape(normalized_alias) + r"(?![a-z0-9])"
            if re.search(pattern, text):
                return True
        elif normalized_alias in text:
            return True
    return False