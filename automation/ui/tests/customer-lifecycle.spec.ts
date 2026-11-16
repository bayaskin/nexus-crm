
import { test, expect } from "../fixtures/customer";

test.describe("Customer lifecycle", () => {
  test("edits customer details", async ({ page, customerProfile }) => {
    await page.goto("/customers");

    const row = page.getByRole("row").filter({
      hasText: customerProfile.name,
    });

    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Edit" }).click();

    const dialog = page.getByRole("dialog");

    await expect(dialog).toBeVisible();

    const updatedName = `${customerProfile.name} Updated`;
    const updatedEmail = `updated-${Date.now()}@example.com`;

    await dialog.getByLabel(/Customer name/).fill(updatedName);
    await dialog.getByLabel("Email").fill(updatedEmail);
    await dialog.getByLabel("Phone").fill("312-555-0199");
    await dialog.getByLabel("Status").fill("preferred");
    await dialog.getByLabel("Notes").fill("Updated by Playwright lifecycle test");

    await dialog.getByRole("button", { name: "Save changes" }).click();

    const updatedRow = page.getByRole("row").filter({
      hasText: updatedName,
    });

    await expect(updatedRow).toBeVisible();
    await expect(updatedRow).toContainText(updatedEmail);
    await expect(updatedRow).toContainText("preferred");
  });

  test("searches customers by name and handles no results", async ({
    page,
    customerProfile,
  }) => {
    await page.goto("/customers");

    const search = page.getByPlaceholder(
      "Search name, email or phone..."
    );

    await search.fill(customerProfile.name);

    const matchingRow = page.getByRole("row").filter({
      hasText: customerProfile.name,
    });

    await expect(matchingRow).toBeVisible();

    await search.fill("No Such Customer 987654321");

    await expect(
      page.getByText("No customers found.")
    ).toBeVisible();

    await expect(matchingRow).not.toBeVisible();
  });

  test("filters customers by account status", async ({
    page,
    customerProfile,
  }) => {
    await page.goto("/customers");

    const search = page.getByPlaceholder(
      "Search name, email or phone..."
    );

    await search.fill(customerProfile.name);

    const statusFilter = page.getByRole("combobox", {
      name: "Filter customers by account status",
    });

    const customerRow = page.getByRole("row").filter({
      hasText: customerProfile.name,
    });

    // The fixture creates an active customer.
    await expect(customerRow).toBeVisible();

    // Inactive filter should exclude this customer.
    await statusFilter.selectOption("inactive");

    await expect(
      page.getByText("No customers found.")
    ).toBeVisible();

    // All filter should include the customer again.
    await statusFilter.selectOption("all");

    await expect(customerRow).toBeVisible();

    // Active filter should include the customer.
    await statusFilter.selectOption("active");

    await expect(customerRow).toBeVisible();
  });

  test("deactivates a customer and verifies account filtering", async ({
    page,
    customerProfile,
  }) => {
    await page.goto("/customers");

    const search = page.getByPlaceholder(
      "Search name, email or phone..."
    );

    await search.fill(customerProfile.name);

    const customerRow = page.getByRole("row").filter({
      hasText: customerProfile.name,
    });

    await expect(customerRow).toBeVisible();

    page.once("dialog", async (dialog) => {
      expect(dialog.message()).toContain(customerProfile.name);
      await dialog.accept();
    });

    await customerRow.getByRole("button", {
      name: "Deactivate",
    }).click();

    // Deactivated customers should disappear from the active list.
    await expect(customerRow).not.toBeVisible();

    const statusFilter = page.getByRole("combobox", {
      name: "Filter customers by account status",
    });

    await statusFilter.selectOption("inactive");

    // The same customer should now appear in the inactive list.
    await expect(customerRow).toBeVisible();

    await statusFilter.selectOption("all");

    // And remain visible when all accounts are requested.
    await expect(customerRow).toBeVisible();
  });
});
