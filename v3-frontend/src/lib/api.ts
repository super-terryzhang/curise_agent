import { getToken, getRefreshToken, saveAuth } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

interface LoginRequest {
  email: string;
  password: string;
}

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: string;
  is_active: boolean;
  is_default_password: boolean;
  /**
   * White-list capability keys granted to this user. Empty for users
   * with no per-feature grants. Treat `role === "superadmin"` as
   * implicitly carrying every capability — the backend's
   * `require_capability` does the same bypass, and surfacing every cap
   * in this array for su would be redundant work on every /me call.
   */
  capabilities?: string[];
}

export interface LoginResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export async function login(data: LoginRequest): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });

  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "登录失败");
  }

  return res.json();
}

export async function getMe(token: string): Promise<User> {
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });

  if (!res.ok) {
    throw new Error("认证失败");
  }

  return res.json();
}

async function refreshUnderLock(): Promise<LoginResponse | null> {
  const refreshToken = getRefreshToken();
  if (!refreshToken) return null;

  try {
    const res = await fetch(`${API_BASE}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });

    if (!res.ok) return null;

    const data: LoginResponse = await res.json();
    // Never resurrect a logout or overwrite a newer session with a late response.
    if (getRefreshToken() !== refreshToken) return null;
    saveAuth(data.access_token, data.user, data.refresh_token);
    return data;
  } catch {
    return null;
  }
}

export async function refreshAccessToken(): Promise<LoginResponse | null> {
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request("cruise-auth-refresh", refreshUnderLock);
  }
  return refreshUnderLock();
}

export async function logoutApi(): Promise<void> {
  const refreshToken = getRefreshToken();
  const token = getToken();
  if (!refreshToken && !token) return;
  const res = await fetch(`${API_BASE}/api/auth/logout`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ refresh_token: refreshToken || "" }),
  });
  if (!res.ok) throw new Error("退出未完成，请重试");
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<LoginResponse> {
  const token = getToken();
  const res = await fetch(`${API_BASE}/api/auth/change-password`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });

  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "修改密码失败");
  }

  return res.json();
}
