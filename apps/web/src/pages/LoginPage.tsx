
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Layers3, LockKeyhole } from "lucide-react";
import { useAuth } from "../auth/AuthContext";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setSubmitting(true);

    try {
      await login(email, password);
      navigate("/orders");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Sign in failed",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-screen">
      <section className="login-card">
        <div className="login-brand">
          <div className="login-logo"><Layers3 size={26} /></div>
          <div className="brand">
            NEXUS<span className="brand-accent">CRM</span>
          </div>
        </div>

        <h1>Welcome back</h1>
        <p className="muted">Sign in to manage your workspace.</p>

        <form onSubmit={handleSubmit} className="login-form">
          <label>
            Email address
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@company.com"
              autoComplete="username"
              required
            />
          </label>

          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Enter your password"
              autoComplete="current-password"
              required
            />
          </label>

          {error && <div className="alert alert-error">{error}</div>}

          <button
            type="submit"
            className="primary-button login-submit"
            disabled={submitting}
          >
            <LockKeyhole size={17} />
            {submitting ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="login-footer">Nexus CRM · Business Operations Platform</p>
      </section>
    </main>
  );
}
