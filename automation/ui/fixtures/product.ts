
import { test as base, expect } from "./customer";
import { randomUUID } from "node:crypto";
import type { APIRequestContext } from "@playwright/test";

export interface TestProduct {
  id: string;
  sku: string;
  name: string;
  description: string;
  price: string;
  is_active: boolean;
  initialStock: number;
}

export interface ProductOptions {
  price?: string;
  stock?: number;
  name?: string;
}

export async function createProduct(
  request: APIRequestContext,
  accessToken: string,
  options: ProductOptions = {},
): Promise<TestProduct> {
  const unique = randomUUID().replace(/-/g, "").slice(0, 10);
  const price = options.price ?? "19.99";
  const stock = options.stock ?? 20;
  const name = options.name ?? `Playwright Product ${unique}`;

  const headers = {
    Authorization: `Bearer ${accessToken}`,
  };

  const productResponse = await request.post("/api/v1/products", {
    headers,
    data: {
      sku: `PW-${unique}`.toUpperCase(),
      name,
      description: "Created by Playwright UI tests",
      price,
    },
  });

  if (!productResponse.ok()) {
    throw new Error(
      `Product creation failed: ${productResponse.status()} ${await productResponse.text()}`,
    );
  }

  const product = await productResponse.json();

  if (stock > 0) {
    const inventoryResponse = await request.post(
      "/api/v1/inventory/adjustments",
      {
        headers,
        data: {
          product_id: product.id,
          quantity_change: stock,
          reason: "purchase_receipt",
        },
      },
    );

    if (!inventoryResponse.ok()) {
      throw new Error(
        `Inventory setup failed: ${inventoryResponse.status()} ${await inventoryResponse.text()}`,
      );
    }
  }

  return {
    ...product,
    initialStock: stock,
  };
}

type ProductFixtures = {
  createProduct: (options?: ProductOptions) => Promise<TestProduct>;
};

export const test = base.extend<ProductFixtures>({
  createProduct: async ({ page, request, authenticatedPage }, use) => {
    const accessToken = await page.evaluate(() =>
      localStorage.getItem("nexus_access_token"),
    );

    if (!accessToken) {
      throw new Error("Authenticated access token was not found");
    }

    await use((options = {}) =>
      createProduct(request, accessToken, options),
    );
  },
});

export { expect };
