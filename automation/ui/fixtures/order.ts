
import { test as base, expect } from "./product";
import type { TestProduct } from "./product";

type OrderFixtures = {
  testOrder: {
    id: string;
    product: TestProduct;
  };
};

export const test = base.extend<OrderFixtures>({
  testOrder: async (
    { page, createProduct, customerProfile },
    use,
  ) => {
    const product = await createProduct();

    await page.goto("/orders/new");

    await page.getByLabel("Customer").selectOption(customerProfile.id);
    await page.getByLabel("Product").selectOption(product.id);

    await page.getByRole("button", {
      name: "Create draft order",
    }).click();

    await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/i);

    const orderId = page.url().split("/").pop();

    if (!orderId) {
      throw new Error(`Could not extract order ID from URL: ${page.url()}`);
    }

    await expect(
      page.locator(".status-badge.status-draft").first(),
    ).toBeVisible();

    await use({
      id: orderId,
      product,
    });
  },
});

export { expect };
