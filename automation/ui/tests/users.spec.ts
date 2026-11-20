import { test, expect } from "../fixtures/auth";

test("user management supports search and empty results", async ({ page, authenticatedPage }) => {
  await page.goto("/users");
  await expect(page.getByRole("heading", { name: "User management" })).toBeVisible();
  const search = page.getByRole("textbox", { name: "Search users" });
  await search.fill("no-match-user-management-check@example.test");
  await expect(page.getByText("No users match these filters.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Create user" }).first()).toBeVisible();
});
