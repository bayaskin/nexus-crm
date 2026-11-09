
import {
  NavLink,
  Outlet,
  useNavigate,
} from "react-router-dom";

import {
  Activity,
  Boxes,
  ClipboardList,
  LayoutDashboard,
  LogOut,
  Users,
} from "lucide-react";

import { useAuth } from "../auth/AuthContext";

export default function AppLayout() {
  const { user, logout, can } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">N</span>
          <span>NEXUS<span className="brand-accent">CRM</span></span>
        </div>

        <div className="sidebar-section">WORKSPACE</div>

        <nav className="navigation">
          <NavLink to="/" end>
            <LayoutDashboard size={18} />
            Overview
          </NavLink>

          {can("customers:read") && (
            <NavLink to="/customers">
              <Users size={18} />
              Customers
            </NavLink>
          )}

          {can("orders:read") && (
            <NavLink to="/orders">
              <ClipboardList size={18} />
              Orders
            </NavLink>
          )}

          {can("inventory:read") && (
            <NavLink to="/inventory">
              <Boxes size={18} />
              Inventory
            </NavLink>
          )}
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-section">SYSTEM</div>
          <div className="system-status">
            <Activity size={15} />
            API connected
          </div>
        </div>

        <div className="sidebar-user">
          <div className="avatar">
            {user?.full_name?.charAt(0).toUpperCase() ?? "U"}
          </div>
          <div className="user-info">
            <strong>{user?.full_name}</strong>
            <span>{user?.roles.join(", ") || "User"}</span>
          </div>
          <button
            className="icon-button logout-button"
            onClick={handleLogout}
            title="Sign out"
            aria-label="Sign out"
          >
            <LogOut size={17} />
          </button>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb">
            NEXUS <span>/</span> WORKSPACE
          </div>
          <div className="topbar-user">{user?.email}</div>
        </header>

        <div className="page-content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
