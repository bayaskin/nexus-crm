
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  is_active: boolean;
  is_superuser: boolean;
  roles: string[];
  permissions: string[];
}

export interface Customer {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  status: string;
  notes: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Product {
  id: string;
  sku: string;
  name: string;
  description: string | null;
  price: string;
  is_active: boolean;
}

export interface OrderItem {
  id: string;
  product_id: string;
  quantity: number;
  unit_price: string;
  line_total: string;
}

export type OrderStatus =
  | "draft"
  | "confirmed"
  | "shipped"
  | "delivered"
  | "cancelled";

export interface Order {
  id: string;
  customer_id: string;
  status: OrderStatus;
  total: string;
  items: OrderItem[];
  created_at: string;
  updated_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}
