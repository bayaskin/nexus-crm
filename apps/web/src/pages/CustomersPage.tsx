
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ChevronLeft,
  ChevronRight,
  Plus,
  RefreshCw,
  Search,
  UserRound,
  X,
} from "lucide-react";

import {
  createCustomer,
  deactivateCustomer,
  listCustomers,
  updateCustomer,
  type CustomerPayload,
} from "../api/resources";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import type { Customer } from "../types";
import StatusBadge from "../components/StatusBadge";

const PAGE_SIZE = 20;

const emptyForm: CustomerPayload = {
  name: "",
  email: "",
  phone: "",
  status: "active",
  notes: "",
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  }).format(new Date(value));
}

export default function CustomersPage() {
  const { can } = useAuth();

  const [customers, setCustomers] = useState<Customer[]>([]);
  const [total, setTotal] = useState(0);
  const [skip, setSkip] = useState(0);
  const [search, setSearch] = useState("");
  const [accountStatus, setAccountStatus] =
    useState<"active" | "inactive" | "all">("active");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [modalOpen, setModalOpen] = useState(false);
  const [editingCustomer, setEditingCustomer] =
    useState<Customer | null>(null);
  const [form, setForm] = useState<CustomerPayload>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");

  const loadCustomers = useCallback(async () => {
    setLoading(true);
    setError("");

    try {
      const result = await listCustomers({
        search,
        account_status: accountStatus,
        skip,
        limit: PAGE_SIZE,
      });

      setCustomers(result.items);
      setTotal(result.total);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to load customers.",
      );
    } finally {
      setLoading(false);
    }
  }, [search, accountStatus, skip]);

  useEffect(() => {
    void loadCustomers();
  }, [loadCustomers]);

  function openCreate() {
    setEditingCustomer(null);
    setForm(emptyForm);
    setFormError("");
    setModalOpen(true);
  }

  function openEdit(customer: Customer) {
    setEditingCustomer(customer);
    setForm({
      name: customer.name,
      email: customer.email ?? "",
      phone: customer.phone ?? "",
      status: customer.status,
      notes: customer.notes ?? "",
    });
    setFormError("");
    setModalOpen(true);
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setFormError("");

    const payload: CustomerPayload = {
      name: form.name.trim(),
      email: form.email?.trim() || null,
      phone: form.phone?.trim() || null,
      status: form.status.trim(),
      notes: form.notes?.trim() || null,
    };

    try {
      if (editingCustomer) {
        await updateCustomer(editingCustomer.id, payload);
      } else {
        await createCustomer(payload);
      }

      setModalOpen(false);
      await loadCustomers();
    } catch (err) {
      setFormError(
        err instanceof ApiError
          ? err.message
          : "Unable to save customer.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleDeactivate(customer: Customer) {
    const confirmed = window.confirm(
      `Deactivate ${customer.name}? They will no longer be available for new orders.`,
    );

    if (!confirmed) return;

    try {
      await deactivateCustomer(customer.id);

      if (customers.length === 1 && skip > 0) {
        setSkip(Math.max(0, skip - PAGE_SIZE));
      } else {
        await loadCustomers();
      }
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unable to deactivate customer.",
      );
    }
  }

  const page = Math.floor(skip / PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="eyebrow">CRM / RELATIONSHIPS</div>
          <h1>Customers</h1>
          <p className="muted">
            Manage customer accounts and contact information.
          </p>
        </div>

        <div className="heading-actions">
          <button
            className="secondary-button"
            onClick={() => void loadCustomers()}
            disabled={loading}
          >
            <RefreshCw size={16} />
            Refresh
          </button>

          {can("customers:create") && (
            <button className="primary-button" onClick={openCreate}>
              <Plus size={17} />
              Add customer
            </button>
          )}
        </div>
      </div>

      <section className="metric-grid">
        <article className="metric-card">
          <span>Total customers</span>
          <strong>{total}</strong>
          <small>Matching current filters</small>
        </article>

        <article className="metric-card">
          <span>Current page</span>
          <strong>{customers.length}</strong>
          <small>Customers displayed</small>
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
              placeholder="Search name, email or phone..."
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setSkip(0);
              }}
            />
          </div>

            <select
              value={accountStatus}
              onChange={(event) => {
                setAccountStatus(
                event.target.value as "active" | "inactive" | "all",
                );
                setSkip(0);
              }}
              aria-label="Filter customers by account status"
            >
              <option value="active">Active customers</option>
              <option value="inactive">Deactivated customers</option>
              <option value="all">All customers</option>
            </select>
        </div>

        {error && <div className="alert alert-error">{error}</div>}

        <div className="table-wrapper">
          <table className="data-table">
            <thead>
              <tr>
                <th>CUSTOMER</th>
                <th>EMAIL</th>
                <th>PHONE</th>
                <th>STATUS</th>
                <th>CREATED</th>
                <th />
              </tr>
            </thead>

            <tbody>
              {!loading && customers.map((customer) => (
                <tr key={customer.id}>
                  <td>
                    <div className="customer-table-name">
                      <div className="avatar customer-avatar">
                        {customer.name.charAt(0).toUpperCase()}
                      </div>
                      <div>
                        <strong>{customer.name}</strong>
                        <small>
                          {customer.id.slice(0, 8).toUpperCase()}
                        </small>
                      </div>
                    </div>
                  </td>

                  <td>{customer.email || "—"}</td>
                  <td>{customer.phone || "—"}</td>
                  <td>
                    <span className="customer-business-status">
                      {customer.status}
                    </span>
                  </td>
                  <td>{formatDate(customer.created_at)}</td>

                  <td>
                    <div className="customer-row-actions">
                      <Link
                        to={`/customers/${customer.id}`}
                        className="text-link"
                      >
                        View
                      </Link>

                      {can("customers:update") && (
                        <>
                          <button
                            className="text-link"
                            onClick={() => openEdit(customer)}
                          >
                            Edit
                          </button>

                          <button
                            className="text-link danger-text"
                            onClick={() => void handleDeactivate(customer)}
                          >
                            Deactivate
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}

              {!loading && customers.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-state">
                    <UserRound size={22} />
                    <strong>No customers found.</strong>
                    <span>Try changing your search or filters.</span>
                  </td>
                </tr>
              )}

              {loading && (
                <tr>
                  <td colSpan={6} className="empty-state">
                    Loading customers...
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

      {modalOpen && (
        <div
          className="modal-backdrop"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              setModalOpen(false);
            }
          }}
        >
          <section
            className="customer-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="customer-modal-title"
          >
            <div className="customer-modal-heading">
              <div>
                <div className="eyebrow">CUSTOMER MANAGEMENT</div>
                <h2 id="customer-modal-title">
                  {editingCustomer ? "Edit customer" : "New customer"}
                </h2>
              </div>

              <button
                className="icon-button"
                onClick={() => setModalOpen(false)}
                aria-label="Close dialog"
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit}>
              <label>
                Customer name *
                <input
                  required
                  maxLength={180}
                  value={form.name}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>

              <label>
                Email
                <input
                  type="email"
                  maxLength={254}
                  value={form.email ?? ""}
                  onChange={(event) =>
                    setForm({ ...form, email: event.target.value })
                  }
                />
              </label>

              <label>
                Phone
                <input
                  type="tel"
                  maxLength={40}
                  value={form.phone ?? ""}
                  onChange={(event) =>
                    setForm({ ...form, phone: event.target.value })
                  }
                />
              </label>

              <label>
                Status
                <input
                  required
                  maxLength={30}
                  value={form.status}
                  onChange={(event) =>
                    setForm({ ...form, status: event.target.value })
                  }
                />
              </label>

              <label>
                Notes
                <textarea
                  rows={4}
                  value={form.notes ?? ""}
                  onChange={(event) =>
                    setForm({ ...form, notes: event.target.value })
                  }
                />
              </label>

              {formError && (
                <div className="alert alert-error">{formError}</div>
              )}

              <div className="customer-modal-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setModalOpen(false)}
                  disabled={saving}
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="primary-button"
                  disabled={saving || !form.name.trim() || !form.status.trim()}
                >
                  {saving
                    ? "Saving..."
                    : editingCustomer
                      ? "Save changes"
                      : "Create customer"}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
