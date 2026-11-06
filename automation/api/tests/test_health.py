import os
import httpx

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")


def test_api_health():
    response = httpx.get(f"{BASE_URL}/health", timeout=5)
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "nexus-api"}


def test_api_readiness_includes_database():
    response = httpx.get(f"{BASE_URL}/health/ready", timeout=5)
    assert response.status_code == 200
    assert response.json()["database"] == "connected"
