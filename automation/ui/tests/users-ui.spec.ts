import { test, expect, type Page } from "@playwright/test";

const basePermissions = [
  "users:read", "users:create", "users:update", "users:deactivate",
  "users:reset_password", "roles:read", "roles:assign", "permissions:read",
  "permissions:manage",
];

const seedUser = {
  id: "user-1",
  first_name: "Avery",
  last_name: "Admin",
  full_name: "Avery Admin",
  email: "avery@example.com",
  roles: ["Admin"],
  is_active: true,
  last_login_at: "2026-01-05T12:30:00Z",
  created_at: "2025-10-01T10:00:00Z",
  updated_at: "2026-01-05T12:30:00Z",
  password_expires_at: null,
  permission_overrides: { "orders:cancel": "deny", "customers:read": "allow" },
  effective_permissions: ["users:read", "users:update", "customers:read"],
  must_change_password: false,
};

function userAt(index: number) {
  if (index === 0) return { ...seedUser };
  return {
    ...seedUser,
    id: `user-${index + 1}`,
    first_name: `User${index + 1}`,
    last_name: "Example",
    full_name: `User${index + 1} Example`,
    email: `user${index + 1}@example.com`,
    roles: [index % 2 === 0 ? "Admin" : "User"],
    is_active: index % 3 !== 0,
    permission_overrides: {},
  };
}

async function mockUsersPage(page: Page, permissions = basePermissions) {
  let currentUser = { ...seedUser };
  await page.addInitScript(() => localStorage.setItem("nexus_access_token", "ui-review-token"));
  await page.route("**/api/v1/auth/me", route => route.fulfill({ json: {
    id: "actor-1", email: "actor@example.com", full_name: "Review Admin", is_active: true,
    is_superuser: false, roles: ["Admin"], permissions,
  } }));
  await page.route("**/api/v1/users/metadata", route => route.fulfill({ json: {
    roles: [
      { name: "Admin", permissions: ["users:read", "users:update", "customers:read", "orders:cancel"] },
      { name: "User", permissions: ["customers:read"] },
    ],
    permissions: ["users:read", "users:update", "customers:read", "orders:cancel"],
  } }));
  await page.route("**/api/v1/users?**", route => {
    const url = new URL(route.request().url());
    const pageNumber = Number(url.searchParams.get("page") || 1);
    const search = (url.searchParams.get("search") || "").toLowerCase();
    const role = url.searchParams.get("role") || "";
    const status = url.searchParams.get("status") || "";
    let rows = Array.from({ length: 48 }, (_, index) => userAt(index));
    if (search) rows = rows.filter(user => `${user.full_name} ${user.email}`.toLowerCase().includes(search));
    if (role) rows = rows.filter(user => user.roles.includes(role));
    if (status) rows = rows.filter(user => user.is_active === (status === "active"));
    const total = rows.length;
    rows = rows.slice((pageNumber - 1) * 25, pageNumber * 25);
    return route.fulfill({
      status: 200,
      headers: { "X-Total-Count": String(total), "X-Page": String(pageNumber), "X-Page-Size": "25" },
      json: rows,
    });
  });
  await page.route("**/api/v1/users/user-1", async route => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as { first_name?: string; last_name?: string; email?: string };
      currentUser = { ...currentUser, ...body, full_name: `${body.first_name || currentUser.first_name} ${body.last_name || currentUser.last_name}` };
      await route.fulfill({ json: currentUser });
      return;
    }
    await route.fulfill({ json: currentUser });
  });
}

test("user toolbar filters and centered pagination work across desktop and mobile widths", async ({ page }) => {
  await mockUsersPage(page);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/users");
  await expect(page.getByText("Showing 1–25 of 48 users")).toBeVisible();
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
  await expect(page.getByRole("button", { name: "Previous" })).toBeDisabled();

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByText("Page 2 of 2")).toBeVisible();
  await expect(page.getByText("Showing 26–48 of 48 users")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next" })).toBeDisabled();
  await page.getByRole("button", { name: "Previous" }).click();

  await page.getByLabel("Filter by role").selectOption("User");
  await expect(page.getByText("Page 1 of 1")).toBeVisible();
  await expect(page.getByText(/Showing 1–\d+ of 24 users/)).toBeVisible();
  await page.getByLabel("Filter by status").selectOption("inactive");
  await expect(page.getByText(/of 8 users/)).toBeVisible();
  await page.getByLabel("Search users").fill("no-such-person@example.com");
  await expect(page.getByText("No users match these filters.")).toBeVisible();
  await expect(page.getByText("Showing 0–0 of 0 users")).toBeVisible();

  await page.getByLabel("Search users").fill("");
  await page.getByLabel("Filter by role").selectOption("");
  await page.getByLabel("Filter by status").selectOption("");
  await page.setViewportSize({ width: 390, height: 844 });
  const toolbar = page.locator(".user-toolbar");
  await expect(toolbar.getByLabel("Search users")).toBeVisible();
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: window.innerWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
  await expect(page.getByText("Page 1 of 2")).toBeVisible();
});

test("user details dialog closes, displays permission groups, and returns to the selected user after edit", async ({ page }) => {
  await mockUsersPage(page);
  await page.goto("/users");
  await page.getByRole("button", { name: "Avery Admin" }).click();
  const dialog = page.getByRole("dialog", { name: "Avery Admin" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("avery@example.com");
  await expect(dialog).toContainText("Temporary password expires");
  await expect(dialog.getByRole("heading", { name: "Inherited role permissions" })).toBeVisible();
  await expect(dialog.getByText("orders:cancel", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Denied", { exact: true })).toBeVisible();
  await expect(page.locator("body")).toHaveCSS("overflow", "hidden");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();

  await page.getByRole("button", { name: "Avery Admin" }).click();
  const reopened = page.getByRole("dialog", { name: "Avery Admin" });
  await reopened.getByRole("button", { name: "Edit user" }).click();
  await expect(reopened.getByRole("heading", { name: "Edit Avery Admin" })).toBeVisible();
  await reopened.getByLabel("First name").fill("Avery Updated");
  await reopened.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("dialog", { name: "Avery Updated Admin" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("User updated.");
  await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
});

test("Edit User is hidden when the current user lacks users:update", async ({ page }) => {
  await mockUsersPage(page, ["users:read"]);
  await page.goto("/users");
  await page.getByRole("button", { name: "Avery Admin" }).click();
  const dialog = page.getByRole("dialog", { name: "Avery Admin" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Edit user" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toHaveCount(0);
});
