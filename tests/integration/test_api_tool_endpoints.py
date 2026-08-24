"""Integration tests for the low-code tool endpoints defined in tools.md."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from app import api


@pytest.fixture()
def client():
    api._SESSION_DRAFTS.clear()
    return TestClient(api.app)


def test_decision_tree_search_returns_nodes(client):
    response = client.get("/api/v1/decision-tree/search", params={"query": "FMT"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 1
    node = body["nodes"][0]
    assert set(node) == {
        "step_id",
        "product_id",
        "option_group",
        "short_description",
        "raw_constraint_text",
    }


def test_decision_tree_search_filters_by_option_group(client):
    response = client.get(
        "/api/v1/decision-tree/search",
        params={"query": "FMT", "option_group": "Generator"},
    )
    assert response.status_code == 200
    body = response.json()
    assert all(node["option_group"] == "Generator" for node in body["nodes"])


def test_configuration_validate_passes_known_product(client):
    response = client.post(
        "/api/v1/configuration/validate",
        json={"product_ids": ["DEMO-FMT-100"], "region": "china"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is True
    assert body["issues"] == []


def test_configuration_validate_flags_unknown_product(client):
    response = client.post(
        "/api/v1/configuration/validate",
        json={"product_ids": ["NOPE-1"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["passed"] is False
    assert body["issues"][0]["code"] == "unknown_product"
    assert "region" in body["missing_fields"]


def test_requirements_validate_accepts_and_rejects(client):
    response = client.post(
        "/api/v1/requirements/validate",
        json={
            "candidates": [
                {"field_name": "currency", "value": "RMB"},
                {"field_name": "quantity", "value": "two"},
                {"field_name": "bogus_field", "value": "x"},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    accepted = {item["field_name"]: item["value"] for item in body["accepted"]}
    assert accepted == {"currency": "CNY"}
    rejected_fields = {item["field_name"] for item in body["rejected"]}
    assert rejected_fields == {"quantity", "bogus_field"}


def test_requirements_merge_and_confirm_flow(client):
    response = client.post(
        "/api/v1/requirements/merge",
        json={
            "session_id": "session-1",
            "candidates": [
                {"field_name": "region", "value": "china", "confidence": 0.4},
                {"field_name": "customer_name", "value": "ACME", "confidence": 0.9},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    merged = {item["field_name"] for item in body["merged"]}
    assert "customer_name" in merged
    pending = body["pending_confirmations"]
    assert pending and pending[0]["field_name"] == "region"
    assert pending[0]["question"]

    response = client.post(
        "/api/v1/requirements/merge",
        json={
            "session_id": "session-1",
            "confirmations": [{"field_name": "region", "confirmed": True}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert {item["field_name"] for item in body["merged"]} == {"region"}
    assert body["pending_confirmations"] == []
    assert body["draft_snapshot"]["region"] == "china"
