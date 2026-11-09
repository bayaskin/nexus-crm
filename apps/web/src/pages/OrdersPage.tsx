
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  RefreshCw,
  Search,
} from "lucide-react";

import { getOrders } from "../api/orders";
import { getCustomers } from "../api/resources";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import StatusBadge from "../components/StatusBadge";

import type {
  Customer,
  Order,
  OrderStatus,
} from "../types";

const PAGE_SIZE = 20;

const statuses: Array<{ value: string; label: string }> = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "confirmed", label: "Confirmed" },
  { value: "shipped", label: "Shipped" },
  { value: "delivered", label: "Delivered" },
  { value: "cancelled", label: "Cancelled" },
];

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

function formatMoney(value: string) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

export default function OrdersPage() {
  const { can } = useAuth();

  const [orders, setOrders] = useState<Order[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const [skip, setSkip] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadOrders = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const [orderResult, customerResult] = await Promise.all([
        getOrders(
          status ? (status as OrderStatus) : undefined,
          skip,
          PAGE_SIZE,
        ),
        getCustomers(),
      ]);

      setOrders(orderResult.items);
      setTotal(orderResult.total);
      setCustomers(customerResult);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to load orders.",
      );
    } finally {
      setLoading(false);
    }
  }, [status, skip]);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  const customerMap = new Map(
    customers.map((customer) => [customer.id, customer.name]),
  );

  const filteredOrders = orders.filter((order) => {
    const term = search.toLowerCase().trim();

    if (!term) return true;

    return (
      order.id.toLowerCase().includes(term) ||
      (customerMap.get(order.customer_id) ?? "")
        .toLowerCase()
        .includes(term)
    );
  });

  const page = Math.floor(skip / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="eyebrow">SALES MANAGEMENT</div>
          <h1>Orders</h1>
          <p className="muted">
            Manage customer orders and fulfillment.
          </p>
        </div>

        <div className="heading-actions">
          <button
            className="secondary-button"
            onClick={() => void loadOrders()}
            disabled={loading}
          >
            <RefreshCw size={16} />
            Refresh
          </button>

          {can("orders:create") && (
            <Link to="/orders/new" className="primary-button">
              <Plus size={17} />
              Create order
            </Link>
          )}
        </div>
      </div>

      <section className="metric-grid">
        <article className="metric-card">
          <span>Total orders</span>
          <strong>{total}</strong>
          <small>Matching current filter</small>
        </article>
        <article className="metric-card">
          <span>Current page</span>
          <strong>{orders.length}</strong>
          <small>Orders displayed</small>
        </article>
        <article className="metric-card">
          <span>Page</span>
          <strong>{page} / {pages}</strong>
          <small>Pagination</small>
        </article>
      </section>

      <section className="panel">
        <div className="table-toolbar">
          <div className="search-field">
            <Search size={17} />
            <input
              placeholder="Search order ID or customer on this page..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>

          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setSkip(0);
            }}
            aria-label="Filter by order status"
          >
            {statuses.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>ORDER</th>
                <th>CUSTOMER</th>
                <th>ITEMS</th>
                <th>DATE</th>
                <th>STATUS</th>
                <th className="align-right">TOTAL</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {!loading && filteredOrders.map((order) => (
                <tr key={order.id}>
                  <td>
                    <Link
                      className="order-reference"
                      to={`/orders/${order.id}`}
                    >
                      #{order.id.slice(0, 8).toUpperCase()}
                    </Link>
                  </td>
                  <td>
                    {customerMap.get(order.customer_id) ?? "Unknown customer"}
                  </td>
                  <td>{order.items.length} items</td>
                  <td>{formatDate(order.created_at)}</td>
                  <td><StatusBadge status={order.status} /></td>
                  <td className="align-right amount">
                    {formatMoney(order.total)}
                  </td>
                  <td>
                    <Link
                      to={`/orders/${order.id}`}
                      className="text-link"
                    >
                      View
                    </Link>
                  </td>
                </tr>
              ))}

              {!loading && filteredOrders.length === 0 && (
                <tr>
                  <td colSpan={7} className="empty-state">
                    No orders found.
                  </td>
                </tr>
              )}

              {loading && (
                <tr>
                  <td colSpan={7} className="empty-state">
                    Loading orders...
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="table-footer">
          <span>
            Showing {total === 0 ? 0 : skip + 1}–
            {Math.min(skip + PAGE_SIZE, total)} of {total}
          </span>

          <div className="pagination">
            <button
              className="icon-button"
              disabled={skip === 0 || loading}
              onClick={() => setSkip(Math.max(0, skip - PAGE_SIZE))}
              aria-label="Previous page"
            >
              <ChevronLeft size={18} />
            </button>
            <span>{page} / {pages}</span>
            <button
              className="icon-button"
              disabled={skip + PAGE_SIZE >= total || loading}
              onClick={() => setSkip(skip + PAGE_SIZE)}
              aria-label="Next page"
            >
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
