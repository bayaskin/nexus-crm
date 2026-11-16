import { test, expect } from "../fixtures/customer";

test.describe("Customer contact management", () => {
  test("creates a contact and displays it in the profile", async ({
    page,
    customerProfile,
  }) => {
    await page.getByRole("button", { name: "Add contact" }).click();

    const dialog = page.getByRole("dialog");

    await dialog.getByLabel(/First name/).fill("John");
    await dialog.getByLabel(/Last name/).fill("Smith");
    await dialog.getByLabel("Email").fill("john.smith@example.com");
    await dialog.getByLabel("Phone").fill("312-555-0100");

    await dialog.getByRole("button", { name: "Add contact" }).click();

    const contact = page
      .locator(".customer-contact-item")
      .filter({ hasText: "John Smith" });

    await expect(contact).toBeVisible();
    await expect(contact).toContainText("john.smith@example.com");
    await expect(contact).toContainText("312-555-0100");
    await expect(
      page.getByRole("heading", {
        level: 1,
        name: customerProfile.name,
    })
    ).toBeVisible();
  });

  test("edits an existing contact", async ({ page, customerProfile }) => {
    await page.getByRole("button", { name: "Add contact" }).click();

    let dialog = page.getByRole("dialog");

    await dialog.getByLabel(/First name/).fill("John");
    await dialog.getByLabel(/Last name/).fill("Smith");
    await dialog.getByLabel("Email").fill("john@example.com");

    await dialog.getByRole("button", { name: "Add contact" }).click();

    const editButton = page.getByRole("button", {
      name: "Edit John Smith",
    });

    await expect(editButton).toBeVisible();
    await editButton.click();

    dialog = page.getByRole("dialog");

    await dialog.getByLabel(/First name/).fill("Jonathan");
    await dialog.getByLabel("Phone").fill("312-555-0199");

    await dialog.getByRole("button", { name: "Save contact" }).click();

    const updatedContact = page
      .locator(".customer-contact-item")
      .filter({ hasText: "Jonathan Smith" });

    await expect(updatedContact).toBeVisible();
    await expect(updatedContact).toContainText("312-555-0199");
    await expect(updatedContact).toContainText("john@example.com");
  });

  test("replaces the primary contact", async ({ page, customerProfile }) => {
    await page.getByRole("button", { name: "Add contact" }).click();

    let dialog = page.getByRole("dialog");

    await dialog.getByLabel(/First name/).fill("John");
    await dialog.getByLabel(/Last name/).fill("Smith");
    await dialog.getByLabel("Set as primary contact").check();

    await dialog.getByRole("button", { name: "Add contact" }).click();

    const john = page
      .locator(".customer-contact-item")
      .filter({ hasText: "John Smith" });

    await expect(john).toContainText("Primary");

    await page.getByRole("button", { name: "Add contact" }).click();

    dialog = page.getByRole("dialog");

    await dialog.getByLabel(/First name/).fill("Jane");
    await dialog.getByLabel(/Last name/).fill("Doe");
    await dialog.getByLabel("Set as primary contact").check();

    await dialog.getByRole("button", { name: "Add contact" }).click();

    const jane = page
      .locator(".customer-contact-item")
      .filter({ hasText: "Jane Doe" });

    await expect(jane).toContainText("Primary");
    await expect(john).not.toContainText("Primary");
  });
});