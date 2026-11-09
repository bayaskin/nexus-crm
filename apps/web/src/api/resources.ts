
import { apiRequest } from "./client";
import type {
  Customer,
  PaginatedResponse,
  Product,
} from "../types";

export async function getCustomers(search = "") {
  const params = new URLSearchParams({
    skip: "0",
    limit: "100",
  });

  if (search.trim()) params.set("search", search.trim());

  const result = await apiRequest<PaginatedResponse<Customer>>(
    `/v1/customers?${params}`,
  );

  return result.items;
}

export async function getProducts(search = "") {
  const params = new URLSearchParams({
    skip: "0",
    limit: "100",
  });

  if (search.trim()) params.set("search", search.trim());

  const result = await apiRequest<PaginatedResponse<Product>>(
    `/v1/products?${params}`,
  );

  return result.items;
}
