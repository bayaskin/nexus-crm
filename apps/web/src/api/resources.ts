
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

export interface CustomerPayload {
  name: string;
  email: string | null;
  phone: string | null;
  status: string;
  notes: string | null;
}

export interface CustomerQuery {
  search?: string;
  account_status?: "active" | "inactive" | "all";
  skip?: number;
  limit?: number;
}

export async function listCustomers(query: CustomerQuery = {}) {
  const params = new URLSearchParams({
    skip: String(query.skip ?? 0),
    limit: String(query.limit ?? 20),
    account_status: query.account_status ?? "active",
  });

  if (query.search?.trim()) {
    params.set("search", query.search.trim());
  }

  return apiRequest<PaginatedResponse<Customer>>(
    `/v1/customers?${params}`,
  );
}

export function createCustomer(payload: CustomerPayload) {
  return apiRequest<Customer>("/v1/customers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCustomer(
  customerId: string,
  payload: Partial<CustomerPayload>,
) {
  return apiRequest<Customer>(`/v1/customers/${customerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deactivateCustomer(customerId: string) {
  return apiRequest<Customer>(
    `/v1/customers/${customerId}/deactivate`,
    { method: "POST" },
  );
}

export interface CustomerContact {
  id: string;
  customer_id: string;
  first_name: string;
  last_name: string;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
  created_at: string;
  updated_at: string;
}

export function getCustomer(customerId: string) {
  return apiRequest<Customer>(`/v1/customers/${customerId}`);
}

export function getCustomerContacts(customerId: string) {
  return apiRequest<CustomerContact[]>(
    `/v1/customers/${customerId}/contacts`,
  );
}


export interface CustomerContactPayload {
  first_name: string;
  last_name: string;
  email: string | null;
  phone: string | null;
  is_primary: boolean;
}

export function createCustomerContact(
  customerId: string,
  payload: CustomerContactPayload,
) {
  return apiRequest<CustomerContact>(
    `/v1/customers/${customerId}/contacts`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function updateCustomerContact(
  customerId: string,
  contactId: string,
  payload: Partial<CustomerContactPayload>,
) {
  return apiRequest<CustomerContact>(
    `/v1/customers/${customerId}/contacts/${contactId}`,
    {
      method: "PATCH",
      body: JSON.stringify(payload),
    },
  );
}
