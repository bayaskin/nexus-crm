import { test as base, expect } from "@playwright/test";

type Fixtures = {
  authenticatedPage: void;
};

export const test = base.extend<Fixtures>({
  authenticatedPage: async ({ page }, use) => {
    const email = process.env.TEST_USER_EMAIL;
    const password = process.env.TEST_USER_PASSWORD;

    if (!email || !password) {
      throw new Error(
        "TEST_USER_EMAIL and TEST_USER_PASSWORD must be configured"
      );
    }

    await page.goto("/");

    await page.getByLabel("Email address").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/orders/);

    await use();
  },
});

export { expect };