
import os
import threading
from concurrent.futures import ThreadPoolExecutor

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

    assert response.status_code == 200, f"Login failed with HTTP {response.status_code}"

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


def test_concurrent_logins_for_qa_admin_create_independent_sessions():
    if os.getenv("MYSQL_TEST_DATABASE_KIND") != "disposable":
        pytest.skip("Concurrent shared-account login regression requires the disposable QA environment")

    assert os.getenv("DB_HOST") == "qa-mysql", "Concurrent login test must target QA MySQL"
    assert os.getenv("DB_PORT") == "3306", "Concurrent login test must use the QA MySQL port"
    assert os.getenv("DB_NAME") == "nexus_qa_test", "Concurrent login test must target nexus_qa_test"
    assert os.getenv("DB_USER") == "nexus_qa_runner", "Concurrent login test must use the QA database user"
    assert BASE_URL == "http://api:8000", "Concurrent login test must target the QA API service"
    database_password = os.getenv("DB_PASSWORD", "")
    if not database_password:
        pytest.fail("Concurrent login test requires the disposable QA database credentials")
    expected_database_url = (
        f"mysql+pymysql://nexus_qa_runner:{database_password}@qa-mysql:3306/nexus_qa_test"
    )
    if os.getenv("DATABASE_URL") != expected_database_url:
        pytest.fail("Concurrent login test requires the QA database URL")
    if os.getenv("MYSQL_TEST_DATABASE_URL") != expected_database_url:
        pytest.fail("Concurrent login test requires matching direct and migration database URLs")
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        pytest.skip("Set QA administrator credentials to run the concurrent login test")

    worker_count = 8
    barrier = threading.Barrier(worker_count)

    def concurrent_login(_worker: int):
        barrier.wait(timeout=10)
        return httpx.post(
            f"{BASE_URL}/v1/auth/login",
            json={"email": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD},
            timeout=10,
        )

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        responses = list(executor.map(concurrent_login, range(worker_count)))

    successful_tokens = [response.json() for response in responses if response.status_code == 200]
    try:
        statuses = [response.status_code for response in responses]
        assert statuses == [200] * worker_count, f"Concurrent login HTTP statuses: {statuses}"

        unique_session_count = len({token["refresh_token"] for token in successful_tokens})
        assert unique_session_count == worker_count, (
            f"Expected {worker_count} independent refresh sessions, got {unique_session_count}"
        )

        current_user_statuses = [
            get_current_user(token["access_token"]).status_code
            for token in successful_tokens
        ]
        assert current_user_statuses == [200] * worker_count, (
            f"Concurrent login session validation HTTP statuses: {current_user_statuses}"
        )
    finally:
        for token in successful_tokens:
            try:
                logout(token["refresh_token"])
            except httpx.HTTPError:
                pass


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
