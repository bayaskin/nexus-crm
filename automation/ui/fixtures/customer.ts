import { test as base, expect } from "./auth";
import { createCustomerEmail } from "./customer-email.mjs";

type CustomerFixtures = {
  customerProfile: {
    name: string;
    id: string;
  };
};

export const test = base.extend<CustomerFixtures>({
  customerProfile: async ({ page, authenticatedPage }, use, testInfo) => {
    const customerName =
      `Playwright Customer ${Date.now()}-${testInfo.workerIndex}`;

    await page.goto("/customers");

    await page.getByRole("button", { name: "Add customer" }).click();

    const dialog = page.getByRole("dialog");

    await dialog.getByLabel(/Customer name/).fill(customerName);
    await dialog.getByLabel("Email").fill(
      createCustomerEmail(testInfo.workerIndex),
    );

    await dialog.getByRole("button", { name: "Create customer" }).click();

    const customerRow = page
      .getByRole("row")
      .filter({ hasText: customerName });

    await expect(customerRow).toBeVisible();

    await customerRow.getByRole("link", { name: "View" }).click();

    await expect(
    page.getByRole("heading", {
        level: 1,
        name: customerName,
    }),
    ).toBeVisible();

    const customerId = page.url().split("/").pop();

    if (!customerId) {
      throw new Error("Could not determine created customer ID");
    }

    await use({
      id: customerId,
      name: customerName,
    });
  },
});

export { expect };
