import { useEffect, useState } from "react";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Person = { id: string; first_name: string; last_name: string; full_name: string; email: string; roles: string[]; is_active: boolean; last_login_at: string | null; created_at: string; updated_at: string; password_expires_at: string | null; permission_overrides: Record<string, string>; effective_permissions: string[]; must_change_password: boolean };
type Metadata = { roles: { name: string; permissions: string[] }[]; permissions: string[] };

export default function UsersPage() {
  const { can } = useAuth();
  const [users, setUsers] = useState<Person[]>([]);
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
  const inheritedPermissions = metadata.roles.filter(item => form.role_names.includes(item.name)).flatMap(item => item.permissions).filter((permission, index, all) => all.indexOf(permission) === index).sort();

  async function reload() {
    setLoading(true); setError("");
    try {
      const [usersResult, metaResult] = await Promise.allSettled([
        apiRequest<Person[]>(`/v1/users?search=${encodeURIComponent(search)}&status=${status}&role=${encodeURIComponent(role)}&page=${page}&page_size=25`),
        apiRequest<Metadata>("/v1/users/metadata"),
      ]);
      if (usersResult.status === "rejected") throw usersResult.reason;
      setUsers(usersResult.value);
      if (metaResult.status === "fulfilled") setMetadata(metaResult.value);
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to load users."); }
    finally { setLoading(false); }
  }
  useEffect(() => { setPage(1); }, [search, status, role]);
  useEffect(() => { void reload(); }, [search, status, role, page]);

  function startEdit(user: Person) {
    setEditing(true);
    setSelected(user);
    setForm({ first_name: user.first_name, last_name: user.last_name, email: user.email, role_names: user.roles, send_credentials: true, permission_overrides: user.permission_overrides });
    setNotice("");
  }
  function startCreate() { setSelected(null); setEditing(true); setForm({ first_name: "", last_name: "", email: "", role_names: [], send_credentials: true, permission_overrides: {} }); setNotice(""); }
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setSaving(true); setError(""); setNotice("");
    try {
      const payload: Record<string, unknown> = { first_name: form.first_name, last_name: form.last_name, email: form.email };
      if (can("roles:assign")) payload.role_names = form.role_names;
      if (can("permissions:manage")) payload.permission_overrides = form.permission_overrides;
      if (!selected) payload.send_credentials = form.send_credentials;
      const result = await apiRequest<Person & { email_sent?: boolean; message?: string; temporary_password?: string }>(selected ? `/v1/users/${selected.id}` : "/v1/users", {
        method: selected ? "PATCH" : "POST", body: JSON.stringify(payload),
      });
      setNotice(selected ? "User updated." : result.email_sent ? "User created and credentials emailed." : `${result.message || "User created; credentials email was not sent."}${result.temporary_password ? ` Temporary password (copy now): ${result.temporary_password}` : ""}`);
      setSelected(null); setEditing(false); await reload();
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to save user."); }
    finally { setSaving(false); }
  }
  async function toggle(user: Person) {
    if (user.is_active && !window.confirm(`Deactivate ${user.full_name}? Their current sessions will stop working.`)) return;
    try { await apiRequest(`/v1/users/${user.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !user.is_active }) }); setNotice(`${user.full_name} ${user.is_active ? "deactivated" : "activated"}.`); await reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to update account status."); }
  }
  async function reset(user: Person) {
    if (!window.confirm(`Generate a temporary password for ${user.full_name}?`)) return;
    try { const result = await apiRequest<{ email_sent: boolean; temporary_password?: string }>(`/v1/users/${user.id}/reset-password`, { method: "POST" }); setNotice(result.email_sent ? `Password reset email sent to ${user.email}.` : `Password reset for ${user.email}, but email delivery failed. Check SMTP/MailHog and retry Reset password; no password was returned.${result.temporary_password ? ` Temporary password (copy now): ${result.temporary_password}` : ""}`); }
    catch (e) { setError(e instanceof Error ? e.message : "Unable to reset password."); }
  }

  return <div>
    <div className="page-heading"><div><div className="eyebrow">SYSTEM / ACCESS CONTROL</div><h1>User management</h1><p className="muted">Create accounts, assign roles, and control individual permissions.</p></div></div>
    {(error || notice) && <div className={error ? "form-error" : "form-success"} role="status">{error || notice}</div>}
    <section className="panel user-management">
      <div className="table-toolbar"><input aria-label="Search users" placeholder="Search name or email" value={search} onChange={e => setSearch(e.target.value)} />
        <select aria-label="Filter by role" value={role} onChange={e => setRole(e.target.value)}><option value="">All roles</option>{metadata.roles.map(r => <option key={r.name}>{r.name}</option>)}</select>
        <select aria-label="Filter by status" value={status} onChange={e => setStatus(e.target.value)}><option value="">All statuses</option><option value="active">Active</option><option value="inactive">Inactive</option></select>
        {can("users:create") && <button className="primary-button" type="button" onClick={startCreate}>Create user</button>}
      </div>
      {loading ? <div className="empty-state">Loading users…</div> : users.length === 0 ? <div className="empty-state">No users match these filters.</div> : <div className="table-wrap"><table><thead><tr><th>Name</th><th>Email</th><th>Roles</th><th>Status</th><th>Last login</th><th>Created</th><th>Actions</th></tr></thead><tbody>{users.map(u => <tr key={u.id}>
        <td><button className="text-link" onClick={() => setSelected(selected?.id === u.id ? null : u)}>{u.full_name}</button></td><td>{u.email}</td><td>{u.roles.join(", ") || "—"}</td><td>{u.is_active ? "Active" : "Inactive"}</td><td>{u.last_login_at ? new Date(u.last_login_at).toLocaleDateString() : "Never"}</td><td>{new Date(u.created_at).toLocaleDateString()}</td>
        <td className="user-actions">{can("users:update") && <button type="button" onClick={() => startEdit(u)}>Edit</button>}{can("users:reset_password") && <button type="button" onClick={() => void reset(u)}>Reset password</button>}{can("users:deactivate") && <button type="button" onClick={() => void toggle(u)}>{u.is_active ? "Deactivate" : "Activate"}</button>}{!can("users:update") && !can("users:reset_password") && !can("users:deactivate") && "—"}</td>
      </tr>)}</tbody></table></div>}
      {!loading && <div className="user-pagination"><span>Page {page}</span><button className="secondary-button" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button><button className="secondary-button" disabled={users.length < 25} onClick={() => setPage(page + 1)}>Next</button></div>}
    </section>
    {selected && !editing && users.some(u => u.id === selected.id) && <section className="panel user-detail"><h2>{selected.full_name}</h2><p>{selected.email} · {selected.is_active ? "Active" : "Inactive"}</p><p className="muted">Must change password: {selected.must_change_password ? "Yes" : "No"} · Temporary password expires: {selected.password_expires_at ? new Date(selected.password_expires_at).toLocaleString() : "Not set"}</p><p className="muted">Last login: {selected.last_login_at ? new Date(selected.last_login_at).toLocaleString() : "Never"} · Created: {new Date(selected.created_at).toLocaleString()} · Updated: {new Date(selected.updated_at).toLocaleString()}</p><strong>Inherited role permissions</strong><p className="muted">{metadata.roles.filter(item => selected.roles.includes(item.name)).flatMap(item => item.permissions).filter((permission, index, all) => all.indexOf(permission) === index).sort().join(", ") || "None"}</p><strong>Effective permissions</strong><p className="muted">{selected.effective_permissions.join(", ") || "None"}</p><strong>Overrides</strong><p className="muted">{Object.entries(selected.permission_overrides).map(([p, e]) => `${p}: ${e}`).join(", ") || "None"}</p><button className="secondary-button" onClick={() => setSelected(null)}>Close details</button></section>}
    {editing && <section className="panel user-form"><h2>{selected ? `Edit ${selected.full_name}` : "Create user"}</h2><form onSubmit={submit}>
      <div className="user-form-grid"><label>First name<input required maxLength={80} value={form.first_name} onChange={e => setForm({ ...form, first_name: e.target.value })} /></label><label>Last name<input required maxLength={80} value={form.last_name} onChange={e => setForm({ ...form, last_name: e.target.value })} /></label><label>Email<input required type="email" value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></label>
      {can("roles:assign") ? <label>Roles<select multiple value={form.role_names} onChange={e => setForm({ ...form, role_names: Array.from(e.target.selectedOptions, option => option.value) })}>{metadata.roles.map(r => <option key={r.name} value={r.name}>{r.name}</option>)}</select></label> : selected && <div className="muted">Roles: {selected.roles.join(", ") || "None"}</div>}</div>
      <fieldset><legend>Permission overrides</legend><p className="muted">Inherited from selected roles: {inheritedPermissions.join(", ") || "None"}</p>{can("permissions:manage") ? <div className="permission-grid">{metadata.permissions.map(permission => <label key={permission}>{permission}<select aria-label={`${permission} override`} value={form.permission_overrides[permission] || "inherit"} onChange={e => { const next = { ...form.permission_overrides }; if (e.target.value === "inherit") delete next[permission]; else next[permission] = e.target.value; setForm({ ...form, permission_overrides: next }); }}><option value="inherit">Inherited</option><option value="allow">Explicit allow</option><option value="deny">Explicit deny</option></select></label>)}</div> : <p className="muted">You do not have permission to edit individual overrides.</p>}</fieldset>
      {!selected && <label className="user-check"><input type="checkbox" checked={form.send_credentials} onChange={e => setForm({ ...form, send_credentials: e.target.checked })} /> Email temporary credentials (required password change)</label>}
      <div className="form-actions"><button className="primary-button" disabled={saving}>{saving ? "Saving…" : selected ? "Save changes" : "Create user"}</button><button type="button" className="secondary-button" onClick={() => { setEditing(false); setSelected(null); }}>Cancel</button></div>
    </form></section>}
  </div>;
}
