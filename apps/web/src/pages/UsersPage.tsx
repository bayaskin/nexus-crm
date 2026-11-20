import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { describePermission, groupPermissions } from "../permissions/metadata";

type Person = { id: string; first_name: string; last_name: string; full_name: string; email: string; roles: string[]; is_active: boolean; last_login_at: string | null; created_at: string; updated_at: string; password_expires_at: string | null; permission_overrides: Record<string, string>; effective_permissions: string[]; must_change_password: boolean };
type Metadata = { roles: { name: string; permissions: string[] }[]; permissions: string[] };
const PAGE_SIZE = 25;

function uniqueSorted(values: string[]) {
  return [...new Set(values)].sort();
}

function formatDate(value: string | null, fallback = "Not set") {
  return value ? new Date(value).toLocaleString() : fallback;
}

export default function UsersPage() {
  const { can } = useAuth();
  const [users, setUsers] = useState<Person[]>([]);
  const [totalUsers, setTotalUsers] = useState(0);
  const [metadata, setMetadata] = useState<Metadata>({ roles: [], permissions: [] });
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [role, setRole] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Person | null>(null);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ first_name: "", last_name: "", email: "", role_names: [] as string[], send_credentials: true, permission_overrides: {} as Record<string, string> });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const permissionsByGroup = groupPermissions(metadata.permissions);
  const rolePermissions = uniqueSorted(metadata.roles.filter(item => form.role_names.includes(item.name)).flatMap(item => item.permissions));
  const totalPages = Math.max(1, Math.ceil(totalUsers / PAGE_SIZE));
  const rangeStart = totalUsers === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, totalUsers);

  async function reload() {
    setLoading(true);
    setError("");
    try {
      const [usersResult, metaResult] = await Promise.allSettled([
        apiRequest<Person[]>(`/v1/users?search=${encodeURIComponent(search)}&status=${status}&role=${encodeURIComponent(role)}&page=${page}&page_size=${PAGE_SIZE}`, {
          onResponse: response => setTotalUsers(Number(response.headers.get("X-Total-Count") || 0)),
        }),
        apiRequest<Metadata>("/v1/users/metadata"),
      ]);
      if (usersResult.status === "rejected") throw usersResult.reason;
      setUsers(usersResult.value);
      if (metaResult.status === "fulfilled") setMetadata(metaResult.value);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to load users.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { setPage(1); }, [search, status, role]);
  useEffect(() => { void reload(); }, [search, status, role, page]);

  useEffect(() => {
    if (!selected) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (editing) setEditing(false);
      else setSelected(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [selected, editing]);

  function startEdit(user: Person) {
    setEditing(true);
    setSelected(user);
    setForm({ first_name: user.first_name, last_name: user.last_name, email: user.email, role_names: user.roles, send_credentials: true, permission_overrides: user.permission_overrides });
    setNotice("");
  }

  function startCreate() {
    setSelected(null);
    setEditing(true);
    setForm({ first_name: "", last_name: "", email: "", role_names: [], send_credentials: true, permission_overrides: {} });
    setNotice("");
  }

  function closeDetails() {
    setSelected(null);
    setEditing(false);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: Record<string, unknown> = { first_name: form.first_name, last_name: form.last_name, email: form.email };
      if (can("roles:assign")) payload.role_names = form.role_names;
      if (can("permissions:manage")) payload.permission_overrides = form.permission_overrides;
      if (!selected) payload.send_credentials = form.send_credentials;
      const result = await apiRequest<Person & { email_sent?: boolean; message?: string; temporary_password?: string }>(selected ? `/v1/users/${selected.id}` : "/v1/users", {
        method: selected ? "PATCH" : "POST", body: JSON.stringify(payload),
      });
      if (selected) {
        setSelected(result);
        setEditing(false);
        setNotice("User updated.");
      } else {
        setNotice(result.email_sent ? "User created and credentials emailed." : `${result.message || "User created; credentials email was not sent."}${result.temporary_password ? ` Temporary password (copy now): ${result.temporary_password}` : ""}`);
        setSelected(null);
        setEditing(false);
      }
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to save user.");
    } finally {
      setSaving(false);
    }
  }

  async function toggle(user: Person) {
    if (user.is_active && !window.confirm(`Deactivate ${user.full_name}? Their current sessions will stop working.`)) return;
    try {
      await apiRequest(`/v1/users/${user.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !user.is_active }) });
      setNotice(`${user.full_name} ${user.is_active ? "deactivated" : "activated"}.`);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to update account status."); }
  }

  async function reset(user: Person) {
    if (!window.confirm(`Generate a temporary password for ${user.full_name}?`)) return;
    try {
      const result = await apiRequest<{ email_sent: boolean; temporary_password?: string }>(`/v1/users/${user.id}/reset-password`, { method: "POST" });
      setNotice(result.email_sent ? `Password reset email sent to ${user.email}.` : `Password reset for ${user.email}, but email delivery failed. Check SMTP/MailHog and retry Reset password; no password was returned.${result.temporary_password ? ` Temporary password (copy now): ${result.temporary_password}` : ""}`);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to reset password."); }
  }

  function renderUserForm() {
    return <form onSubmit={submit}>
      <div className="user-form-grid">
        <label>First name<input required maxLength={80} value={form.first_name} onChange={e => setForm({ ...form, first_name: e.target.value })} /></label>
        <label>Last name<input required maxLength={80} value={form.last_name} onChange={e => setForm({ ...form, last_name: e.target.value })} /></label>
        <label>Email<input required type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
        {can("roles:assign") ? <label>Roles<select multiple value={form.role_names} onChange={e => setForm({ ...form, role_names: Array.from(e.target.selectedOptions, option => option.value) })}>{metadata.roles.map(r => <option key={r.name} value={r.name}>{r.name}</option>)}</select></label> : selected && <div className="muted">Roles: {selected.roles.join(", ") || "None"}</div>}
      </div>
      <fieldset>
        <legend>Customize individual permissions</legend>
        <p className="permission-intro">Permissions follow the user's assigned role by default. You can customize individual permissions below to allow or block specific actions for this user.</p>
        <p className="permission-choice-note"><strong>Default</strong> follows assigned roles. <strong>Allowed</strong> grants the action to this user. <strong>Blocked</strong> prevents it, even when an assigned role allows it.</p>
        <div className="role-permission-summary"><strong>Permissions from selected roles</strong><PermissionChips values={rolePermissions} empty="No role permissions" /></div>
        {can("permissions:manage") ? <div className="permission-groups">{permissionsByGroup.map(group => <section className="permission-group" key={group.key} aria-labelledby={`permission-group-${group.key}`}>
          <h3 id={`permission-group-${group.key}`}>{group.label}</h3>
          {group.permissions.map(permission => <div className="permission-setting" key={permission.key}>
            <div className="permission-copy"><label htmlFor={`permission-setting-${permission.key}`}>{permission.label}</label><p>{permission.description}</p></div>
            <select id={`permission-setting-${permission.key}`} aria-label={`${permission.label} setting`} value={form.permission_overrides[permission.key] || "inherit"} onChange={e => { const next = { ...form.permission_overrides }; if (e.target.value === "inherit") delete next[permission.key]; else next[permission.key] = e.target.value; setForm({ ...form, permission_overrides: next }); }}>
              <option value="inherit">Default</option><option value="allow">Allowed</option><option value="deny">Blocked</option>
            </select>
          </div>)}
        </section>)}</div> : <p className="muted">You do not have permission to customize individual access settings.</p>}
      </fieldset>
      {!selected && <label className="user-check"><input type="checkbox" checked={form.send_credentials} onChange={e => setForm({ ...form, send_credentials: e.target.checked })} /> Email temporary credentials (required password change)</label>}
      <div className="form-actions"><button className="primary-button" disabled={saving}>{saving ? "Saving…" : selected ? "Save changes" : "Create user"}</button><button type="button" className="secondary-button" onClick={() => { if (selected) setEditing(false); else { setEditing(false); setSelected(null); } }}>Cancel</button></div>
    </form>;
  }

  const selectedRolePermissions = selected ? uniqueSorted(metadata.roles.filter(item => selected.roles.includes(item.name)).flatMap(item => item.permissions)) : [];

  return <div>
    <div className="page-heading"><div><div className="eyebrow">SYSTEM / ACCESS CONTROL</div><h1>User management</h1><p className="muted">Create accounts, assign roles, and control individual permissions.</p></div></div>
    {(error || notice) && <div className={error ? "form-error" : "form-success"} role="status">{error || notice}</div>}
    <section className="panel user-management">
      <div className="table-toolbar user-toolbar">
        <input aria-label="Search users" placeholder="Search name or email" value={search} onChange={e => setSearch(e.target.value)} />
        <select aria-label="Filter by role" value={role} onChange={e => setRole(e.target.value)}><option value="">All roles</option>{metadata.roles.map(r => <option key={r.name}>{r.name}</option>)}</select>
        <select aria-label="Filter by status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select>
        {can("users:create") && <button className="primary-button" type="button" onClick={startCreate}>Create user</button>}
      </div>
      {loading ? <div className="empty-state">Loading users…</div> : users.length === 0 ? <div className="empty-state">No users match these filters.</div> : <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Roles</th><th>Status</th><th>Last login</th><th>Created</th><th>Actions</th></tr></thead><tbody>{users.map(u => <tr key={u.id}>
        <td><button className="text-link" aria-haspopup="dialog" onClick={() => { setSelected(u); setEditing(false); }}>{u.full_name}</button></td><td>{u.email}</td><td>{u.roles.join(", ") || "—"}</td><td><span className={`user-status ${u.is_active ? "is-active" : "is-inactive"}`}>{u.is_active ? "Active" : "Inactive"}</span></td><td>{u.last_login_at ? new Date(u.last_login_at).toLocaleDateString() : "Never"}</td><td>{new Date(u.created_at).toLocaleDateString()}</td>
        <td className="user-actions">{can("users:update") && <button type="button" onClick={() => startEdit(u)}>Edit</button>}{can("users:reset_password") && <button type="button" onClick={() => void reset(u)}>Reset password</button>}{can("users:deactivate") && <button type="button" onClick={() => void toggle(u)}>{u.is_active ? "Deactivate" : "Activate"}</button>}{!can("users:update") && !can("users:reset_password") && !can("users:deactivate") && "—"}</td>
      </tr>)}</tbody></table></div>}
      {!loading && <div className="user-pagination">
        <div className="user-pagination-controls"><button className="secondary-button" disabled={page <= 1 || loading} onClick={() => setPage(page - 1)}>Previous</button><span aria-live="polite">Page {page} of {totalPages}</span><button className="secondary-button" disabled={page >= totalPages || loading} onClick={() => setPage(page + 1)}>Next</button></div>
        <span className="user-range">Showing {rangeStart}–{rangeEnd} of {totalUsers} users</span>
      </div>}
    </section>
    {editing && !selected && <section className="panel user-form"><h2>Create user</h2>{renderUserForm()}</section>}
    {selected && users.some(u => u.id === selected.id) && <div className="modal-backdrop user-dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !editing) closeDetails(); }}>
      <section className={`customer-modal user-dialog ${editing ? "is-editing" : ""}`} role="dialog" aria-modal="true" aria-labelledby="user-dialog-title" onMouseDown={event => event.stopPropagation()}>
        <div className="customer-modal-heading user-dialog-heading"><div><div className="eyebrow">USER ACCOUNT</div><h2 id="user-dialog-title">{editing ? `Edit ${selected.full_name}` : selected.full_name}</h2>{!editing && <p className="muted">{selected.email}</p>}</div><button className="icon-button" onClick={() => editing ? setEditing(false) : closeDetails()} aria-label={editing ? "Close edit form" : "Close dialog"}>×</button></div>
        {editing ? <div className="user-form user-dialog-form">{renderUserForm()}</div> : <>
          <div className="user-detail-grid">
            <div><span>Status</span><strong className={`user-status ${selected.is_active ? "is-active" : "is-inactive"}`}>{selected.is_active ? "Active" : "Inactive"}</strong></div>
            <div><span>Role</span><strong>{selected.roles.join(", ") || "None"}</strong></div>
            <div><span>Must change password</span><strong>{selected.must_change_password ? "Yes" : "No"}</strong></div>
            <div><span>Temporary password expires</span><strong>{formatDate(selected.password_expires_at)}</strong></div>
            <div><span>Last login</span><strong>{formatDate(selected.last_login_at, "Never")}</strong></div>
            <div><span>Created</span><strong>{formatDate(selected.created_at)}</strong></div>
            <div><span>Updated</span><strong>{formatDate(selected.updated_at)}</strong></div>
          </div>
          <section className="user-permission-section"><h3>Permissions from assigned roles</h3><PermissionChips values={selectedRolePermissions} empty="No role permissions" /></section>
          <section className="user-permission-section"><h3>Current access</h3><PermissionChips values={selected.effective_permissions} empty="No permissions available" /></section>
          <section className="user-permission-section"><h3>Individual access settings</h3>{Object.keys(selected.permission_overrides).length ? <><div className="permission-chips">{Object.entries(selected.permission_overrides).sort(([a], [b]) => a.localeCompare(b)).map(([key, effect]) => <span className={`permission-chip override-${effect}`} key={key}><strong>{effect === "deny" ? "Blocked" : "Allowed"}</strong>{describePermission(key).label}</span>)}</div><p className="permission-default-note">All other permissions follow the selected roles by default.</p></> : <p className="muted">All permissions follow the selected roles by default.</p>}</section>
          <div className="customer-modal-actions user-dialog-actions"><button className="secondary-button" onClick={closeDetails}>Close</button>{can("users:update") && <button className="primary-button" onClick={() => startEdit(selected)}>Edit user</button>}</div>
        </>}
      </section>
    </div>}
  </div>;
}

function PermissionChips({ values, empty }: { values: string[]; empty: string }) {
  return values.length ? <div className="permission-chips">{values.map(permission => <span className="permission-chip" key={permission}>{describePermission(permission).label}</span>)}</div> : <p className="muted">{empty}</p>;
}
