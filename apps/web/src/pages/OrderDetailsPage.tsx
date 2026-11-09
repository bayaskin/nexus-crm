
import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  Package,
  Truck,
  XCircle,
  Trash2
} from "lucide-react";

import { getOrder, performOrderAction, removeOrderItem } from "../api/orders";
import { getCustomers, getProducts } from "../api/resources";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import StatusBadge from "../components/StatusBadge";

import type { Customer, Order, Product } from "../types";

interface InsufficientStockError {
  code: "insufficient_stock";
  message: string;
  product_id: string;
  sku: string;
  product_name: string;
  available: number;
  requested: number;
}

function money(value: string | number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

function dateTime(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function OrderDetailsPage() {
  const { orderId } = useParams();
  const { can } = useAuth();

  const [order, setOrder] = useState<Order | null>(null);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [stockError, setStockError] =
    useState<InsufficientStockError | null>(null);
  

  const loadOrder = useCallback(async () => {
    if (!orderId) return;

    setLoading(true);
    setError("");

    try {
      const [orderData, customerData, productData] = await Promise.all([
        getOrder(orderId),
        getCustomers(),
        getProducts(),
      ]);

      setOrder(orderData);
      setCustomers(customerData);
      setProducts(productData);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Unable to load order.",
      );
    } finally {
      setLoading(false);
    }
  }, [orderId]);

  useEffect(() => {
    void loadOrder();
  }, [loadOrder]);

  async function executeAction(
    action: "confirm" | "ship" | "deliver" | "cancel",
    ) {
    if (!orderId) return;

    const messages = {
        confirm: "Confirm this order? Inventory will be deducted.",
        ship: "Mark this order as shipped?",
        deliver: "Mark this order as delivered?",
        cancel: "Cancel this order? A confirmed order will have its inventory restored.",
    };

    if (!window.confirm(messages[action])) return;

    setActionLoading(true);
    setError("");
    setStockError(null);

    try {
        const updatedOrder = await performOrderAction(orderId, action);
        setOrder(updatedOrder);
    } catch (err) {
        if (err instanceof ApiError) {
        const detail = err.detail;

        if (
            typeof detail === "object" &&
            detail !== null &&
            "code" in detail &&
            detail.code === "insufficient_stock"
        ) {
            const stockDetail = detail as InsufficientStockError;

            setStockError(stockDetail);
        } else {
            setError(err.message);
        }
        } else {
        setError(`Unable to ${action} order.`);
        }
    } finally {
        setActionLoading(false);
    }
  }

  async function handleRemoveItem(productId: string, productName: string) {
    if (!orderId || !order) return;

    if (order.status !== "draft") return;

    if (order.items.length === 1) {
        setError("You cannot remove the last item. Cancel the order instead.");
        return;
    }

    if (
        !window.confirm(
        `Remove "${productName}" from this draft order?`,
        )
    ) {
        return;
    }

    setActionLoading(true);
    setError("");
    setStockError(null);

    try {
        const updatedOrder = await removeOrderItem(orderId, productId);
        setOrder(updatedOrder);
    } catch (err) {
        setError(
        err instanceof ApiError
            ? err.message
            : "Unable to remove the product from this order.",
        );
    } finally {
        setActionLoading(false);
    }
  }

  if (loading) {
    return <div className="panel empty-state">Loading order...</div>;
  }

  if (error && !order) {
    return (
      <div>
        <div className="alert alert-error">{error}</div>
        <Link to="/orders" className="back-link">
          <ArrowLeft size={16} /> Back to orders
        </Link>
      </div>
    );
  }

  if (!order) return null;

  const customer = customers.find(
    (item) => item.id === order.customer_id,
  );

  const productMap = new Map(
    products.map((product) => [product.id, product]),
  );

  const canUpdate = can("orders:update");
  const isDraft = order.status === "draft";
  const isConfirmed = order.status === "confirmed";
  const isShipped = order.status === "shipped";

  return (
    <div>
      <div className="page-heading">
        <div>
          <Link to="/orders" className="back-link">
            <ArrowLeft size={16} /> Back to orders
          </Link>
          <div className="eyebrow">ORDER MANAGEMENT</div>
          <h1>Order #{order.id.slice(0, 8).toUpperCase()}</h1>
          <p className="muted">Created {dateTime(order.created_at)}</p>
        </div>

        <div className="heading-actions">
          <StatusBadge status={order.status} />
        </div>
      </div>

    {error && <div className="alert alert-error">{error}</div>}

    {stockError && (
    <section className="stock-error-panel" role="alert">
        <div className="stock-error-heading">
        <strong>Insufficient inventory</strong>
        <span>Order remains in Draft. No inventory was deducted.</span>
        </div>

        <div className="stock-error-product">
        <strong>{stockError.product_name}</strong>
        <span>SKU: {stockError.sku}</span>
        </div>

        <div className="stock-error-quantities">
        <div>
            <span>Available stock</span>
            <strong>{stockError.available}</strong>
        </div>

        <div>
            <span>Requested quantity</span>
            <strong>{stockError.requested}</strong>
        </div>

        <div>
            <span>Shortage</span>
            <strong>
            {Math.max(
                0,
                stockError.requested - stockError.available,
            )}
            </strong>
        </div>
        </div>
    </section>
    )}

      <section className="fulfillment-panel panel">
        <div>
          <h2>Fulfillment workflow</h2>
          <p className="muted">
            Actions available for the current order status.
          </p>
        </div>

        {canUpdate && (
          <div className="fulfillment-actions">
            {isDraft && (
              <button
                className="primary-button"
                onClick={() => void executeAction("confirm")}
                disabled={actionLoading}
              >
                <Check size={16} /> Confirm order
              </button>
            )}

            {isConfirmed && (
              <button
                className="primary-button"
                onClick={() => void executeAction("ship")}
                disabled={actionLoading}
              >
                <Truck size={16} /> Mark shipped
              </button>
            )}

            {isShipped && (
              <button
                className="primary-button"
                onClick={() => void executeAction("deliver")}
                disabled={actionLoading}
              >
                <Package size={16} /> Mark delivered
              </button>
            )}

            {(isDraft || isConfirmed) && (
              <button
                className="secondary-button danger-button"
                onClick={() => void executeAction("cancel")}
                disabled={actionLoading}
              >
                <XCircle size={16} /> Cancel order
              </button>
            )}

            {actionLoading && (
              <span className="muted">Processing...</span>
            )}
          </div>
        )}

        {!canUpdate && (
          <p className="muted">
            You have read-only access to this order.
          </p>
        )}
      </section>

      <div className="details-grid">
        <section className="panel details-main">
          <div className="section-heading">
            <div>
              <h2>Order items</h2>
              <p className="muted">
                {order.items.length} product(s)
              </p>
            </div>
          </div>

          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>PRODUCT</th>
                  <th>SKU</th>
                  <th>QUANTITY</th>
                  <th>UNIT PRICE</th>
                  <th className="align-right">TOTAL</th>
                  {isDraft && canUpdate && <th>ACTION</th>}
                </tr>
              </thead>
              <tbody>
                {order.items.map((item) => {
                  const product = productMap.get(item.product_id);

                  return (
                    <tr key={item.id}>
                        <td>
                            <strong>
                            {product?.name ?? `Product ${item.product_id.slice(0, 8)}`}
                            </strong>
                        </td>

                        <td>{product?.sku ?? "—"}</td>
                        <td>{item.quantity}</td>
                        <td>{money(item.unit_price)}</td>

                        <td className="align-right amount">
                            {money(item.line_total)}
                        </td>

                        {isDraft && canUpdate && (
                            <td>
                            <button
                                type="button"
                                className="icon-button danger-icon"
                                onClick={() =>
                                void handleRemoveItem(
                                    item.product_id,
                                    product?.name ?? "this product",
                                )
                                }
                                disabled={actionLoading || order.items.length === 1}
                                title={
                                order.items.length === 1
                                    ? "An order must contain at least one item"
                                    : "Remove product"
                                }
                                aria-label={`Remove ${product?.name ?? "product"} from order`}
                            >
                                <Trash2 size={16} />
                            </button>
                            </td>
                        )}
                        </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="order-total-row">
            <span>Order total</span>
            <strong>{money(order.total)}</strong>
          </div>
        </section>

        <aside className="details-sidebar">
          <section className="panel detail-card">
            <h2>Customer</h2>

            <div className="customer-summary">
              <div className="avatar customer-avatar">
                {customer?.name.charAt(0).toUpperCase() ?? "?"}
              </div>
              <div>
                <strong>{customer?.name ?? "Customer unavailable"}</strong>
                <span>{customer?.email ?? "No email available"}</span>
              </div>
            </div>

            {customer?.phone && (
              <div className="detail-line">
                <span>Phone</span>
                <strong>{customer.phone}</strong>
              </div>
            )}

            <div className="detail-line">
              <span>Customer ID</span>
              <small>{order.customer_id.slice(0, 13)}...</small>
            </div>
          </section>

          <section className="panel detail-card">
            <h2>Order information</h2>

            <div className="detail-line">
              <span>Status</span>
              <StatusBadge status={order.status} />
            </div>

            <div className="detail-line">
              <span>Created</span>
              <strong>{dateTime(order.created_at)}</strong>
            </div>

            <div className="detail-line">
              <span>Last updated</span>
              <strong>{dateTime(order.updated_at)}</strong>
            </div>

            <div className="detail-line">
              <span>Order ID</span>
              <small className="break-id">{order.id}</small>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
