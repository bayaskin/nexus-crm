
import { apiRequest } from "./client";
import type {
  Order,
  OrderStatus,
  PaginatedResponse,
} from "../types";

export interface OrderItemInput {
  product_id: string;
  quantity: number;
}

export interface OrderCreateInput {
  customer_id: string;
  items: OrderItemInput[];
}

export async function getOrders(
  status?: OrderStatus,
  skip = 0,
  limit = 20,
  customerId?: string,
) {
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(limit),
  });

  if (status) params.set("status", status);
  if (customerId) params.set("customer_id", customerId);

  return apiRequest<PaginatedResponse<Order>>(
    `/v1/orders?${params}`,
  );
}

export function getOrder(orderId: string) {
  return apiRequest<Order>(`/v1/orders/${orderId}`);
}

export function createOrder(payload: OrderCreateInput) {
  return apiRequest<Order>("/v1/orders", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateOrder(
  orderId: string,
  payload: Partial<OrderCreateInput>,
) {
  return apiRequest<Order>(`/v1/orders/${orderId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function performOrderAction(
  orderId: string,
  action: "confirm" | "ship" | "deliver" | "cancel",
) {
  return apiRequest<Order>(
    `/v1/orders/${orderId}/${action}`,
    { method: "POST" },
  );
}

export function removeOrderItem(
  orderId: string,
  productId: string,
) {
  return apiRequest<Order>(
    `/v1/orders/${orderId}/items/${productId}`,
    { method: "DELETE" },
  );
}