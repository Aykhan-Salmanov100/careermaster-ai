"""Tests for the POST /interview/evaluate endpoint (STAR framework analysis)."""

import pytest


# ---------------------------------------------------------------------------
# Good STAR response
# ---------------------------------------------------------------------------

GOOD_STAR_RESPONSE = (
    "When I was working at Google in 2021, during a critical migration project, "
    "our team faced a significant challenge with the legacy system. "
    "I was responsible for redesigning the data pipeline and had to meet "
    "a tight deadline. My goal was to reduce latency by 50%. "
    "I implemented a new caching layer using Redis, I designed the schema "
    "migration strategy, and I built automated rollback procedures. "
    "I collaborated with the DevOps team to ensure smooth deployment. "
    "As a result, we achieved a 60% reduction in latency, improved uptime "
    "to 99.9%, and saved the company $200K annually."
)

POOR_RESPONSE = "I don't know."

ALL_STAR_RESPONSE = (
    "During my time at a fintech startup in 2022, while leading the backend team, "
    "the context was that we needed to scale our payment processing system. "
    "I was tasked with redesigning the architecture. I needed to handle "
    "10x the transaction volume. My objective was clear: zero downtime migration. "
    "I built a new microservices architecture. I implemented event-driven processing. "
    "I created comprehensive monitoring dashboards. I designed the database sharding "
    "strategy. I led the team through a phased rollout. "
    "The result was outstanding: we achieved 99.99% uptime, reduced processing "
    "time by 75%, increased throughput by 10x, and saved $500K in infrastructure "
    "costs. The outcome delivered real business value."
)


def test_evaluate_good_response_score_above_60(client):
    """A good STAR response should score above 60."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": GOOD_STAR_RESPONSE,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_score"] > 60, f"Expected >60, got {data['total_score']}"


def test_evaluate_good_response_grade(client):
    """A good STAR response should receive grade A or B."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": GOOD_STAR_RESPONSE,
        },
    )
    data = response.json()
    assert data["grade"] in ("A", "B"), f"Expected A or B, got {data['grade']}"


# ---------------------------------------------------------------------------
# Poor response
# ---------------------------------------------------------------------------

def test_evaluate_poor_response_low_score(client):
    """'I don't know' should score below 30."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 2,
            "question_text": "Tell me about a challenge you faced.",
            "response_text": POOR_RESPONSE,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_score"] < 30, f"Expected <30, got {data['total_score']}"


def test_evaluate_poor_response_grade_d(client):
    """A poor response should get grade D."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 2,
            "question_text": "Tell me about a challenge you faced.",
            "response_text": POOR_RESPONSE,
        },
    )
    data = response.json()
    assert data["grade"] == "D", f"Expected D, got {data['grade']}"


# ---------------------------------------------------------------------------
# Full STAR response
# ---------------------------------------------------------------------------

def test_evaluate_all_star_components_high_score(client):
    """Response with all STAR components should score above 80."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 3,
            "question_text": "Describe a major project you led.",
            "response_text": ALL_STAR_RESPONSE,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_score"] > 80, f"Expected >80, got {data['total_score']}"


def test_evaluate_all_star_grade_a(client):
    """A comprehensive STAR response should get grade A."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 3,
            "question_text": "Describe a major project you led.",
            "response_text": ALL_STAR_RESPONSE,
        },
    )
    data = response.json()
    assert data["grade"] == "A", f"Expected A, got {data['grade']}"


# ---------------------------------------------------------------------------
# Empty response
# ---------------------------------------------------------------------------

def test_evaluate_empty_response_returns_error(client):
    """Empty response_text should return 400, not crash."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 4,
            "question_text": "Tell me about yourself.",
            "response_text": "",
        },
    )
    # The endpoint raises HTTPException(400) for empty response
    assert response.status_code == 400


def test_evaluate_whitespace_only_response(client):
    """Whitespace-only response should either error or score very low."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 5,
            "question_text": "Tell me about yourself.",
            "response_text": "   ",
        },
    )
    # The endpoint checks `if not response_text` -- whitespace is truthy,
    # so it will be evaluated but score very low.
    if response.status_code == 200:
        data = response.json()
        assert data["total_score"] < 30
    else:
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Feedback field
# ---------------------------------------------------------------------------

def test_evaluate_feedback_present_and_nonempty(client):
    """The response should include non-empty overall_feedback."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": GOOD_STAR_RESPONSE,
        },
    )
    data = response.json()
    assert "overall_feedback" in data
    assert isinstance(data["overall_feedback"], str)
    assert len(data["overall_feedback"]) > 0


def test_evaluate_star_feedback_per_component(client):
    """Each STAR component should have feedback."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": GOOD_STAR_RESPONSE,
        },
    )
    data = response.json()
    assert "star_feedback" in data
    for component in ("situation", "task", "action", "result"):
        assert component in data["star_feedback"], f"Missing feedback for {component}"
        assert len(data["star_feedback"][component]) > 0


def test_evaluate_suggestions_present(client):
    """Response should include a suggestions list."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": POOR_RESPONSE,
        },
    )
    data = response.json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    # Poor response should have suggestions
    assert len(data["suggestions"]) > 0


def test_evaluate_strengths_and_weaknesses(client):
    """Response should include strengths and weaknesses lists."""
    response = client.post(
        "/interview/evaluate",
        json={
            "question_id": 1,
            "question_text": "Tell me about a time you improved a system.",
            "response_text": GOOD_STAR_RESPONSE,
        },
    )
    data = response.json()
    assert "strengths" in data
    assert "weaknesses" in data
    assert isinstance(data["strengths"], list)
    assert isinstance(data["weaknesses"], list)
