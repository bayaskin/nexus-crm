
import os
import uuid

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL")
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD")
TEST_RBAC_PASSWORD = os.getenv("TEST_RBAC_PASSWORD")

TIMEOUT = 10


def login(email: str, password: str) -> str:
    response = httpx.post(
        f"{BASE_URL}/v1/auth/login",
        json={"email": email, "password": password},
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def admin_headers():
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        pytest.skip("Admin test credentials are not configured")

    token = login(TEST_USER_EMAIL, TEST_USER_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def customer(admin_headers):
    unique = uuid.uuid4().hex[:10]
    response = httpx.post(
        f"{BASE_URL}/v1/customers",
        headers=admin_headers,
        json={
            "name": f"QA Customer {unique}",
            "email": f"qa-{unique}@example.com",
            "phone": "+1-312-555-0100",
            "status": "active",
            "notes": "Created by automated API tests",
        },
        timeout=TIMEOUT,
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_role_headers(role_name: str, admin_headers: dict) -> dict:
    if not TEST_RBAC_PASSWORD:
        pytest.skip("TEST_RBAC_PASSWORD is not configured")

    unique = uuid.uuid4().hex[:10]
    email = f"rbac-{role_name.lower().replace(' ', '-')}-{unique}@example.com"

    response = httpx.post(
        f"{BASE_URL}/v1/users",
        headers=admin_headers,
        json={
            "email": email,
            "full_name": f"QA {role_name}",
            "password": TEST_RBAC_PASSWORD,
            "role_names": [role_name],
        },
        timeout=TIMEOUT,
    )
    assert response.status_code == 201, response.text

    token = login(email, TEST_RBAC_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


def test_create_customer_and_retrieve(customer, admin_headers):
    customer_id = customer["id"]

    response = httpx.get(
        f"{BASE_URL}/v1/customers/{customer_id}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert response.json()["id"] == customer_id
    assert response.json()["name"] == customer["name"]
    assert response.json()["is_active"] is True


def test_customer_search_and_pagination(customer, admin_headers):
    name = customer["name"]

    response = httpx.get(
        f"{BASE_URL}/v1/customers",
        headers=admin_headers,
        params={"search": name, "skip": 0, "limit": 10},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == name
    assert data["skip"] == 0
    assert data["limit"] == 10


def test_customer_partial_update(customer, admin_headers):
    customer_id = customer["id"]

    response = httpx.patch(
        f"{BASE_URL}/v1/customers/{customer_id}",
        headers=admin_headers,
        json={
            "phone": "+1-312-555-0199",
            "notes": "Updated by automated test",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["phone"] == "+1-312-555-0199"
    assert data["notes"] == "Updated by automated test"
    assert data["name"] == customer["name"]


def test_invalid_customer_email_returns_422(admin_headers):
    response = httpx.post(
        f"{BASE_URL}/v1/customers",
        headers=admin_headers,
        json={
            "name": "Invalid Email Customer",
            "email": "not-an-email",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 422


def test_missing_customer_returns_404(admin_headers):
    missing_id = str(uuid.uuid4())

    response = httpx.get(
        f"{BASE_URL}/v1/customers/{missing_id}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 404


def test_contact_creation_and_primary_contact_replacement(
    customer, admin_headers
):
    customer_id = customer["id"]
    contacts_url = f"{BASE_URL}/v1/customers/{customer_id}/contacts"

    first = httpx.post(
        contacts_url,
        headers=admin_headers,
        json={
            "first_name": "John",
            "last_name": "Smith",
            "email": "john@example.com",
            "is_primary": True,
        },
        timeout=TIMEOUT,
    )
    assert first.status_code == 201, first.text
    first_contact_id = first.json()["id"]

    second = httpx.post(
        contacts_url,
        headers=admin_headers,
        json={
            "first_name": "Jane",
            "last_name": "Doe",
            "email": "jane@example.com",
            "is_primary": True,
        },
        timeout=TIMEOUT,
    )
    assert second.status_code == 201, second.text

    listing = httpx.get(
        contacts_url,
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert listing.status_code == 200

    contacts = listing.json()
    primary_contacts = [item for item in contacts if item["is_primary"]]

    assert len(contacts) == 2
    assert len(primary_contacts) == 1
    assert primary_contacts[0]["id"] == second.json()["id"]

    first_contact = next(
        item for item in contacts if item["id"] == first_contact_id
    )
    assert first_contact["is_primary"] is False


def test_deactivate_customer_excludes_it_from_default_list(
    customer, admin_headers
):
    customer_id = customer["id"]

    response = httpx.post(
        f"{BASE_URL}/v1/customers/{customer_id}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False

    detail = httpx.get(
        f"{BASE_URL}/v1/customers/{customer_id}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert detail.status_code == 404

    listing = httpx.get(
        f"{BASE_URL}/v1/customers",
        headers=admin_headers,
        params={"search": customer["name"]},
        timeout=TIMEOUT,
    )
    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_read_only_role_can_read_but_cannot_create_or_update(
    customer, admin_headers
):
    headers = create_role_headers("Read-only", admin_headers)

    read_response = httpx.get(
        f"{BASE_URL}/v1/customers/{customer['id']}",
        headers=headers,
        timeout=TIMEOUT,
    )
    assert read_response.status_code == 200

    create_response = httpx.post(
        f"{BASE_URL}/v1/customers",
        headers=headers,
        json={"name": "Forbidden Customer"},
        timeout=TIMEOUT,
    )
    assert create_response.status_code == 403

    update_response = httpx.patch(
        f"{BASE_URL}/v1/customers/{customer['id']}",
        headers=headers,
        json={"name": "Forbidden Update"},
        timeout=TIMEOUT,
    )
    assert update_response.status_code == 403


def test_sales_representative_can_create_but_cannot_update(
    customer, admin_headers
):
    headers = create_role_headers("Sales Representative", admin_headers)

    create_response = httpx.post(
        f"{BASE_URL}/v1/customers",
        headers=headers,
        json={"name": f"Sales Rep Customer {uuid.uuid4().hex[:8]}"},
        timeout=TIMEOUT,
    )
    assert create_response.status_code == 201

    update_response = httpx.patch(
        f"{BASE_URL}/v1/customers/{customer['id']}",
        headers=headers,
        json={"name": "Forbidden Sales Rep Update"},
        timeout=TIMEOUT,
    )
    assert update_response.status_code == 403
