
import { test, expect } from "../fixtures/order";

test.describe("Order workflows", () => {
  test("creates a draft order with multiple products", async ({
    page,
    customerProfile,
    createProduct,
  }) => {
    const first = await createProduct({
      price: "19.99",
      stock: 20,
    });

    const second = await createProduct({
      price: "29.50",
      stock: 20,
    });

    await page.goto("/orders/new");

    await page.getByLabel("Customer").selectOption(customerProfile.id);

    await page.getByLabel("Product").nth(0).selectOption(first.id);

    await page.getByRole("button", {
      name: "Add product",
    }).click();

    await page.getByLabel("Product").nth(1).selectOption(second.id);

    const quantities = page.locator('input[type="number"]');
    await quantities.nth(0).fill("2");
    await quantities.nth(1).fill("3");

    await expect(page.getByText("$128.48")).toBeVisible();

    await page.getByRole("button", {
      name: "Create draft order",
    }).click();

    await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/i);

    await expect(
      page.locator(".status-badge.status-draft"),
    ).toHaveCount(2);

    const itemsTable = page.locator("table").filter({
      hasText: first.sku,
    });

    await expect(itemsTable).toContainText(first.name);
    await expect(itemsTable).toContainText(second.name);
    await expect(page.getByText("$128.48")).toBeVisible();
  });

  test("confirms, ships, and delivers an order", async ({
    page,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", { name: "Confirm order" }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    await page.getByRole("button", { name: "Mark shipped" }).click();

    await expect(
      page.locator(".status-badge.status-shipped"),
    ).toHaveCount(2);

    await page.getByRole("button", { name: "Mark delivered" }).click();

    await expect(
      page.locator(".status-badge.status-delivered"),
    ).toHaveCount(2);

    await expect(
      page.getByRole("button", { name: "Mark shipped" }),
    ).toHaveCount(0);

    await expect(
      page.getByRole("button", { name: "Mark delivered" }),
    ).toHaveCount(0);

    await expect(
      page.getByRole("button", { name: "Cancel order" }),
    ).toHaveCount(0);
  });

  test("cancels a draft order", async ({ page, testOrder }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", { name: "Cancel order" }).click();

    await expect(
      page.locator(".status-badge.status-cancelled"),
    ).toHaveCount(2);

    await expect(
      page.getByRole("button", { name: "Confirm order" }),
    ).toHaveCount(0);
  });

  test("cancels a confirmed order", async ({ page, testOrder }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", { name: "Confirm order" }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    await page.getByRole("button", { name: "Cancel order" }).click();

    await expect(
      page.locator(".status-badge.status-cancelled"),
    ).toHaveCount(2);

    await expect(
      page.getByRole("button", { name: "Mark shipped" }),
    ).toHaveCount(0);
  });

  test("does not allow cancellation after shipping", async ({
    page,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", (dialog) => dialog.accept());

    await page.getByRole("button", { name: "Confirm order" }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    await page.getByRole("button", { name: "Mark shipped" }).click();

    await expect(
      page.locator(".status-badge.status-shipped"),
    ).toHaveCount(2);

    await expect(
      page.getByRole("button", { name: "Cancel order" }),
    ).toHaveCount(0);
  });
});
