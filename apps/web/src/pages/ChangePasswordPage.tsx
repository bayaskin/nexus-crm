import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { apiRequest } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export default function ChangePasswordPage() {
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const { logout } = useAuth();
  const navigate = useNavigate();
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    if (password.length < 12) { setError("Use at least 12 characters."); return; }
    if (password !== confirm) { setError("Passwords do not match."); return; }
    setSaving(true);
    try {
      await apiRequest<void>("/v1/auth/password/change", { method: "POST", body: JSON.stringify({ current_password: current, new_password: password }) });
      await logout(); navigate("/login");
    } catch (e) { setError(e instanceof Error ? e.message : "Unable to change password."); }
    finally { setSaving(false); }
  }
  return <main className="password-gate"><section className="panel user-form"><div className="eyebrow">ACCOUNT SECURITY</div><h1>Change your password</h1><p className="muted">Your temporary password must be replaced before using Nexus.</p>{error && <div className="form-error" role="alert">{error}</div>}<form onSubmit={submit}><label>Current password<input type="password" autoComplete="current-password" required value={current} onChange={e => setCurrent(e.target.value)} /></label><label>New password<input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={e => setPassword(e.target.value)} /></label><label>Confirm new password<input type="password" autoComplete="new-password" required value={confirm} onChange={e => setConfirm(e.target.value)} /></label><button className="primary-button" disabled={saving}>{saving ? "Updating…" : "Change password"}</button></form></section></main>;
}
