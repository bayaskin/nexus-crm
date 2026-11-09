
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";

import { createOrder } from "../api/orders";
import { getCustomers, getProducts } from "../api/resources";
import { ApiError } from "../api/client";
import type { Customer, Product } from "../types";

interface DraftItem {
  product_id: string;
  quantity: number;
}

function money(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(value);
}

export default function CreateOrderPage() {
  const navigate = useNavigate();

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [items, setItems] = useState<DraftItem[]>([
    { product_id: "", quantity: 1 },
  ]);

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    async function load() {
      try {
        const [customerData, productData] = await Promise.all([
          getCustomers(),
          getProducts(),
        ]);

        setCustomers(customerData);
        setProducts(productData);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Unable to load data.",
        );
      } finally {
        setLoading(false);
      }
    }

    void load();
  }, []);

  const selectedProducts = useMemo(() => {
    return new Map(products.map((product) => [product.id, product]));
  }, [products]);

  const total = items.reduce((sum, item) => {
    const product = selectedProducts.get(item.product_id);
    return sum + (product ? Number(product.price) * item.quantity : 0);
  }, 0);

  function updateItem(index: number, changes: Partial<DraftItem>) {
    setItems((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...changes } : item,
      ),
    );
  }

  function addItem() {
    setItems((current) => [
      ...current,
      { product_id: "", quantity: 1 },
    ]);
  }

  function removeItem(index: number) {
    setItems((current) => current.filter((_, i) => i !== index));
  }

  const duplicateProduct = new Set(
    items
      .map((item) => item.product_id)
      .filter((id) => id !== ""),
  ).size !== items.filter((item) => item.product_id !== "").length;

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");

    if (!customerId) {
      setError("Select a customer.");
      return;
    }

    if (items.length === 0 || items.some((item) => !item.product_id)) {
      setError("Add at least one product to the order.");
      return;
    }

    if (items.some((item) => !Number.isInteger(item.quantity) || item.quantity < 1)) {
      setError("Every product quantity must be a positive whole number.");
      return;
    }

    if (duplicateProduct) {
      setError("Each product can appear only once. Update its quantity instead.");
      return;
    }

    setSubmitting(true);

    try {
      const order = await createOrder({
        customer_id: customerId,
        items,
      });

      navigate(`/orders/${order.id}`);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to create order.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return <div className="panel empty-state">Loading customers and products...</div>;
  }

  return (
    <div>
      <div className="page-heading">
        <div>
          <Link to="/orders" className="back-link">
            <ArrowLeft size={16} /> Back to orders
          </Link>
          <div className="eyebrow">SALES MANAGEMENT</div>
          <h1>Create order</h1>
          <p className="muted">
            Create a draft order for an active customer.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="order-form">
        <section className="panel form-panel">
          <div className="section-heading">
            <div>
              <h2>Customer</h2>
              <p className="muted">Choose the customer placing this order.</p>
            </div>
            <span className="step-number">01</span>
          </div>

          <label className="field-label">
            Customer <span className="required">*</span>
            <select
              value={customerId}
              onChange={(event) => setCustomerId(event.target.value)}
              required
            >
              <option value="">Select a customer</option>
              {customers.map((customer) => (
                <option key={customer.id} value={customer.id}>
                  {customer.name}{customer.email ? ` — ${customer.email}` : ""}
                </option>
              ))}
            </select>
          </label>

          {customers.length === 0 && (
            <p className="muted">
              No active customers found. Create a customer before placing an order.
            </p>
          )}
        </section>

        <section className="panel form-panel">
          <div className="section-heading">
            <div>
              <h2>Order items</h2>
              <p className="muted">Select products and quantities.</p>
            </div>
            <span className="step-number">02</span>
          </div>

          <div className="order-item-list">
            {items.map((item, index) => {
              const product = selectedProducts.get(item.product_id);

              return (
                <div className="order-item-row" key={index}>
                  <label className="product-select">
                    Product
                    <select
                      value={item.product_id}
                      onChange={(event) =>
                        updateItem(index, {
                          product_id: event.target.value,
                        })
                      }
                      required
                    >
                      <option value="">Select product</option>
                      {products.map((option) => (
                        <option
                          key={option.id}
                          value={option.id}
                          disabled={
                            items.some(
                              (other, otherIndex) =>
                                otherIndex !== index &&
                                other.product_id === option.id,
                            )
                          }
                        >
                          {option.sku} — {option.name}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="quantity-field">
                    Quantity
                    <input
                      type="number"
                      min="1"
                      max="1000000"
                      step="1"
                      value={item.quantity}
                      onChange={(event) =>
                        updateItem(index, {
                          quantity: Number(event.target.value),
                        })
                      }
                      required
                    />
                  </label>

                  <div className="item-price">
                    <span>Unit price</span>
                    <strong>{product ? money(Number(product.price)) : "—"}</strong>
                  </div>

                  <div className="item-price">
                    <span>Line total</span>
                    <strong>
                      {product
                        ? money(Number(product.price) * item.quantity)
                        : "—"}
                    </strong>
                  </div>

                  <button
                    type="button"
                    className="icon-button danger-icon"
                    onClick={() => removeItem(index)}
                    disabled={items.length === 1}
                    aria-label="Remove item"
                    title="Remove item"
                  >
                    <Trash2 size={17} />
                  </button>
                </div>
              );
            })}
          </div>

          <button
            type="button"
            className="secondary-button add-item-button"
            onClick={addItem}
            disabled={items.length >= 100}
          >
            <Plus size={16} /> Add product
          </button>
        </section>

        <section className="panel total-panel">
          <div>
            <span className="muted">ORDER TOTAL</span>
            <strong>{money(total)}</strong>
            <small>Prices are captured when the draft order is created.</small>
          </div>

          <div className="form-actions">
            <Link to="/orders" className="secondary-button">
              Cancel
            </Link>
            <button
              type="submit"
              className="primary-button"
              disabled={
                submitting ||
                !customerId ||
                items.length === 0 ||
                products.length === 0
              }
            >
              {submitting ? "Creating..." : "Create draft order"}
            </button>
          </div>
        </section>

        {error && <div className="alert alert-error">{error}</div>}
      </form>
    </div>
  );
}
