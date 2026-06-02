"""Tests for the POST /live/suggest endpoint (Live Interview Assistant)."""

import pytest


# ---------------------------------------------------------------------------
# Question type classification via the endpoint
# ---------------------------------------------------------------------------

def test_behavioral_question_type(client):
    """A behavioral question should be classified as 'behavioral'."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Tell me about a time when you led a team project."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["question_type"] == "behavioral"


def test_technical_question_type(client):
    """A technical question should be classified as 'technical'."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "How would you implement a caching layer for a web application?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["question_type"] == "technical"


def test_situational_question_type(client):
    """A situational question should be classified as 'situational'."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "What would you do if you found a critical bug right before a release?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["question_type"] == "situational"


def test_general_question_type(client):
    """A general question should be classified as 'general'."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Why do you want to work at our company?"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["question_type"] == "general"


# ---------------------------------------------------------------------------
# Response structure
# ---------------------------------------------------------------------------

def test_response_includes_star_hints(client):
    """Response should include star_hints dict."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Tell me about a time you solved a difficult problem."},
    )
    data = response.json()
    assert "star_hints" in data
    assert isinstance(data["star_hints"], dict)
    assert len(data["star_hints"]) > 0


def test_response_includes_suggested_points(client):
    """Response should include suggested_points list."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "How would you design a REST API?"},
    )
    data = response.json()
    assert "suggested_points" in data
    assert isinstance(data["suggested_points"], list)
    assert len(data["suggested_points"]) > 0


def test_response_includes_answer_skeleton(client):
    """Response should include an answer_skeleton string."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Tell me about a time you worked under pressure."},
    )
    data = response.json()
    assert "answer_skeleton" in data
    assert isinstance(data["answer_skeleton"], str)
    assert len(data["answer_skeleton"]) > 20


def test_behavioral_star_hints_have_all_components(client):
    """Behavioral questions should get STAR hints for all four components."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Tell me about a time when you overcame a challenge."},
    )
    data = response.json()
    hints = data["star_hints"]
    for component in ("situation", "task", "action", "result"):
        assert component in hints, f"Missing STAR hint for '{component}'"


# ---------------------------------------------------------------------------
# With CV data
# ---------------------------------------------------------------------------

def test_with_cv_data_references_skills(client):
    """When cv_data is provided, suggested_points should reference CV skills."""
    cv_data = {
        "name": "Jane Doe",
        "role": "Backend Engineer",
        "skills": ["python", "django", "postgresql", "docker"],
        "experience": [
            {"title": "Backend Engineer", "company": "TechCorp", "dates": "2020-2023"}
        ],
        "education": ["BSc Computer Science, UCL"],
    }
    response = client.post(
        "/live/suggest",
        json={
            "transcript": "How would you design a backend system using python and django?",
            "cv_data": cv_data,
            "role": "Backend Engineer",
        },
    )
    assert response.status_code == 200
    data = response.json()
    points_text = " ".join(data["suggested_points"]).lower()
    # Should reference at least one CV skill
    has_skill_ref = any(
        skill in points_text for skill in ["python", "django", "postgresql", "docker"]
    )
    assert has_skill_ref, f"Suggested points don't reference CV skills: {data['suggested_points']}"


def test_with_cv_data_includes_role(client):
    """When cv_data has a role, suggested_points should include target role."""
    cv_data = {
        "name": "Test User",
        "role": "Data Scientist",
        "skills": ["python", "machine learning"],
    }
    response = client.post(
        "/live/suggest",
        json={
            "transcript": "Tell me about a time you worked with data.",
            "cv_data": cv_data,
        },
    )
    data = response.json()
    points_text = " ".join(data["suggested_points"]).lower()
    assert "data scientist" in points_text, (
        f"Expected role reference in points: {data['suggested_points']}"
    )


# ---------------------------------------------------------------------------
# Without CV data
# ---------------------------------------------------------------------------

def test_without_cv_data_returns_generic_suggestions(client):
    """Without cv_data, the endpoint should still return suggestions."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Describe a situation where you had to learn quickly."},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["suggested_points"]) > 0
    # Should mention that no CV was provided or give generic advice
    points_text = " ".join(data["suggested_points"]).lower()
    assert "cv" in points_text or len(data["suggested_points"]) >= 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_transcript_returns_400(client):
    """Empty transcript should return 400."""
    response = client.post(
        "/live/suggest",
        json={"transcript": ""},
    )
    assert response.status_code == 400


def test_whitespace_transcript_returns_400(client):
    """Whitespace-only transcript should return 400."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "   "},
    )
    assert response.status_code == 400


def test_response_has_confidence(client):
    """Response should include a confidence score."""
    response = client.post(
        "/live/suggest",
        json={"transcript": "Tell me about your experience with Python."},
    )
    data = response.json()
    assert "confidence" in data
    assert isinstance(data["confidence"], (int, float))
    assert 0 <= data["confidence"] <= 1
