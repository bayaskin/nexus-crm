import React from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

function App() {
  const [apiStatus, setApiStatus] = React.useState('Checking API…');

  React.useEffect(() => {
    fetch('/api/health')
      .then((response) => {
        if (!response.ok) throw new Error('API unavailable');
        return response.json();
      })
      .then((data: { status: string }) => setApiStatus(data.status === 'ok' ? 'Connected' : 'Unavailable'))
      .catch(() => setApiStatus('Unavailable'));
  }, []);

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">NEXUS<span>CRM</span></div>
        <p className="nav-label">WORKSPACE</p>
        <a className="active" href="#dashboard">Overview</a>
        <a href="#customers">Customers</a>
        <a href="#orders">Orders</a>
        <a href="#inventory">Inventory</a>
        <p className="nav-label">ADMINISTRATION</p>
        <a href="#users">Users &amp; Roles</a>
        <a href="#audit">Audit Log</a>
      </aside>
      <section className="content">
        <header><div><p className="eyebrow">WORKSPACE / OVERVIEW</p><h1>Welcome to Nexus</h1><p className="muted">Your business operations, in one place.</p></div><div className="status"><i /> API {apiStatus}</div></header>
        <section className="cards">
          <article><p className="muted">CUSTOMERS</p><strong>—</strong><small>CRM module coming next</small></article>
          <article><p className="muted">OPEN ORDERS</p><strong>—</strong><small>Order management coming next</small></article>
          <article><p className="muted">PRODUCTS</p><strong>—</strong><small>Inventory module coming next</small></article>
        </section>
        <article className="welcome"><p className="eyebrow">PROJECT STATUS</p><h2>Foundation is running.</h2><p>The frontend, API, reverse proxy, and MySQL service are separated into containers. Next we will implement database migrations, authentication, and the first CRM entities.</p><code>GET /api/health</code></article>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
