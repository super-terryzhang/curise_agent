import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearAuth, getRefreshToken, getToken, saveAuth } from "./auth";
import { logoutApi, refreshAccessToken, type User } from "./api";
import { fetchWithAuth } from "./fetch-with-auth";

const user: User = { id: 1, email: "test@example.test", full_name: "Test", role: "employee", is_active: true, is_default_password: false };
const response = (refresh = "rotated") => new Response(JSON.stringify({ access_token: "new-access", refresh_token: refresh, user }), { status: 200 });

beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => values.get(k) ?? null, setItem: (k: string, v: string) => values.set(k, v), removeItem: (k: string) => values.delete(k) });
  vi.stubGlobal("window", { location: { href: "/dashboard" } });
  vi.stubGlobal("navigator", {});
  saveAuth("old-access", user, "old-refresh");
});
afterEach(() => vi.unstubAllGlobals());

describe("session browser boundaries", () => {
  it("restricted login removes a refresh token from a previous session", () => {
    saveAuth("restricted", { ...user, is_default_password: true }, "");
    expect(getRefreshToken()).toBeNull();
  });
  it("a delayed refresh never revives logout", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { clearAuth(); return response(); }));
    expect(await refreshAccessToken()).toBeNull();
    expect(getToken()).toBeNull();
  });
  it("a delayed failed request does not clear a newer login", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response("", { status: 401 }))
      .mockImplementationOnce(async () => { saveAuth("other-login", user, "other-refresh"); return response(); }));
    expect((await fetchWithAuth("/api/orders")).status).toBe(401);
    expect(getToken()).toBe("other-login");
    expect(window.location.href).toBe("/dashboard");
  });
  it("failed server logout retains local credentials for retry", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 503 })));
    await expect(logoutApi()).rejects.toThrow();
    expect(getToken()).toBe("old-access");
  });
  it("restricted sessions can revoke via their bearer token", async () => {
    saveAuth("restricted", user, "");
    const fetcher = vi.fn<typeof fetch>(async () => response());
    vi.stubGlobal("fetch", fetcher);
    await logoutApi();
    expect(fetcher.mock.calls[0]?.[1]).toMatchObject({ headers: { Authorization: "Bearer restricted" }, body: JSON.stringify({ refresh_token: "" }) });
  });
  it("cross-tab locks serialize rotation and read the latest stored token", async () => {
    let queue: Promise<unknown> = Promise.resolve();
    vi.stubGlobal("navigator", { locks: { request: vi.fn((_name: string, callback: () => Promise<unknown>) => { queue = queue.then(callback); return queue; }) } });
    const sent: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => { sent.push(JSON.parse(init.body as string).refresh_token); return response(`rotated-${sent.length}`); }));
    const results = await Promise.all([refreshAccessToken(), refreshAccessToken()]);
    expect(results.every(Boolean)).toBe(true);
    expect(sent).toEqual(["old-refresh", "rotated-1"]);
    expect(getRefreshToken()).toBe("rotated-2");
  });
});
