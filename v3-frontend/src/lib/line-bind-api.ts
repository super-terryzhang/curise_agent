/**
 * LINE bind endpoint — consumes a one-shot bind token sent via the LINE bot.
 *
 * The flow:
 * 1. User sends any message to the bot in LINE.
 * 2. Bot replies with `${API_BASE}/line/bind?token=<plaintext>` — they tap it.
 * 3. This page collects email + password.
 * 4. We POST to /api/line/bind/{token}; backend verifies password +
 *    consumes the (one-shot, time-limited) token + creates the LineUser row.
 *
 * Errors map to specific user-facing messages — see `BindErrorCode` below.
 * Note: this endpoint does NOT issue auth tokens; the user is "logged in
 * to LINE", not to the web app. Don't call `saveAuth()` on success.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001";

export interface BindRequest {
  email: string;
  password: string;
}

export interface BindResponse {
  line_user_id: string;
  user_id: number;
  user_email: string;
  user_role: string;
}

export type BindErrorCode =
  | "invalid_token"   // 400 — backend says token is unknown/expired/used
  | "bad_credentials" // 401 — wrong email or password
  | "already_bound"   // 409 — this LINE account is bound to a different user
  | "network"         // network failure or 5xx
  | "unknown";

export class BindError extends Error {
  code: BindErrorCode;
  constructor(code: BindErrorCode, message: string) {
    super(message);
    this.code = code;
  }
}

export async function bindLine(token: string, body: BindRequest): Promise<BindResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/line/bind/${encodeURIComponent(token)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    throw new BindError("network", "网络错误，请检查您的连接后重试");
  }

  if (res.ok) {
    return res.json();
  }

  // Try to read the backend's detail message — fall back to status-based defaults.
  let detail = "";
  try {
    const data = await res.json();
    detail = (data && (data.detail || data.message)) || "";
  } catch {
    // body wasn't JSON
  }

  if (res.status === 400) {
    throw new BindError(
      "invalid_token",
      detail || "绑定链接已失效，请回 LINE 发送任意消息重新获取链接",
    );
  }
  if (res.status === 401) {
    throw new BindError("bad_credentials", detail || "邮箱或密码错误");
  }
  if (res.status === 409) {
    throw new BindError(
      "already_bound",
      detail || "此 LINE 账号已绑定其他公司账号，请联系管理员",
    );
  }
  throw new BindError("unknown", detail || `服务器错误 (${res.status})`);
}
