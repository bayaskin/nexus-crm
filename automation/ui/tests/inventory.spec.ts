import { test, expect } from "../fixtures/product";

async function accessToken(page: import("@playwright/test").Page) {
  const token = await page.evaluate(() => localStorage.getItem("nexus_access_token"));
  if (!token) throw new Error("Authenticated access token was not found");
  return token;
}

async function inventoryBalance(request: import("@playwright/test").APIRequestContext, token: string, productId: string) {
  const response = await request.get(`/api/v1/inventory/${productId}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok()) throw new Error(`Inventory lookup failed: ${response.status()} ${await response.text()}`);
  return response.json() as Promise<{ quantity_on_hand: number; reorder_level: number }>;
}

async function searchInventory(page: import("@playwright/test").Page, term: string) {
  await page.getByRole("textbox", { name: "Search inventory" }).fill(term);
}

test.describe("Inventory management", () => {
  test("loads and displays product inventory", async ({ page, createProduct }) => {
    const product = await createProduct({ name: "Inventory page load product" });
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    const row = page.getByRole("row").filter({ hasText: product.sku });
    await expect(row).toBeVisible();
    await expect(row).toContainText("20");
    await expect(row).toContainText("MAIN");
  });

  test("searches inventory through the API", async ({ page, createProduct }) => {
    const product = await createProduct({ name: "Searchable inventory product" });
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    await expect(page.getByRole("row").filter({ hasText: product.sku })).toBeVisible();
    await expect(page.getByText("No inventory found.")).toHaveCount(0);
  });

  test("filters low stock through the API", async ({ page, request, createProduct }) => {
    const product = await createProduct({ stock: 0, name: "Low stock test product" });
    const token = await accessToken(page);
    const updated = await request.patch(`/api/v1/inventory/${product.id}/reorder-level`, { headers: { Authorization: `Bearer ${token}` }, data: { reorder_level: 2 } });
    expect(updated.ok()).toBeTruthy();
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    await page.getByLabel("Low stock only").check();
    await expect(page.getByRole("row").filter({ hasText: product.sku })).toBeVisible();
    await expect(page.getByRole("row").filter({ hasText: product.sku })).toContainText("Low stock");
  });

  test("paginates inventory results", async ({ page, createProduct }) => {
    const marker = `Pagination-${Date.now()}`;
    for (let i = 0; i < 21; i += 1) await createProduct({ name: `${marker} ${i}` });
    await page.goto("/inventory");
    await searchInventory(page, marker);
    await expect(page.locator(".pagination")).toContainText("1 / 2");
    await page.getByRole("button", { name: "Next page" }).click();
    await expect(page.locator(".pagination")).toContainText("2 / 2");
  });

  test("positive adjustment increases quantity", async ({ page, request, createProduct }) => {
    const product = await createProduct({ name: "Positive adjustment product" });
    const token = await accessToken(page);
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    const row = page.getByRole("row").filter({ hasText: product.sku });
    await row.getByRole("button", { name: "Adjust" }).click();
    await page.getByLabel("Quantity change *").fill("5");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(row.locator("td").nth(2)).toHaveText("25");
    expect((await inventoryBalance(request, token, product.id)).quantity_on_hand).toBe(25);
  });

  test("negative adjustment works when stock is sufficient", async ({ page, request, createProduct }) => {
    const product = await createProduct({ stock: 8, name: "Negative adjustment product" });
    const token = await accessToken(page);
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    const row = page.getByRole("row").filter({ hasText: product.sku });
    await row.getByRole("button", { name: "Adjust" }).click();
    await page.getByLabel("Quantity change *").fill("-3");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(row.locator("td").nth(2)).toHaveText("5");
    expect((await inventoryBalance(request, token, product.id)).quantity_on_hand).toBe(5);
  });

  test("insufficient removal shows an error and preserves balance", async ({ page, request, createProduct }) => {
    const product = await createProduct({ stock: 2, name: "Insufficient adjustment product" });
    const token = await accessToken(page);
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    await page.getByRole("row").filter({ hasText: product.sku }).getByRole("button", { name: "Adjust" }).click();
    await page.getByLabel("Quantity change *").fill("-3");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await expect(page.getByRole("alert")).toContainText("Insufficient stock");
    expect((await inventoryBalance(request, token, product.id)).quantity_on_hand).toBe(2);
  });

  test("updates reorder level", async ({ page, request, createProduct }) => {
    const product = await createProduct({ name: "Reorder level product" });
    const token = await accessToken(page);
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    const row = page.getByRole("row").filter({ hasText: product.sku });
    await row.getByRole("button", { name: "Edit" }).click();
    await page.getByLabel(`Reorder level for ${product.name}`).fill("7");
    await row.getByRole("button", { name: "Save" }).click();
    await expect(row.locator("td").nth(3)).toHaveText("7Edit");
    expect((await inventoryBalance(request, token, product.id)).reorder_level).toBe(7);
  });

  test("movement history includes the adjustment", async ({ page, createProduct }) => {
    const product = await createProduct({ name: "Movement history product" });
    await page.goto("/inventory");
    await searchInventory(page, product.sku);
    const row = page.getByRole("row").filter({ hasText: product.sku });
    await row.getByRole("button", { name: "Adjust" }).click();
    await page.getByLabel("Quantity change *").fill("4");
    await page.getByLabel("Reference ID").fill("PW-ADJUSTMENT-REF");
    await page.getByRole("button", { name: "Save adjustment" }).click();
    await row.getByRole("button", { name: "History" }).click();
    const dialog = page.getByRole("dialog", { name: "Movement history" });
    await expect(dialog).toContainText("+4 units");
    await expect(dialog).toContainText("Purchase receipt");
    await expect(dialog).toContainText("PW-ADJUSTMENT-REF");
  });
});
