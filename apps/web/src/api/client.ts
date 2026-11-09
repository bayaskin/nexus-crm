
import type { TokenResponse } from "../types";

const API_BASE = "/api";

const ACCESS_KEY = "nexus_access_token";
const REFRESH_KEY = "nexus_refresh_token";

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export function saveTokens(tokens: TokenResponse) {
  localStorage.setItem(ACCESS_KEY, tokens.access_token);
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

export function hasTokens() {
  return Boolean(localStorage.getItem(ACCESS_KEY));
}

function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY);
}

async function refreshAccessToken(): Promise<boolean> {
  const refreshToken = localStorage.getItem(REFRESH_KEY);

  if (!refreshToken) return false;

  try {
    const response = await fetch(`${API_BASE}/v1/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!response.ok) {
      clearTokens();
      return false;
    }

    const tokens: TokenResponse = await response.json();
    saveTokens(tokens);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

function getErrorMessage(data: unknown): string {
  if (
    typeof data === "object" &&
    data !== null &&
    "detail" in data
  ) {
    const detail = (data as { detail: unknown }).detail;

    if (typeof detail === "string") return detail;

    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          typeof item === "object" && item !== null && "msg" in item
            ? String(item.msg)
            : String(item),
        )
        .join("; ");
    }
  }

  return "The request could not be completed.";
}

export async function apiRequest<T>(
  path: string,
  options: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");

  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const token = getAccessToken();

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response: Response;

  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    });
  } catch {
    throw new ApiError("Unable to connect to Nexus API.", 0);
  }

  if (response.status === 401 && retry && token) {
    const refreshed = await refreshAccessToken();

    if (refreshed) {
      return apiRequest<T>(path, options, false);
    }

    clearTokens();
    window.dispatchEvent(new Event("nexus:unauthorized"));
    throw new ApiError("Your session has expired. Please sign in again.", 401);
  }

  if (!response.ok) {
    let data: unknown;

    try {
      data = await response.json();
    } catch {
      data = null;
    }

    const detail =
    typeof data === "object" && data !== null && "detail" in data
        ? (data as { detail: unknown }).detail
        : undefined;

    throw new ApiError(
    getErrorMessage(data),
    response.status,
    detail,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

export async function loginRequest(
  email: string,
  password: string,
) {
  const tokens = await apiRequest<TokenResponse>(
    "/v1/auth/login",
    {
      method: "POST",
      body: JSON.stringify({ email, password }),
    },
    false,
  );

  saveTokens(tokens);
  return tokens;
}

export async function logoutRequest() {
  const refreshToken = localStorage.getItem(REFRESH_KEY);

  try {
    if (refreshToken) {
      await apiRequest<void>("/v1/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      });
    }
  } finally {
    clearTokens();
  }
}
