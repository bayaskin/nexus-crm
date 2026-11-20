
import type { ReactNode } from "react";
import {
  BrowserRouter,
  Link,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";

import { AuthProvider, useAuth } from "./auth/AuthContext";
import AppLayout from "./components/AppLayout";

import LoginPage from "./pages/LoginPage";
import OrdersPage from "./pages/OrdersPage";
import CreateOrderPage from "./pages/CreateOrderPage";
import OrderDetailsPage from "./pages/OrderDetailsPage";
import CustomersPage from "./pages/CustomersPage";
import CustomerDetailsPage from "./pages/CustomerDetailsPage";
import InventoryPage from "./pages/InventoryPage";
import UsersPage from "./pages/UsersPage";
import ChangePasswordPage from "./pages/ChangePasswordPage";

function OverviewPage() {
  const { user, can } = useAuth();

  return (
    <div>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WORKSPACE / OVERVIEW</div>
          <h1>Welcome, {user?.full_name.split(" ")[0]}</h1>
          <p className="muted">
            Your business operations, in one place.
          </p>
        </div>
      </div>

      <section className="metric-grid">
        <article className="metric-card">
          <span>Platform</span>
          <strong>Online</strong>
          <small>Frontend connected to Nexus API</small>
        </article>
        <article className="metric-card">
          <span>Access level</span>
          <strong>{user?.is_superuser ? "Admin" : "User"}</strong>
          <small>{user?.roles.join(", ")}</small>
        </article>
        <article className="metric-card">
          <span>Permissions</span>
          <strong>{user?.permissions.includes("*") ? "All" : user?.permissions.length ?? 0}</strong>
          <small>Granted permissions</small>
        </article>
      </section>

      <section className="panel welcome-panel">
        <div className="eyebrow">GET STARTED</div>
        <h2>Business operations workspace</h2>
        <p className="muted">
          Manage customers, products, orders, and inventory from one place.
        </p>

        {can("orders:read") && (
          <Link to="/orders" className="primary-button">
            Open order management
          </Link>
        )}
      </section>
    </div>
  );
}

function ProtectedRoutes() {
  const { user, loading } = useAuth();

  if (loading) {
    return <div className="app-loading">Loading Nexus...</div>;
  }

  if (user?.must_change_password) {
    return <Routes><Route path="/change-password" element={<ChangePasswordPage />} /><Route path="*" element={<Navigate to="/change-password" replace />} /></Routes>;
  }

  return (
    <Routes>
      <Route
        path="/login"
        element={user ? <Navigate to="/orders" replace /> : <LoginPage />}
      />

      <Route
        element={user ? <AppLayout /> : <Navigate to="/login" replace />}
      >
        <Route path="/" element={<OverviewPage />} />
        <Route path="/users" element={<PermissionRoute permission="users:read"><UsersPage /></PermissionRoute>} />

        <Route
          path="/orders"
          element={
            <PermissionRoute permission="orders:read">
              <OrdersPage />
            </PermissionRoute>
          }
        />
        <Route
          path="/inventory"
          element={
            <PermissionRoute permission="inventory:read">
              <InventoryPage />
            </PermissionRoute>
          }
        />
        <Route
          path="/customers"
          element={
            <PermissionRoute permission="customers:read">
              <CustomersPage />
            </PermissionRoute>
          }
        />

        <Route
          path="/customers/:customerId"
          element={
            <PermissionRoute permission="customers:read">
              <CustomerDetailsPage />
            </PermissionRoute>
          }
        />

        <Route
          path="/orders/new"
          element={
            <PermissionRoute permission="orders:create">
              <CreateOrderPage />
            </PermissionRoute>
          }
        />

        <Route
          path="/orders/:orderId"
          element={
            <PermissionRoute permission="orders:read">
              <OrderDetailsPage />
            </PermissionRoute>
          }
        />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

function PermissionRoute({
  permission,
  children,
}: {
  permission: string;
  children: ReactNode;
}) {
  const { can } = useAuth();

  return can(permission)
    ? children
    : <div className="panel access-denied">
        <h2>Access denied</h2>
        <p className="muted">
          You don't have permission to access this resource.
        </p>
      </div>;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ProtectedRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
