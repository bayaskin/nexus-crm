
import { test, expect } from "../fixtures/order";

async function getProductStock(
  page: import("@playwright/test").Page,
  request: import("@playwright/test").APIRequestContext,
  productId: string,
): Promise<number> {
  const accessToken = await page.evaluate(() =>
    localStorage.getItem("nexus_access_token"),
  );

  if (!accessToken) {
    throw new Error("Authenticated access token was not found");
  }

  const response = await request.get(
    `/api/v1/inventory/${productId}`,
    {
      headers: {
        Authorization: `Bearer ${accessToken}`,
      },
    },
  );

  if (!response.ok()) {
    throw new Error(
      `Inventory lookup failed: ${response.status()} ${await response.text()}`,
    );
  }

  const inventory = await response.json();
  return inventory.quantity_on_hand;
}

test.describe("Order inventory management", () => {
  test("does not deduct inventory when creating a draft order", async ({
    page,
    request,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    const stock = await getProductStock(
      page,
      request,
      testOrder.product.id,
    );

    expect(stock).toBe(testOrder.product.initialStock);

    // Draft orders must not reserve or deduct inventory.
    expect(
      await getProductStock(page, request, testOrder.product.id),
    ).toBe(testOrder.product.initialStock);
  });

  test("deducts inventory when confirming an order", async ({
    page,
    request,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    const initialStock = await getProductStock(
      page,
      request,
      testOrder.product.id,
    );

    await page.getByRole("button", {
      name: "Confirm order",
    }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    await expect.poll(
      () => getProductStock(page, request, testOrder.product.id),
    ).toBe(initialStock - 1);
  });

  test("restores inventory when cancelling a confirmed order", async ({
    page,
    request,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    const initialStock = await getProductStock(
      page,
      request,
      testOrder.product.id,
    );

    // Confirm order: stock should decrease by one.
    await page.getByRole("button", {
      name: "Confirm order",
    }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    await expect.poll(
      () => getProductStock(page, request, testOrder.product.id),
    ).toBe(initialStock - 1);

    // Cancel confirmed order: stock should be restored.
    await page.getByRole("button", {
      name: "Cancel order",
    }).click();

    await expect(
      page.locator(".status-badge.status-cancelled"),
    ).toHaveCount(2);

    await expect.poll(
      () => getProductStock(page, request, testOrder.product.id),
    ).toBe(initialStock);
  });

  test("rejects confirmation when inventory is insufficient", async ({
    page,
    request,
    customerProfile,
    createProduct,
  }) => {
    // Create an active product with zero inventory.
    const product = await createProduct({
      stock: 0,
    });

    // Create a draft order for one unit.
    await page.goto("/orders/new");

    await page.getByLabel("Customer").selectOption(customerProfile.id);
    await page.getByLabel("Product").selectOption(product.id);

    await page.getByRole("button", {
      name: "Create draft order",
    }).click();

    await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/i);

    // Attempt to confirm the order.
    page.on("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", {
      name: "Confirm order",
    }).click();

    // Verify the application displays the inventory error.
    const stockError = page.locator(
      "section.stock-error-panel[role='alert']",
    );

    await expect(stockError).toBeVisible();
    await expect(stockError).toContainText("Insufficient inventory");
    await expect(stockError).toContainText(
      "Order remains in Draft. No inventory was deducted.",
    );
    await expect(stockError).toContainText(product.name);

    // The order must remain in Draft.
    await expect(
      page.locator(".status-badge.status-draft"),
    ).toHaveCount(2);

    // No inventory should have been deducted.
    expect(
      await getProductStock(page, request, product.id),
    ).toBe(0);
  });
});
