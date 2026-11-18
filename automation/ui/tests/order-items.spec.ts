
import { test, expect } from "../fixtures/order";

test.describe("Order item management", () => {
  test("removes an item from a draft order and recalculates the total", async ({
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

    // Create a draft order containing two products.
    await page.goto("/orders/new");

    await page.getByLabel("Customer").selectOption(customerProfile.id);
    await page.getByLabel("Product").nth(0).selectOption(first.id);

    await page.getByRole("button", {
      name: "Add product",
    }).click();

    await page.getByLabel("Product").nth(1).selectOption(second.id);

    await page.getByRole("button", {
      name: "Create draft order",
    }).click();

    await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/i);

    await expect(page.getByText(first.name)).toBeVisible();
    await expect(page.getByText(second.name)).toBeVisible();
    await expect(page.getByText("$49.49")).toBeVisible();

    // Locate individual product rows.
    const firstItemRow = page.getByRole("row").filter({
      hasText: first.sku,
    });

    const secondItemRow = page.getByRole("row").filter({
      hasText: second.sku,
    });

    // Verify both products initially exist.
    await expect(firstItemRow).toHaveCount(1);
    await expect(secondItemRow).toHaveCount(1);

    // Locate the remove button.
    const removeButton = page.getByRole("button", {
      name: `Remove ${first.name} from order`,
    });

    await expect(removeButton).toBeVisible();
    await expect(removeButton).toBeEnabled();

    // Accept confirmation dialogs if displayed.
    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    // Register the response listener before clicking.
    const deleteResponsePromise = page.waitForResponse((response) => {
      return (
        response.request().method() === "DELETE" &&
        response.url().includes(`/items/${first.id}`)
      );
    });

    await removeButton.click();

    // Wait for the DELETE request to complete.
    const deleteResponse = await deleteResponsePromise;

    console.log("DELETE status:", deleteResponse.status());

    expect(
      deleteResponse.ok(),
      `Item removal failed with HTTP ${deleteResponse.status()}`,
    ).toBeTruthy();

    // Verify the removed product is gone.
    await expect(firstItemRow).toHaveCount(0);

    // Verify the remaining product is still present.
    await expect(secondItemRow).toHaveCount(1);

    // Verify the order total was recalculated.
    await expect(
      page.locator(".order-total-row"),
    ).toContainText("$29.50");

    // Order must remain in Draft status.
    await expect(
      page.locator(".status-badge.status-draft"),
    ).toHaveCount(2);
  });

  test("does not allow removing the last item from a draft order", async ({
    page,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    const removeButton = page.getByRole("button", {
      name: `Remove ${testOrder.product.name} from order`,
    });

    // The last remaining item cannot be removed.
    await expect(removeButton).toBeDisabled();

    await expect(page.getByText(testOrder.product.name)).toBeVisible();
  });

  test("does not allow removing items after order confirmation", async ({
    page,
    testOrder,
  }) => {
    await page.goto(`/orders/${testOrder.id}`);

    page.on("dialog", async (dialog) => {
      await dialog.accept();
    });

    await page.getByRole("button", {
      name: "Confirm order",
    }).click();

    await expect(
      page.locator(".status-badge.status-confirmed"),
    ).toHaveCount(2);

    // Item removal must no longer be available.
    await expect(
      page.getByRole("button", {
        name: `Remove ${testOrder.product.name} from order`,
      }),
    ).toHaveCount(0);

    await expect(page.getByText(testOrder.product.name)).toBeVisible();
  });
});
