import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL")
TEST_USER_PASSWORD = os.getenv("TEST_USER_PASSWORD")
TEST_RBAC_PASSWORD = os.getenv("TEST_RBAC_PASSWORD")

TIMEOUT = 15


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
            "sku": f"INV-{unique}".upper(),
            "name": f"Inventory QA Product {unique}",
            "price": "25.00",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 201, response.text
    return response.json()


def adjust_stock(headers, product_id, quantity, reason="correction"):
    return httpx.post(
        f"{BASE_URL}/v1/inventory/adjustments",
        headers=headers,
        json={
            "product_id": product_id,
            "quantity_change": quantity,
            "reason": reason,
        },
        timeout=TIMEOUT,
    )


def create_role_headers(role_name, admin_headers):
    if not TEST_RBAC_PASSWORD:
        pytest.skip("TEST_RBAC_PASSWORD is not configured")

    unique = uuid.uuid4().hex[:10]
    email = (
        f"inventory-{role_name.lower().replace(' ', '-')}-"
        f"{unique}@example.com"
    )

    response = httpx.post(
        f"{BASE_URL}/v1/users",
        headers=admin_headers,
        json={
            "email": email,
            "full_name": f"Inventory QA {role_name}",
            "password": TEST_RBAC_PASSWORD,
            "role_names": [role_name],
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 201, f"Role user creation failed with HTTP {response.status_code}"

    token = login(email, TEST_RBAC_PASSWORD)
    return {"Authorization": f"Bearer {token}"}


def test_new_product_has_initial_inventory_balance(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["product_id"] == product["id"]
    assert data["sku"] == product["sku"]
    assert data["quantity_on_hand"] == 0
    assert data["reorder_level"] == 0


def test_receive_stock_increases_balance(product, admin_headers):
    response = adjust_stock(
        admin_headers,
        product["id"],
        50,
        "purchase_receipt",
    )

    assert response.status_code == 201, response.text

    data = response.json()
    assert data["movement"]["quantity_change"] == 50
    assert data["movement"]["reason"] == "purchase_receipt"
    assert data["balance"]["quantity_on_hand"] == 50


def test_remove_stock_decreases_balance(product, admin_headers):
    receipt = adjust_stock(admin_headers, product["id"], 50)
    assert receipt.status_code == 201, receipt.text

    response = adjust_stock(
        admin_headers,
        product["id"],
        -5,
        "damaged",
    )

    assert response.status_code == 201, response.text
    assert response.json()["balance"]["quantity_on_hand"] == 45


def test_negative_stock_is_rejected_without_movement(
    product, admin_headers
):
    response = adjust_stock(admin_headers, product["id"], -1)

    assert response.status_code == 409

    balance = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert balance.status_code == 200
    assert balance.json()["quantity_on_hand"] == 0

    movements = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}/movements",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert movements.status_code == 200
    assert movements.json() == []


def test_zero_quantity_adjustment_is_rejected(product, admin_headers):
    response = adjust_stock(admin_headers, product["id"], 0)

    assert response.status_code == 422


def test_movement_history_records_adjustments(product, admin_headers):
    adjust_stock(admin_headers, product["id"], 50, "purchase_receipt")
    adjust_stock(admin_headers, product["id"], -5, "damaged")

    response = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}/movements",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    movements = response.json()

    assert len(movements) == 2
    assert {item["quantity_change"] for item in movements} == {50, -5}
    assert {item["reason"] for item in movements} == {
        "purchase_receipt",
        "damaged",
    }


def test_reorder_level_and_low_stock_filter(product, admin_headers):
    response = httpx.patch(
        f"{BASE_URL}/v1/inventory/{product['id']}/reorder-level",
        headers=admin_headers,
        json={"reorder_level": 10},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    assert response.json()["reorder_level"] == 10
    assert response.json()["is_low_stock"] is True

    listing = httpx.get(
        f"{BASE_URL}/v1/inventory/low-stock",
        headers=admin_headers,
        params={"limit": 100},
        timeout=TIMEOUT,
    )

    assert listing.status_code == 200
    matching = [
        item for item in listing.json()["items"]
        if item["product_id"] == product["id"]
    ]
    assert len(matching) == 1


def test_inventory_search_and_pagination(product, admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/inventory",
        headers=admin_headers,
        params={
            "search": product["sku"],
            "skip": 0,
            "limit": 10,
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["product_id"] == product["id"]


def test_inactive_product_cannot_receive_stock(product, admin_headers):
    deactivation = httpx.post(
        f"{BASE_URL}/v1/products/{product['id']}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert deactivation.status_code == 200

    response = adjust_stock(admin_headers, product["id"], 10)

    assert response.status_code == 404


def test_read_only_role_can_read_but_cannot_adjust(
    product, admin_headers
):
    headers = create_role_headers("Read-only", admin_headers)

    read_response = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=headers,
        timeout=TIMEOUT,
    )
    assert read_response.status_code == 200

    adjust_response = adjust_stock(headers, product["id"], 10)

    assert adjust_response.status_code == 403


def test_concurrent_adjustments_cannot_oversell(product, admin_headers):
    receipt = adjust_stock(
        admin_headers,
        product["id"],
        50,
        "purchase_receipt",
    )
    assert receipt.status_code == 201, receipt.text

    def remove_thirty():
        return adjust_stock(
            admin_headers,
            product["id"],
            -30,
            "correction",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(remove_thirty),
            executor.submit(remove_thirty),
        ]
        responses = [future.result() for future in futures]

    assert sorted(response.status_code for response in responses) == [
        201,
        409,
    ]

    balance = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert balance.status_code == 200
    assert balance.json()["quantity_on_hand"] == 20

    movements = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}/movements",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert movements.status_code == 200
    assert len(movements.json()) == 2
