import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CalendarDays,
  Edit2,
  Mail,
  Phone,
  Plus,
  RefreshCw,
  UserRound,
  Users,
  X,
} from "lucide-react";

import {
  createCustomerContact,
  deactivateCustomer,
  getCustomer,
  getCustomerContacts,
  updateCustomer,
  updateCustomerContact,
  type CustomerContact,
  type CustomerContactPayload,
  type CustomerPayload,
} from "../api/resources";
import { getOrders } from "../api/orders";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import StatusBadge from "../components/StatusBadge";
import type { Customer, Order } from "../types";

type ModalMode = "customer" | "contact" | null;

const emptyCustomerForm: CustomerPayload = {
  name: "",
  email: null,
  phone: null,
  status: "active",
  notes: null,
};

const emptyContactForm: CustomerContactPayload = {
  first_name: "",
  last_name: "",
  email: null,
  phone: null,
  is_primary: false,
};

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatMoney(value: string) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return value;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(amount);
}

function getErrorMessage(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

export default function CustomerDetailsPage() {
  const { customerId } = useParams<{ customerId: string }>();
  const navigate = useNavigate();
  const { can } = useAuth();

  const [customer, setCustomer] = useState<Customer | null>(null);
  const [contacts, setContacts] = useState<CustomerContact[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [modal, setModal] = useState<ModalMode>(null);
  const [editingContact, setEditingContact] = useState<CustomerContact | null>(null);
  const [customerForm, setCustomerForm] = useState<CustomerPayload>(emptyCustomerForm);
  const [contactForm, setContactForm] = useState<CustomerContactPayload>(emptyContactForm);

  const loadCustomer = useCallback(async () => {
    if (!customerId) {
      setError("Customer ID is missing.");
      setLoading(false);
      return;
    }

    setLoading(true);
    setError("");

    try {
      const [customerResult, contactResult, orderResult] = await Promise.all([
        getCustomer(customerId),
        getCustomerContacts(customerId),
        getOrders(undefined, 0, 50, customerId),
      ]);

      setCustomer(customerResult);
      setContacts(contactResult);
      setOrders(orderResult.items);
    } catch (err) {
      setError(getErrorMessage(err, "Unable to load customer details."));
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    void loadCustomer();
  }, [loadCustomer]);

  function openCustomerEditor() {
    if (!customer) return;
    setCustomerForm({
      name: customer.name,
      email: customer.email,
      phone: customer.phone,
      status: customer.status,
      notes: customer.notes,
    });
    setActionError("");
    setModal("customer");
  }

  function openContactEditor(contact?: CustomerContact) {
    setEditingContact(contact ?? null);
    setContactForm(
      contact
        ? {
            first_name: contact.first_name,
            last_name: contact.last_name,
            email: contact.email,
            phone: contact.phone,
            is_primary: contact.is_primary,
          }
        : emptyContactForm,
    );
    setActionError("");
    setModal("contact");
  }

  function closeModal() {
    if (saving) return;
    setModal(null);
    setEditingContact(null);
    setActionError("");
  }

  async function handleCustomerSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!customerId) return;

    setSaving(true);
    setActionError("");

    try {
      await updateCustomer(customerId, {
        name: customerForm.name.trim(),
        email: customerForm.email?.trim() || null,
        phone: customerForm.phone?.trim() || null,
        status: customerForm.status.trim(),
        notes: customerForm.notes?.trim() || null,
      });
      setModal(null);
      await loadCustomer();
    } catch (err) {
      setActionError(getErrorMessage(err, "Unable to update customer."));
    } finally {
      setSaving(false);
    }
  }

  async function handleContactSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!customerId) return;

    setSaving(true);
    setActionError("");

    const payload: CustomerContactPayload = {
      first_name: contactForm.first_name.trim(),
      last_name: contactForm.last_name.trim(),
      email: contactForm.email?.trim() || null,
      phone: contactForm.phone?.trim() || null,
      is_primary: contactForm.is_primary,
    };

    try {
      if (editingContact) {
        await updateCustomerContact(customerId, editingContact.id, payload);
      } else {
        await createCustomerContact(customerId, payload);
      }
      setModal(null);
      setEditingContact(null);
      await loadCustomer();
    } catch (err) {
      setActionError(getErrorMessage(err, "Unable to save contact."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDeactivate() {
    if (!customerId || !customer) return;
    const confirmed = window.confirm(
      `Deactivate ${customer.name}? The customer will no longer be available for new orders.`,
    );
    if (!confirmed) return;

    setActionError("");
    setSaving(true);
    try {
      await deactivateCustomer(customerId);
      await loadCustomer();
    } catch (err) {
      setActionError(getErrorMessage(err, "Unable to deactivate customer."));
    } finally {
      setSaving(false);
    }
  }

  if (loading && !customer) {
    return <div className="panel empty-state">Loading customer...</div>;
  }

  if (error || !customer) {
    return (
      <section className="panel customer-detail-panel">
        <div className="alert alert-error">{error || "Customer not found."}</div>
        <Link to="/customers" className="secondary-button">
          <ArrowLeft size={16} />
          Back to customers
        </Link>
      </section>
    );
  }

  return (
    <div className="customer-details-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">CRM / CUSTOMERS / PROFILE</div>
          <h1>{customer.name}</h1>
          <p className="muted">Customer account, contacts, and order history.</p>
        </div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void loadCustomer()} disabled={loading}>
            <RefreshCw size={16} />
            Refresh
          </button>
          <button className="secondary-button" onClick={() => navigate("/customers")}>
            <ArrowLeft size={16} />
            Customers
          </button>
        </div>
      </div>

      {actionError && !modal && <div className="alert alert-error customer-page-alert">{actionError}</div>}

      <section className="customer-profile-header panel">
        <div className="customer-profile-identity">
          <div className="customer-profile-avatar">{customer.name.charAt(0).toUpperCase()}</div>
          <div>
            <h2>{customer.name}</h2>
            <div className="customer-profile-meta">
              <span className={`status-badge ${customer.is_active ? "customer-active" : "customer-deactivated"}`}>
                {customer.is_active ? "Active" : "Deactivated"}
              </span>
              <span>Customer #{customer.id.slice(0, 8).toUpperCase()}</span>
            </div>
          </div>
        </div>
        <div className="customer-profile-actions">
          {can("customers:update") && (
            <>
              <button className="secondary-button" onClick={openCustomerEditor}>
                <Edit2 size={15} />
                Edit customer
              </button>
              {customer.is_active && (
                <button className="secondary-button danger-button" onClick={() => void handleDeactivate()} disabled={saving}>
                  Deactivate
                </button>
              )}
            </>
          )}
        </div>
      </section>

      <div className="customer-detail-grid">
        <section className="panel customer-detail-panel">
          <div className="section-heading">
            <div>
              <div className="eyebrow">CUSTOMER INFORMATION</div>
              <h2>Account details</h2>
            </div>
            <UserRound size={20} />
          </div>
          <div className="customer-detail-list">
            <div>
              <Mail size={17} />
              <div><span>Email</span><strong>{customer.email || "Not provided"}</strong></div>
            </div>
            <div>
              <Phone size={17} />
              <div><span>Phone</span><strong>{customer.phone || "Not provided"}</strong></div>
            </div>
            <div>
              <CalendarDays size={17} />
              <div><span>Created</span><strong>{formatDate(customer.created_at)}</strong></div>
            </div>
            <div>
              <CalendarDays size={17} />
              <div><span>Last updated</span><strong>{formatDate(customer.updated_at)}</strong></div>
            </div>
            <div className="customer-detail-text-row">
              <div><span>Business status</span><strong>{customer.status}</strong></div>
            </div>
          </div>
          <div className="customer-notes">
            <span>Notes</span>
            <p>{customer.notes || "No notes available."}</p>
          </div>
        </section>

        <section className="panel customer-detail-panel">
          <div className="section-heading">
            <div>
              <div className="eyebrow">CUSTOMER RELATIONSHIPS</div>
              <h2>Contacts</h2>
            </div>
            <div className="customer-section-actions">
              <span className="contact-count">{contacts.length}</span>
              {customer.is_active && can("customers:create") && (
                <button className="primary-button compact-button" onClick={() => openContactEditor()}>
                  <Plus size={15} /> Add contact
                </button>
              )}
            </div>
          </div>

          {contacts.length === 0 ? (
            <div className="empty-state customer-inline-empty">
              <Users size={22} />
              <span>No contacts have been added.</span>
              {customer.is_active && can("customers:create") && (
                <button className="text-link" onClick={() => openContactEditor()}>Add the first contact</button>
              )}
            </div>
          ) : (
            <div className="customer-contact-list">
              {contacts.map((contact) => (
                <div className="customer-contact-item" key={contact.id}>
                  <div className="avatar customer-contact-avatar">{contact.first_name.charAt(0).toUpperCase()}</div>
                  <div className="customer-contact-info">
                    <div className="customer-contact-name">
                      <strong>{contact.first_name} {contact.last_name}</strong>
                      {contact.is_primary && <span className="primary-contact-label">Primary</span>}
                    </div>
                    <small>{contact.email || "No email"}</small>
                    <small>{contact.phone || "No phone"}</small>
                  </div>
                  {customer.is_active && can("customers:update") && (
                    <button className="icon-button" aria-label={`Edit ${contact.first_name} ${contact.last_name}`} title="Edit contact" onClick={() => openContactEditor(contact)}>
                      <Edit2 size={15} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="panel customer-orders-panel">
        <div className="section-heading customer-orders-heading">
          <div>
            <div className="eyebrow">SALES ACTIVITY</div>
            <h2>Order history</h2>
            <p className="muted">Orders associated with this customer.</p>
          </div>
          <span className="contact-count">{orders.length}</span>
        </div>

        {orders.length === 0 ? (
          <div className="empty-state customer-inline-empty">No orders found for this customer.</div>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>ORDER</th>
                  <th>DATE</th>
                  <th>STATUS</th>
                  <th className="align-right">TOTAL</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.id}>
                    <td><strong>#{order.id.slice(0, 8).toUpperCase()}</strong></td>
                    <td>{formatDate(order.created_at)}</td>
                    <td><StatusBadge status={order.status} /></td>
                    <td className="align-right amount">{formatMoney(order.total)}</td>
                    <td className="align-right"><Link className="text-link" to={`/orders/${order.id}`}>View order</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {modal && (
        <div className="modal-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeModal();
        }}>
          <section className="customer-modal" role="dialog" aria-modal="true" aria-labelledby="customer-modal-title">
            <div className="customer-modal-heading">
              <div>
                <div className="eyebrow">{modal === "customer" ? "CUSTOMER ACCOUNT" : "CUSTOMER RELATIONSHIP"}</div>
                <h2 id="customer-modal-title">
                  {modal === "customer" ? "Edit customer" : editingContact ? "Edit contact" : "Add contact"}
                </h2>
              </div>
              <button className="icon-button" onClick={closeModal} aria-label="Close dialog" disabled={saving}>
                <X size={18} />
              </button>
            </div>

            {actionError && <div className="alert alert-error">{actionError}</div>}

            {modal === "customer" ? (
              <form onSubmit={(event) => void handleCustomerSubmit(event)}>
                <label>Customer name <span className="required">*</span>
                  <input required maxLength={200} value={customerForm.name} onChange={(event) => setCustomerForm({ ...customerForm, name: event.target.value })} />
                </label>
                <div className="customer-form-grid">
                  <label>Email
                    <input type="email" value={customerForm.email ?? ""} onChange={(event) => setCustomerForm({ ...customerForm, email: event.target.value || null })} />
                  </label>
                  <label>Phone
                    <input type="tel" value={customerForm.phone ?? ""} onChange={(event) => setCustomerForm({ ...customerForm, phone: event.target.value || null })} />
                  </label>
                </div>
                <label>Business status
                  <input maxLength={50} value={customerForm.status} onChange={(event) => setCustomerForm({ ...customerForm, status: event.target.value })} />
                </label>
                <label>Notes
                  <textarea rows={4} value={customerForm.notes ?? ""} onChange={(event) => setCustomerForm({ ...customerForm, notes: event.target.value || null })} />
                </label>
                <div className="customer-modal-actions">
                  <button type="button" className="secondary-button" onClick={closeModal} disabled={saving}>Cancel</button>
                  <button type="submit" className="primary-button" disabled={saving}>{saving ? "Saving..." : "Save changes"}</button>
                </div>
              </form>
            ) : (
              <form onSubmit={(event) => void handleContactSubmit(event)}>
                <div className="customer-form-grid">
                  <label>First name <span className="required">*</span>
                    <input required maxLength={100} value={contactForm.first_name} onChange={(event) => setContactForm({ ...contactForm, first_name: event.target.value })} />
                  </label>
                  <label>Last name <span className="required">*</span>
                    <input required maxLength={100} value={contactForm.last_name} onChange={(event) => setContactForm({ ...contactForm, last_name: event.target.value })} />
                  </label>
                </div>
                <label>Email
                  <input type="email" value={contactForm.email ?? ""} onChange={(event) => setContactForm({ ...contactForm, email: event.target.value || null })} />
                </label>
                <label>Phone
                  <input type="tel" value={contactForm.phone ?? ""} onChange={(event) => setContactForm({ ...contactForm, phone: event.target.value || null })} />
                </label>
                <label className="customer-checkbox-label">
                  <input type="checkbox" checked={contactForm.is_primary} onChange={(event) => setContactForm({ ...contactForm, is_primary: event.target.checked })} />
                  Set as primary contact
                </label>
                <div className="customer-modal-actions">
                  <button type="button" className="secondary-button" onClick={closeModal} disabled={saving}>Cancel</button>
                  <button type="submit" className="primary-button" disabled={saving}>{saving ? "Saving..." : editingContact ? "Save contact" : "Add contact"}</button>
                </div>
              </form>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
