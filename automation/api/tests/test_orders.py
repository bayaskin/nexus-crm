
import os
import uuid

import httpx
import pytest

from concurrent.futures import ThreadPoolExecutor

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


def create_customer(headers: dict) -> dict:
    unique = uuid.uuid4().hex[:10]

    response = httpx.post(
        f"{BASE_URL}/v1/customers",
        headers=headers,
        json={
            "name": f"QA Order Customer {unique}",
            "email": f"order-{unique}@example.com",
            "status": "active",
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 201, response.text
    return response.json()


def create_product(headers: dict, price: str = "19.99") -> dict:
    unique = uuid.uuid4().hex[:10]

    response = httpx.post(
        f"{BASE_URL}/v1/products",
        headers=headers,
        json={
            "sku": f"ORDER-QA-{unique}",
            "name": f"Order QA Product {unique}",
            "description": "Created by order API tests",
            "price": price,
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def customer(admin_headers):
    return create_customer(admin_headers)


@pytest.fixture
def product(admin_headers):
    return create_product(admin_headers)


@pytest.fixture
def second_product(admin_headers):
    return create_product(admin_headers, price="29.50")


def create_order(
    headers: dict,
    customer_id: str,
    items: list[dict],
) -> httpx.Response:
    return httpx.post(
        f"{BASE_URL}/v1/orders",
        headers=headers,
        json={
            "customer_id": customer_id,
            "items": items,
        },
        timeout=TIMEOUT,
    )


def create_role_headers(role_name: str, admin_headers: dict) -> dict:
    if not TEST_RBAC_PASSWORD:
        pytest.skip("TEST_RBAC_PASSWORD is not configured")

    unique = uuid.uuid4().hex[:10]
    email = (
        f"rbac-{role_name.lower().replace(' ', '-')}-"
        f"{unique}@example.com"
    )

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


def get_stock_from_database(product_id: str) -> int:
    import pymysql

    connection = pymysql.connect(
        host=os.getenv("DB_HOST", "mysql"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
    )

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT quantity_on_hand
                FROM inventory_balances
                WHERE product_id = %s
                """,
                (product_id,),
            )
            row = cursor.fetchone()
            assert row is not None, f"No inventory balance for {product_id}"
            return row[0]
    finally:
        connection.close()


def test_create_draft_order_with_multiple_products(
    admin_headers,
    customer,
    product,
    second_product,
):
    response = create_order(
        admin_headers,
        customer["id"],
        [
            {"product_id": product["id"], "quantity": 2},
            {"product_id": second_product["id"], "quantity": 3},
        ],
    )

    assert response.status_code == 201, response.text

    data = response.json()

    assert data["customer_id"] == customer["id"]
    assert data["status"] == "draft"
    assert data["total"] == "128.48"
    assert len(data["items"]) == 2

    items = {item["product_id"]: item for item in data["items"]}

    assert items[product["id"]]["quantity"] == 2
    assert items[product["id"]]["unit_price"] == "19.99"
    assert items[product["id"]]["line_total"] == "39.98"

    assert items[second_product["id"]]["quantity"] == 3
    assert items[second_product["id"]]["unit_price"] == "29.50"
    assert items[second_product["id"]]["line_total"] == "88.50"


def test_draft_order_does_not_deduct_inventory(
    admin_headers,
    customer,
    product,
):
    before = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert before.status_code == 200, before.text

    response = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 5}],
    )

    assert response.status_code == 201, response.text

    after = httpx.get(
        f"{BASE_URL}/v1/inventory/{product['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert after.status_code == 200, after.text

    assert after.json()["quantity_on_hand"] == before.json()["quantity_on_hand"]


def test_order_price_is_snapshotted_at_creation(
    admin_headers,
    customer,
    product,
):
    created = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 2}],
    )

    assert created.status_code == 201, created.text
    order = created.json()

    update_product = httpx.patch(
        f"{BASE_URL}/v1/products/{product['id']}",
        headers=admin_headers,
        json={"price": "99.00"},
        timeout=TIMEOUT,
    )

    assert update_product.status_code == 200, update_product.text

    detail = httpx.get(
        f"{BASE_URL}/v1/orders/{order['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert detail.status_code == 200, detail.text

    data = detail.json()

    assert data["items"][0]["unit_price"] == "19.99"
    assert data["total"] == "39.98"


def test_missing_customer_returns_404(
    admin_headers,
    product,
):
    response = create_order(
        admin_headers,
        str(uuid.uuid4()),
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert response.status_code == 404


def test_inactive_customer_cannot_create_order(
    admin_headers,
    customer,
    product,
):
    deactivate = httpx.post(
        f"{BASE_URL}/v1/customers/{customer['id']}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert deactivate.status_code == 200, deactivate.text

    response = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert response.status_code == 404


def test_missing_product_returns_404(
    admin_headers,
    customer,
):
    response = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": str(uuid.uuid4()), "quantity": 1}],
    )

    assert response.status_code == 404


def test_inactive_product_cannot_be_added_to_order(
    admin_headers,
    customer,
    product,
):
    deactivate = httpx.post(
        f"{BASE_URL}/v1/products/{product['id']}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert deactivate.status_code == 200, deactivate.text

    response = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert response.status_code == 409


def test_duplicate_products_are_rejected(
    admin_headers,
    customer,
    product,
):
    response = create_order(
        admin_headers,
        customer["id"],
        [
            {"product_id": product["id"], "quantity": 1},
            {"product_id": product["id"], "quantity": 2},
        ],
    )

    assert response.status_code == 422


@pytest.mark.parametrize("quantity", [0, -1])
def test_invalid_order_quantity_returns_422(
    admin_headers,
    customer,
    product,
    quantity,
):
    response = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": quantity}],
    )

    assert response.status_code == 422


def test_edit_draft_order_replaces_items_and_recalculates_total(
    admin_headers,
    customer,
    product,
    second_product,
):
    created = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 2}],
    )

    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    response = httpx.patch(
        f"{BASE_URL}/v1/orders/{order_id}",
        headers=admin_headers,
        json={
            "items": [
                {
                    "product_id": second_product["id"],
                    "quantity": 4,
                }
            ]
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 200, response.text

    data = response.json()

    assert data["status"] == "draft"
    assert data["total"] == "118.00"
    assert len(data["items"]) == 1
    assert data["items"][0]["product_id"] == second_product["id"]
    assert data["items"][0]["quantity"] == 4
    assert data["items"][0]["unit_price"] == "29.50"


def test_edit_draft_order_customer(
    admin_headers,
    customer,
    product,
):
    another_customer = create_customer(admin_headers)

    created = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert created.status_code == 201, created.text

    order_id = created.json()["id"]

    response = httpx.patch(
        f"{BASE_URL}/v1/orders/{order_id}",
        headers=admin_headers,
        json={"customer_id": another_customer["id"]},
        timeout=TIMEOUT,
    )

    assert response.status_code == 200, response.text
    assert response.json()["customer_id"] == another_customer["id"]


def test_order_detail_returns_404_for_missing_order(admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/orders/{uuid.uuid4()}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )

    assert response.status_code == 404


def test_order_list_filtering_and_pagination(
    admin_headers,
    customer,
    product,
):
    created = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert created.status_code == 201, created.text

    response = httpx.get(
        f"{BASE_URL}/v1/orders",
        headers=admin_headers,
        params={
            "status": "draft",
            "customer_id": customer["id"],
            "skip": 0,
            "limit": 10,
        },
        timeout=TIMEOUT,
    )

    assert response.status_code == 200, response.text

    data = response.json()

    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["customer_id"] == customer["id"]
    assert data["items"][0]["status"] == "draft"
    assert data["skip"] == 0
    assert data["limit"] == 10


def test_invalid_order_status_filter_returns_422(admin_headers):
    response = httpx.get(
        f"{BASE_URL}/v1/orders",
        headers=admin_headers,
        params={"status": "not-a-real-status"},
        timeout=TIMEOUT,
    )

    assert response.status_code == 422


def test_read_only_role_can_read_but_cannot_create_or_update(
    admin_headers,
    customer,
    product,
):
    headers = create_role_headers("Read-only", admin_headers)

    created = create_order(
        admin_headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    read_response = httpx.get(
        f"{BASE_URL}/v1/orders/{order_id}",
        headers=headers,
        timeout=TIMEOUT,
    )

    assert read_response.status_code == 200

    create_response = create_order(
        headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert create_response.status_code == 403

    update_response = httpx.patch(
        f"{BASE_URL}/v1/orders/{order_id}",
        headers=headers,
        json={"items": [{"product_id": product["id"], "quantity": 2}]},
        timeout=TIMEOUT,
    )

    assert update_response.status_code == 403


def test_sales_representative_can_create_but_cannot_update(
    admin_headers,
    customer,
    product,
):
    headers = create_role_headers("Sales Representative", admin_headers)

    created = create_order(
        headers,
        customer["id"],
        [{"product_id": product["id"], "quantity": 1}],
    )

    assert created.status_code == 201, created.text
    order_id = created.json()["id"]

    update_response = httpx.patch(
        f"{BASE_URL}/v1/orders/{order_id}",
        headers=headers,
        json={"items": [{"product_id": product["id"], "quantity": 2}]},
        timeout=TIMEOUT,
    )

    assert update_response.status_code == 403



def adjust_stock(headers: dict, product_id: str, quantity: int) -> httpx.Response:
    return httpx.post(
        f"{BASE_URL}/v1/inventory/adjustments",
        headers=headers,
        json={
            "product_id": product_id,
            "quantity_change": quantity,
            "reason": "correction",
        },
        timeout=TIMEOUT,
    )


def get_stock(headers: dict, product_id: str) -> int:
    response = httpx.get(
        f"{BASE_URL}/v1/inventory/{product_id}",
        headers=headers,
        timeout=TIMEOUT,
    )
    assert response.status_code == 200, response.text
    return response.json()["quantity_on_hand"]


def order_action(
    headers: dict,
    order_id: str,
    action: str,
) -> httpx.Response:
    return httpx.post(
        f"{BASE_URL}/v1/orders/{order_id}/{action}",
        headers=headers,
        timeout=TIMEOUT,
    )


def create_single_item_order(
    headers: dict,
    customer_id: str,
    product_id: str,
    quantity: int = 1,
) -> dict:
    response = create_order(
        headers,
        customer_id,
        [{"product_id": product_id, "quantity": quantity}],
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_confirm_order_deducts_inventory(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 20)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 5
    )

    response = order_action(admin_headers, order["id"], "confirm")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "confirmed"
    assert get_stock(admin_headers, product["id"]) == 15


def test_confirm_order_with_insufficient_stock_changes_nothing(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 3)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 5
    )

    response = order_action(admin_headers, order["id"], "confirm")

    assert response.status_code == 409
    assert get_stock(admin_headers, product["id"]) == 3

    detail = httpx.get(
        f"{BASE_URL}/v1/orders/{order['id']}",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "draft"


def test_confirm_order_is_not_allowed_twice(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 4
    )

    first = order_action(admin_headers, order["id"], "confirm")
    assert first.status_code == 200, first.text

    second = order_action(admin_headers, order["id"], "confirm")
    assert second.status_code == 409

    assert get_stock(admin_headers, product["id"]) == 6


def test_multi_product_confirmation_is_atomic(
    admin_headers, customer, product, second_product
):
    first_receipt = adjust_stock(admin_headers, product["id"], 10)
    second_receipt = adjust_stock(admin_headers, second_product["id"], 1)

    assert first_receipt.status_code == 201, first_receipt.text
    assert second_receipt.status_code == 201, second_receipt.text

    order = create_order(
        admin_headers,
        customer["id"],
        [
            {"product_id": product["id"], "quantity": 5},
            {"product_id": second_product["id"], "quantity": 2},
        ],
    )
    assert order.status_code == 201, order.text

    response = order_action(
        admin_headers, order.json()["id"], "confirm"
    )

    assert response.status_code == 409
    assert get_stock(admin_headers, product["id"]) == 10
    assert get_stock(admin_headers, second_product["id"]) == 1


def test_ship_and_deliver_order(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 2
    )

    confirm = order_action(admin_headers, order["id"], "confirm")
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "confirmed"

    ship = order_action(admin_headers, order["id"], "ship")
    assert ship.status_code == 200, ship.text
    assert ship.json()["status"] == "shipped"

    deliver = order_action(admin_headers, order["id"], "deliver")
    assert deliver.status_code == 200, deliver.text
    assert deliver.json()["status"] == "delivered"

    assert get_stock(admin_headers, product["id"]) == 8


def test_cancel_draft_order_does_not_change_inventory(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 3
    )

    response = order_action(admin_headers, order["id"], "cancel")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert get_stock(admin_headers, product["id"]) == 10


def test_cancel_confirmed_order_restores_inventory(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 4
    )

    confirm = order_action(admin_headers, order["id"], "confirm")
    assert confirm.status_code == 200, confirm.text
    assert get_stock(admin_headers, product["id"]) == 6

    cancel = order_action(admin_headers, order["id"], "cancel")

    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    assert get_stock(admin_headers, product["id"]) == 10


@pytest.mark.parametrize("final_action", ["ship", "deliver"])
def test_cannot_cancel_order_after_shipping(
    admin_headers, customer, product, final_action
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 2
    )

    confirm = order_action(admin_headers, order["id"], "confirm")
    assert confirm.status_code == 200, confirm.text

    ship = order_action(admin_headers, order["id"], "ship")
    assert ship.status_code == 200, ship.text

    if final_action == "deliver":
        deliver = order_action(admin_headers, order["id"], "deliver")
        assert deliver.status_code == 200, deliver.text

    cancel = order_action(admin_headers, order["id"], "cancel")

    assert cancel.status_code == 409
    assert get_stock(admin_headers, product["id"]) == 8


def test_cancel_confirmed_order_restores_stock_for_inactive_product(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 4
    )

    confirm = order_action(admin_headers, order["id"], "confirm")
    assert confirm.status_code == 200, confirm.text
    assert get_stock(admin_headers, product["id"]) == 6

    deactivate = httpx.post(
        f"{BASE_URL}/v1/products/{product['id']}/deactivate",
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert deactivate.status_code == 200, deactivate.text

    cancel = order_action(admin_headers, order["id"], "cancel")

    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    assert get_stock_from_database(product["id"]) == 10


def test_concurrent_confirmation_deducts_inventory_only_once(
    admin_headers, customer, product
):
    receipt = adjust_stock(admin_headers, product["id"], 10)
    assert receipt.status_code == 201, receipt.text

    order = create_single_item_order(
        admin_headers, customer["id"], product["id"], 7
    )

    def confirm():
        return order_action(admin_headers, order["id"], "confirm")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(confirm),
            executor.submit(confirm),
        ]
        responses = [future.result() for future in futures]

    assert sorted(response.status_code for response in responses) == [
        200,
        409,
    ]
    assert get_stock(admin_headers, product["id"]) == 3


