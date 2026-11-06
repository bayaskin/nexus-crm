
import os

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL")
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD")


def login():
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        pytest.skip("Set TEST_USER_EMAIL and TEST_USER_PASSWORD to run session tests")

    response = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={
            "email": TEST_USER_EMAIL,
            "password": TEST_USER_PASSWORD,
        },
        timeout=5,
    )

    assert response.status_code == 200, response.text

    tokens = response.json()
    assert tokens["access_token"]
    assert tokens["refresh_token"]
    assert tokens["token_type"] == "bearer"

    return tokens


def get_current_user(access_token):
    return httpx.get(
        f"{BASE_URL}/v1/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=5,
    )


def logout(refresh_token):
    return httpx.post(
        f"{BASE_URL}/v1/auth/logout",
        json={"refresh_token": refresh_token},
        timeout=5,
    )


def test_current_user_requires_bearer_token():
    response = httpx.get(f"{BASE_URL}/v1/auth/me", timeout=5)
    assert response.status_code == 401


def test_users_endpoint_requires_authentication():
    response = httpx.get(f"{BASE_URL}/v1/users", timeout=5)
    assert response.status_code == 401


def test_login_rejects_unknown_account():
    response = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={
            "email": "unknown-user@example.com",
            "password": "not-a-real-password",
        },
        timeout=5,
    )
    assert response.status_code == 401


def test_invalid_bearer_token_is_rejected():
    response = get_current_user("not-a-valid-jwt")
    assert response.status_code == 401


def test_successful_login_and_current_user():
    tokens = login()

    response = get_current_user(tokens["access_token"])

    assert response.status_code == 200
    assert response.json()["email"] == TEST_USER_EMAIL.lower()

    assert logout(tokens["refresh_token"]).status_code == 204


def test_logout_revokes_access_and_refresh_tokens():
    tokens = login()

    # Session is initially valid.
    assert get_current_user(tokens["access_token"]).status_code == 200

    # Logout revokes the associated database session.
    response = logout(tokens["refresh_token"])
    assert response.status_code == 204

    # Both token types must now be unusable.
    assert get_current_user(tokens["access_token"]).status_code == 401

    refresh_response = httpx.post(
        f"{BASE_URL}/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
        timeout=5,
    )
    assert refresh_response.status_code == 401


def test_refresh_rotation_invalidates_old_access_token():
    tokens = login()

    response = httpx.post(
        f"{BASE_URL}/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
        timeout=5,
    )

    assert response.status_code == 200

    replacement = response.json()

    assert replacement["access_token"]
    assert replacement["refresh_token"]

    # Old access token must no longer work.
    assert get_current_user(tokens["access_token"]).status_code == 401

    # New access token must work.
    assert get_current_user(replacement["access_token"]).status_code == 200

    assert logout(replacement["refresh_token"]).status_code == 204


def test_independent_sessions_survive_other_session_logout():
    first_session = login()
    second_session = login()

    assert get_current_user(first_session["access_token"]).status_code == 200
    assert get_current_user(second_session["access_token"]).status_code == 200

    # Logging out from the first device must not invalidate the second.
    assert logout(first_session["refresh_token"]).status_code == 204

    assert get_current_user(first_session["access_token"]).status_code == 401
    assert get_current_user(second_session["access_token"]).status_code == 200

    assert logout(second_session["refresh_token"]).status_code == 204


def test_refresh_token_reuse_revokes_all_user_sessions():
    first_session = login()
    second_session = login()

    # Rotate the first session's refresh token.
    rotation = httpx.post(
        f"{BASE_URL}/v1/auth/refresh",
        json={"refresh_token": first_session["refresh_token"]},
        timeout=5,
    )
    assert rotation.status_code == 200

    replacement = rotation.json()

    # Both sessions should initially be valid.
    assert get_current_user(replacement["access_token"]).status_code == 200
    assert get_current_user(second_session["access_token"]).status_code == 200

    # Reuse the old, already-rotated refresh token.
    reuse = httpx.post(
        f"{BASE_URL}/v1/auth/refresh",
        json={"refresh_token": first_session["refresh_token"]},
        timeout=5,
    )
    assert reuse.status_code == 401
    assert "reuse detected" in reuse.json()["detail"].lower()

    # Reuse detection should revoke every active session for this user.
    assert get_current_user(replacement["access_token"]).status_code == 401
    assert get_current_user(second_session["access_token"]).status_code == 401