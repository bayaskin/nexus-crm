
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  apiRequest,
  clearTokens,
  hasTokens,
  loginRequest,
  logoutRequest,
} from "../api/client";

import type { User } from "../types";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    try {
      const currentUser = await apiRequest<User>("/v1/auth/me");
      setUser(currentUser);
    } catch {
      clearTokens();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (hasTokens()) {
      void loadUser();
    } else {
      setLoading(false);
    }
  }, [loadUser]);

  useEffect(() => {
    const handleUnauthorized = () => setUser(null);

    window.addEventListener("nexus:unauthorized", handleUnauthorized);

    return () => {
      window.removeEventListener(
        "nexus:unauthorized",
        handleUnauthorized,
      );
    };
  }, []);

  const login = async (email: string, password: string) => {
    await loginRequest(email, password);
    const currentUser = await apiRequest<User>("/v1/auth/me");
    setUser(currentUser);
  };

  const logout = async () => {
    try {
      await logoutRequest();
    } finally {
      setUser(null);
    }
  };

  const can = (permission: string) => {
    if (!user) return false;

    return (
      user.is_superuser ||
      user.permissions.includes("*") ||
      user.permissions.includes(permission)
    );
  };

  return (
    <AuthContext.Provider
      value={{ user, loading, login, logout, can }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);

  if (!context) {
    throw new Error("useAuth must be used inside AuthProvider");
  }

  return context;
}
