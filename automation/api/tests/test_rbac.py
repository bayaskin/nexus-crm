import os

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL = os.getenv("TEST_USER_EMAIL")
ADMIN_PASSWORD = os.getenv("TEST_USER_PASSWORD")
RBAC_PASSWORD = os.getenv("TEST_RBAC_PASSWORD")

ROLE_PERMISSIONS = {
    "Sales Manager": [
        "customers:create",
        "customers:read",
        "customers:update",
        "inventory:read",
        "orders:create",
        "orders:read",
        "orders:update",
        "products:read",
    ],
    "Sales Representative": [
        "customers:create",
        "customers:read",
        "inventory:read",
        "orders:create",
        "orders:read",
        "products:read",
    ],
    "Support": [
        "customers:read",
        "orders:read",
        "products:read",
    ],
    "Read-only": [
        "customers:read",
        "inventory:read",
        "orders:read",
        "products:read",
    ],
}


@pytest.fixture(scope="module")
def admin_token():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        pytest.skip("Admin test credentials are not configured")

    response = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=5,
    )

    assert response.status_code == 200, response.text
    return response.json()


def create_or_login_role_user(admin_token, role_name):
    if not RBAC_PASSWORD:
        pytest.skip("TEST_RBAC_PASSWORD is not configured")

    email_slug = role_name.lower().replace(" ", "-")
    email = f"rbac-{email_slug}@example.com"

    headers = {
        "Authorization": f"Bearer {admin_token['access_token']}"
    }

    create_response = httpx.post(
        f"{BASE_URL}/v1/users",
        headers=headers,
        json={
            "email": email,
            "full_name": f"RBAC Test {role_name}",
            "password": RBAC_PASSWORD,
            "role_names": [role_name],
        },
        timeout=5,
    )

    assert create_response.status_code in (201, 409), create_response.text

    login_response = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={"email": email, "password": RBAC_PASSWORD},
        timeout=5,
    )

    assert login_response.status_code == 200, login_response.text

    return login_response.json()


def test_administrator_can_list_users(admin_token):
    response = httpx.get(
        f"{BASE_URL}/v1/users",
        headers={
            "Authorization": f"Bearer {admin_token['access_token']}"
        },
        timeout=5,
    )

    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.parametrize(
    "role_name,expected_permissions",
    ROLE_PERMISSIONS.items(),
)
def test_role_permissions_and_user_listing(
    admin_token,
    role_name,
    expected_permissions,
):
    tokens = create_or_login_role_user(admin_token, role_name)

    headers = {
        "Authorization": f"Bearer {tokens['access_token']}"
    }

    # Verify assigned role and exact permissions.
    me_response = httpx.get(
        f"{BASE_URL}/v1/auth/me",
        headers=headers,
        timeout=5,
    )

    assert me_response.status_code == 200

    user = me_response.json()

    assert user["roles"] == [role_name]
    assert user["permissions"] == expected_permissions
    assert user["is_superuser"] is False

    # None of these roles should have users:read.
    users_response = httpx.get(
        f"{BASE_URL}/v1/users",
        headers=headers,
        timeout=5,
    )

    assert users_response.status_code == 403

    # Non-admin roles must not be able to create users.
    create_response = httpx.post(
        f"{BASE_URL}/v1/users",
        headers=headers,
        json={
            "email": "unauthorized@example.com",
            "full_name": "Unauthorized User",
            "password": RBAC_PASSWORD,
        },
        timeout=5,
    )

    assert create_response.status_code == 403

    # Clean up the test session.
    logout_response = httpx.post(
        f"{BASE_URL}/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        timeout=5,
    )

    assert logout_response.status_code == 204