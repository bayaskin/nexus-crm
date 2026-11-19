import { apiRequest } from "./client";
import type {
  InventoryAdjustmentResponse,
  InventoryBalance,
  InventoryMovement,
  InventoryReason,
  PaginatedResponse,
} from "../types";

export interface InventoryQuery {
  search?: string;
  low_stock_only?: boolean;
  skip?: number;
  limit?: number;
}

export interface InventoryAdjustmentPayload {
  product_id: string;
  quantity_change: number;
  reason: InventoryReason;
  reference_id?: string;
  notes?: string;
  reorder_level?: number;
}

export function listInventory(query: InventoryQuery = {}) {
  const params = new URLSearchParams({
    skip: String(query.skip ?? 0),
    limit: String(query.limit ?? 20),
    low_stock_only: String(query.low_stock_only ?? false),
  });
  if (query.search?.trim()) params.set("search", query.search.trim());
  return apiRequest<PaginatedResponse<InventoryBalance>>(
    `/v1/inventory?${params}`,
  );
}

export function getInventoryMovements(productId: string, limit = 50) {
  return apiRequest<InventoryMovement[]>(
    `/v1/inventory/${productId}/movements?limit=${limit}`,
  );
}

export function createInventoryAdjustment(payload: InventoryAdjustmentPayload) {
  return apiRequest<InventoryAdjustmentResponse>("/v1/inventory/adjustments", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateInventoryReorderLevel(productId: string, reorder_level: number) {
  return apiRequest<InventoryBalance>(`/v1/inventory/${productId}/reorder-level`, {
    method: "PATCH",
    body: JSON.stringify({ reorder_level }),
  });
}
