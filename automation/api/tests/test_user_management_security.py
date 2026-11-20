import os
import json
import re
import time
import uuid

import httpx
import pymysql
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("TEST_USER_EMAIL")
ADMIN_PASSWORD = os.getenv("TEST_USER_PASSWORD")
MAILHOG_URL = os.getenv("MAILHOG_API_URL", "http://mailhog:8025")


@pytest.fixture(scope="module")
def admin_headers():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        pytest.skip("Admin test credentials are not configured")
    response = httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=5)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_user(headers, *, email=None, password=None, roles=None, overrides=None, send_credentials=False):
    email = email or f"security-{uuid.uuid4().hex}@example.com"
    payload = {
        "first_name": "Security",
        "last_name": "Review",
        "email": email,
        "role_names": roles or [],
        "permission_overrides": overrides or {},
        "send_credentials": send_credentials,
    }
    if password:
        payload["password"] = password
    response = httpx.post(f"{BASE_URL}/v1/users", headers=headers, json=payload, timeout=10)
    return email, response


def login(email, password):
    response = httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": email, "password": password}, timeout=5)
    assert response.status_code == 200, response.text
    return response.json()


def db_connection():
    required = ("DB_HOST", "DB_USER", "DB_PASSWORD", "DB_NAME")
    if any(not os.getenv(name) for name in required):
        pytest.skip("Database test connection variables are not configured")
    return pymysql.connect(
        host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"], autocommit=True,
    )


def ensure_limited_creator_role():
    """Seed a narrow role in the disposable test DB for assignment denial cases."""
    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM roles WHERE name=%s", ("Security Creator",))
            row = cursor.fetchone()
            role_id = row[0] if row else uuid.uuid4().hex
            if not row:
                cursor.execute("INSERT INTO roles (id, name, description) VALUES (%s, %s, %s)", (role_id, "Security Creator", "Test role"))
            cursor.execute("SELECT id FROM permissions WHERE resource='users' AND action IN ('create', 'update')")
            permission_ids = [item[0] for item in cursor.fetchall()]
            assert len(permission_ids) == 2, "Expected seeded users:create and users:update permissions"
            for permission_id in permission_ids:
                cursor.execute("INSERT IGNORE INTO role_permissions (role_id, permission_id) VALUES (%s, %s)", (role_id, permission_id))
    finally:
        connection.close()


def test_creation_and_reset_never_return_temporary_passwords(admin_headers):
    email, response = create_user(admin_headers)
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["email_sent"] is False
    assert created["credentials_sent"] is False
    assert created["credentials_email_requested"] is False
    assert "temporary_password" not in created
    assert "password_hash" not in created
    assert "Reset password" in created["message"]

    reset = httpx.post(f"{BASE_URL}/v1/users/{created['id']}/reset-password", headers=admin_headers, timeout=10)
    assert reset.status_code == 200, reset.text
    body = reset.json()
    assert "temporary_password" not in body
    assert "password_hash" not in body
    assert isinstance(body["email_sent"], bool)
    if not body["email_sent"]:
        assert "retry Reset password" in body["message"]


def test_password_validation_errors_do_not_echo_submitted_secrets(admin_headers):
    login_secret = "LoginSecretMustNotAppear" * 15
    login_error = httpx.post(f"{BASE_URL}/v1/auth/login", json={
        "email": "missing@example.com", "password": login_secret,
    }, timeout=5)
    assert login_error.status_code == 422
    assert login_secret not in login_error.text

    create_secret = "ShortSecret"
    _, create_error = create_user(admin_headers, password=create_secret)
    assert create_error.status_code == 422
    assert create_secret not in create_error.text

    refresh_secret = "RefreshTokenMustNotEcho"
    refresh_error = httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": refresh_secret}, timeout=5)
    assert refresh_error.status_code == 422
    assert refresh_secret not in refresh_error.text

    change_secret = "ChangeSecretMustNotEcho"
    change_error = httpx.post(f"{BASE_URL}/v1/auth/password/change", headers=admin_headers, json={
        "current_password": "CurrentPasswordIsAlsoSecret!",
        "new_password": change_secret,
    }, timeout=5)
    assert change_error.status_code == 422
    assert change_secret not in change_error.text


def test_failed_welcome_email_reports_recovery_without_returning_password(admin_headers):
    if os.getenv("EXPECT_EMAIL_FAILURE") != "1":
        pytest.skip("Run this case against an API instance configured with unreachable SMTP")
    email, response = create_user(admin_headers, send_credentials=True)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email_sent"] is False
    assert body["credentials_sent"] is False
    assert body["credentials_email_requested"] is True
    assert "failed" in body["message"].lower()
    assert "reset password" in body["message"].lower()
    assert "temporary_password" not in body
    assert email == body["email"]


def test_welcome_password_is_mailed_but_not_returned_and_change_revokes_session(admin_headers):
    email, response = create_user(admin_headers, send_credentials=True)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email_sent"] is True, body
    assert body["credentials_sent"] is True
    assert body["credentials_email_requested"] is True
    assert "temporary_password" not in body
    assert "password_hash" not in body

    message = None
    for _ in range(40):
        search = httpx.get(f"{MAILHOG_URL}/api/v2/search", params={"kind": "to", "query": email}, timeout=5)
        assert search.status_code == 200, search.text
        items = search.json().get("items", [])
        if items:
            message = items[0]
            break
        time.sleep(0.25)
    assert message is not None, f"No MailHog message found for {email}"
    credential_body = message["Content"]["Body"]
    recipients = json.dumps(message.get("To", []))
    assert email.split("@")[0] in recipients and email.split("@")[1] in recipients
    match = re.search(r"Temporary password:\s*([A-Za-z0-9_-]+)", credential_body)
    assert match, credential_body
    temporary_password = match.group(1)

    tokens = login(email, temporary_password)
    second_tokens = login(email, temporary_password)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    protected = httpx.get(f"{BASE_URL}/v1/customers", headers=headers, timeout=5)
    assert protected.status_code == 403
    assert protected.json()["detail"] == "Password change required"

    changed = httpx.post(f"{BASE_URL}/v1/auth/password/change", headers=headers, json={
        "current_password": temporary_password, "new_password": "ChangedSecurityPass!2031",
    }, timeout=5)
    assert changed.status_code == 204, changed.text
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers=headers, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, timeout=5).status_code == 401
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {second_tokens['access_token']}"}, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}, timeout=5).status_code == 401
    logged_in = login(email, "ChangedSecurityPass!2031")
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {logged_in['access_token']}"}, timeout=5).status_code == 200


def test_deactivation_revokes_sessions_and_reactivation_does_not_restore_them(admin_headers):
    password = "LifecycleSecurityPass!2031"
    email, created = create_user(admin_headers, password=password)
    assert created.status_code == 201, created.text
    tokens = login(email, password)
    second_tokens = login(email, password)
    access = {"Authorization": f"Bearer {tokens['access_token']}"}

    response = httpx.patch(f"{BASE_URL}/v1/users/{created.json()['id']}", headers=admin_headers, json={"is_active": False}, timeout=5)
    assert response.status_code == 200, response.text
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers=access, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, timeout=5).status_code == 401
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {second_tokens['access_token']}"}, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": email, "password": password}, timeout=5).status_code == 401

    activated = httpx.patch(f"{BASE_URL}/v1/users/{created.json()['id']}", headers=admin_headers, json={"is_active": True}, timeout=5)
    assert activated.status_code == 200, activated.text
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers=access, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, timeout=5).status_code == 401
    assert login(email, password)["access_token"]


def test_password_reset_revokes_sessions_without_returning_the_password(admin_headers):
    password = "ResetSecurityPass!2031"
    email, created = create_user(admin_headers, password=password)
    assert created.status_code == 201, created.text
    tokens = login(email, password)
    second_tokens = login(email, password)

    response = httpx.post(f"{BASE_URL}/v1/users/{created.json()['id']}/reset-password", headers=admin_headers, timeout=10)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "temporary_password" not in body
    assert "password_hash" not in body
    assert isinstance(body["email_sent"], bool)
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, timeout=5).status_code == 401
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {second_tokens['access_token']}"}, timeout=5).status_code == 401
    assert httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}, timeout=5).status_code == 401


def test_temporary_password_expiry_revokes_refresh_session(admin_headers):
    password = "ExpirySecurityPass!2031"
    email, created = create_user(admin_headers, password=password)
    assert created.status_code == 201, created.text
    tokens = login(email, password)

    connection = db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE users SET password_expires_at=UTC_TIMESTAMP() - INTERVAL 1 MINUTE WHERE email=%s", (email,))
    finally:
        connection.close()

    assert httpx.post(f"{BASE_URL}/v1/auth/login", json={"email": email, "password": password}, timeout=5).status_code == 401
    refreshed = httpx.post(f"{BASE_URL}/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}, timeout=5)
    assert refreshed.status_code == 401
    assert "expired" in refreshed.json()["detail"].lower()
    assert httpx.get(f"{BASE_URL}/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}, timeout=5).status_code == 401


def test_users_without_assignment_permissions_are_denied_on_create_and_update(admin_headers):
    ensure_limited_creator_role()
    password = "LimitedCreatorPass!2031"
    actor_email, actor = create_user(admin_headers, password=password, roles=["Security Creator"])
    assert actor.status_code == 201, actor.text
    actor_headers = {"Authorization": f"Bearer {login(actor_email, password)['access_token']}"}

    _, role_attempt = create_user(actor_headers, roles=["User"])
    assert role_attempt.status_code == 403
    _, override_attempt = create_user(actor_headers, overrides={"customers:read": "allow"})
    assert override_attempt.status_code == 403
    _, ordinary = create_user(actor_headers)
    assert ordinary.status_code == 201, ordinary.text
    target_id = ordinary.json()["id"]
    assert httpx.patch(f"{BASE_URL}/v1/users/{target_id}", headers=actor_headers, json={"role_names": ["User"]}, timeout=5).status_code == 403
    assert httpx.patch(f"{BASE_URL}/v1/users/{target_id}", headers=actor_headers, json={"permission_overrides": {"customers:read": "deny"}}, timeout=5).status_code == 403


@pytest.mark.parametrize("overrides", [
    {"permission-that-does-not-exist": "allow"},
    {"customers:read": "grant"},
])
def test_admin_cannot_store_unknown_permissions_or_effects(admin_headers, overrides):
    _, response = create_user(admin_headers, overrides=overrides)
    assert response.status_code == 422, response.text


def test_admin_cannot_update_unknown_permissions_or_effects(admin_headers):
    _, created = create_user(admin_headers, password="PermissionUpdatePass!2031")
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    for overrides in ({"unknown:permission": "allow"}, {"customers:read": "grant"}):
        response = httpx.patch(f"{BASE_URL}/v1/users/{user_id}", headers=admin_headers, json={"permission_overrides": overrides}, timeout=5)
        assert response.status_code == 422, response.text
