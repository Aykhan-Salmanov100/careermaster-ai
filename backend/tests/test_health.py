"""Tests for the GET /health endpoint."""


def test_health_returns_200(client):
    """GET /health should return HTTP 200."""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_contains_status_ok(client):
    """GET /health response must include {"status": "ok"}."""
    response = client.get("/health")
    data = response.json()
    assert "status" in data
    assert data["status"] == "ok"


def test_health_contains_version(client):
    """GET /health response should include a version string."""
    response = client.get("/health")
    data = response.json()
    assert "version" in data
    assert isinstance(data["version"], str)
