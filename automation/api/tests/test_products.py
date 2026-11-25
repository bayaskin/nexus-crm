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
    assert response.status_code == 200, f"Login failed with HTTP {response.status_code}"
    return response.json()["access_token"]


@pytest.fixture(scope="module")
def admin_headers():
    if not TEST_USER_EMAIL or not TEST_USER_PASSWORD:
        pytest.skip("Admin test credentials are not configured")

    token = login(TEST_USER_EMAIL, TEST_USER_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def product(admin_headers):
    unique = uuid.uuid4().hex[:10]

    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        json={
            "sku": f"QA-{unique}".upper(),
            "name": f"QA Test Product {unique}",
            "description": "Created by automated API tests",
            "price": "19.99",
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

    assert response.status_code == 201, f"Role user creation failed with HTTP {response.status_code}"

    token = login(email, TEST_RBAC_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


def test_create_product_and_retrieve(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/products/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == product["id"]
    assert data["sku"] == product["sku"]
    assert data["name"] == product["name"]
    assert data["price"] == "19.99"
    assert data["is_active"] is True


def test_product_search_by_name(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        params={"search": product["name"]},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert data["items"][0]["id"] == product["id"]


def test_product_search_by_sku(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        params={"search": product["sku"]},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert data["items"][0]["sku"] == product["sku"]


def test_product_search_is_case_insensitive(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        params={"search": product["sku"].lower()},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_product_list_pagination(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        params={"search": product["name"], "skip": 0, "limit": 10},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["skip"] == 0
    assert data["limit"] == 10


def test_product_partial_update(product, admin_headers):
    response = httpx.patch(
        f"{BASE_URL}/v1/products/{product['id']}",
        headers=admin_headers,
        json={
            "name": "Updated QA Product",
            "price": "29.50",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["name"] == "Updated QA Product"
    assert data["price"] == "29.50"
    assert data["sku"] == product["sku"]


def test_duplicate_sku_returns_409(product, admin_headers):
    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        json={
            "sku": product["sku"],
            "name": "Duplicate SKU Product",
            "price": "10.00",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 409


def test_sku_is_normalized_before_creation(admin_headers):
    unique = uuid.uuid4().hex[:10]
    sku = f"qa-{unique}"

    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        json={
            "sku": sku,
            "name": "SKU Normalization Test",
            "price": "12.00",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 201, response.text
    assert response.json()["sku"] == sku.upper()


@pytest.mark.parametrize("price", ["-1.00", "19.999"])
def test_invalid_price_returns_422(admin_headers, price):
    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        json={
            "sku": f"QA-INVALID-{uuid.uuid4().hex[:10]}",
            "name": "Invalid Price Product",
            "price": price,
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 422


def test_blank_sku_returns_422(admin_headers):
    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        json={
            "sku": "   ",
            "name": "Blank SKU Product",
            "price": "10.00",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 422


def test_missing_product_returns_404(admin_headers):
    missing_id = str(uuid.uuid4())

    response = httpx.get(
        f"{BASE_URL}/v1/products/{missing_id}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 404


def test_deactivate_product_excludes_it_from_list(
    product, admin_headers
):
    product_id = product["id"]

    response = httpx.post(
        f"{BASE_URL}/v1/products/{product_id}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False

    detail = httpx.get(
        f"{BASE_URL}/v1/products/{product_id}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert detail.status_code == 404

    listing = httpx.get(
        f"{BASE_URL}/v1/products",
        headers=admin_headers,
        params={"search": product["sku"]},
        timeout=TIMEOUT,
    )

    assert listing.status_code == 200
    assert listing.json()["total"] == 0


def test_read_only_role_can_read_but_cannot_create_or_update(
    product, admin_headers
):
    headers = create_role_headers("Read-only", admin_headers)

    read_response = httpx.get(
        f"{BASE_URL}/v1/products/{product['id']}",
        headers=headers,
        timeout=TIMEOUT,
    )
    assert read_response.status_code == 200

    create_response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=headers,
        json={
            "sku": f"QA-FORBIDDEN-{uuid.uuid4().hex[:8]}",
            "name": "Forbidden Product",
            "price": "10.00",
        },
        timeout=TIMEOUT,
    )
    assert create_response.status_code == 403

    update_response = httpx.patch(
        f"{BASE_URL}/v1/products/{product['id']}",
        headers=headers,
        json={"name": "Forbidden Update"},
        timeout=TIMEOUT,
    )
    assert update_response.status_code == 403
