import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("TEST_USER_EMAIL")
ADMIN_PASSWORD = os.getenv("TEST_USER_PASSWORD")


@pytest.fixture(scope="module")
def admin_headers():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        pytest.skip("Admin test credentials are not configured")
    response = httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=5)
    assert response.status_code == 200, f"Admin login failed with HTTP {response.status_code}"
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_user_list_requires_authentication():
    response = httpx.get(f"{BASE_URL}/v1/users", timeout=5)
    assert response.status_code == 401


def test_create_user_rejects_invalid_email(admin_headers):
    response = httpx.post(f"{BASE_URL}/v1/users", headers=admin_headers, json={
        "first_name": "Invalid", "last_name": "Email", "email": "not-an-email",
    }, timeout=5)
    assert response.status_code == 422


def test_user_create_search_details_and_duplicate(admin_headers):
    email = f"qa-user-{uuid.uuid4().hex}@example.com"
    payload = {"first_name": "QA", "last_name": "Candidate", "email": email, "role_names": ["User"], "send_credentials": False}
    created = httpx.post(f"{BASE_URL}/v1/users", headers=admin_headers, json=payload, timeout=10)
    assert created.status_code == 201, f"User creation failed with HTTP {created.status_code}"
    body = created.json()
    assert body["email"] == email
    assert body["must_change_password"] is True
    assert body["email_sent"] is False
    assert body["credentials_sent"] is False
    assert body["credentials_email_requested"] is False
    assert "temporary_password" not in body, "User response exposed a temporary password"
    assert "Reset password" in body["message"]

    duplicate = httpx.post(f"{BASE_URL}/v1/users", headers=admin_headers, json=payload, timeout=10)
    assert duplicate.status_code == 409

    listing = httpx.get(f"{BASE_URL}/v1/users", headers=admin_headers, params={"search": "QA Candidate", "page": 1, "page_size": 10}, timeout=5)
    assert listing.status_code == 200
    assert isinstance(listing.json(), list)
    assert any(item["id"] == body["id"] for item in listing.json())

    detail = httpx.get(f"{BASE_URL}/v1/users/{body['id']}", headers=admin_headers, timeout=5)
    assert detail.status_code == 200
    assert "password_hash" not in detail.json(), "User detail response exposed a password hash"

    disabled = httpx.patch(f"{BASE_URL}/v1/users/{body['id']}", headers=admin_headers, json={"is_active": False}, timeout=5)
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False


def test_explicit_deny_overrides_role_grant(admin_headers):
    email = f"qa-deny-{uuid.uuid4().hex}@example.com"
    temporary_password = "KnownTemporaryPass!2030"
    created = httpx.post(f"{BASE_URL}/v1/users", headers=admin_headers, json={
        "first_name": "Permission", "last_name": "Check", "email": email,
        "password": temporary_password, "role_names": ["User"],
        "permission_overrides": {"customers:read": "deny"}, "send_credentials": False,
    }, timeout=10)
    assert created.status_code == 201, f"User creation failed with HTTP {created.status_code}"
    assert "customers:read" not in created.json()["effective_permissions"]
    assert "password_hash" not in created.json(), "User response exposed a password hash"
    assert "temporary_password" not in created.json(), "User response exposed a temporary password"
    tokens = httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": email, "password": temporary_password}, timeout=5)
    assert tokens.status_code == 200, f"User login failed with HTTP {tokens.status_code}"
    headers = {"Authorization": f"Bearer {tokens.json()['access_token']}"}
    denied = httpx.get(f"{BASE_URL}/v1/customers", headers=headers, timeout=5)
    assert denied.status_code == 403
