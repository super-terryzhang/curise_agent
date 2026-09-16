"use client";

/**
 * usePageContext — resolves the user's current URL into a one-line
 * human-readable string that gets attached to every outgoing chat
 * message and read by the backend as a per-turn system-prompt overlay.
 *
 * Design rules:
 *   - Pure, dependency-light: takes only Next.js pathname; no API
 *     calls, no entity fetches. The backend can still query DB for
 *     specifics (it has tools); we just give it the orientation.
 *   - One line, max ~120 chars. The overlay sits in attention's high
 *     region but isn't free — keep it scoped.
 *   - English on the backend side ("user is viewing order #123") so
 *     it composes naturally with the English system prompt. The UI
 *     surface around the chat stays Chinese; this is just orientation.
 *   - Don't leak: query strings (potential PII like ?token=...),
 *     fragment, anything beyond pathname segments.
 *
 * Output shape — a stable string (or null for "no useful context"):
 *   "/dashboard"                        → null  (root, no signal)
 *   "/dashboard/agent"                  → null  (agent page is the chat itself)
 *   "/dashboard/orders"                 → "user is on the orders list"
 *   "/dashboard/orders/123"             → "user is viewing order #123"
 *   "/dashboard/documents/abc"          → "user is viewing document abc"
 *   "/dashboard/data"                   → "user is on the data management page"
 *   "/dashboard/settings"               → "user is on the settings page"
 *   "/dashboard/users"                  → "user is on the user management page"
 *   anything else                       → "user is on the {pathname} page"
 *
 * If you add a new dashboard route, prefer extending this mapping
 * over relying on the generic fallback — the more specific the
 * orientation, the less the agent has to infer from raw URL text.
 */

import { usePathname } from "next/navigation";
import { useMemo } from "react";

export function buildPageContext(pathname: string | null): string | null {
  if (!pathname) return null;
  // The agent workspace is itself the chat surface — the URL only
  // encodes session id (already implicit). No useful orientation.
  if (pathname.startsWith("/dashboard/agent")) return null;
  if (pathname.startsWith("/dashboard/workbench/ai")) return null;

  // Strip trailing slash for normalization.
  const path = pathname.replace(/\/+$/, "");

  // Detail routes — one ID segment after the entity name.
  const orderDetail = path.match(/^\/dashboard\/orders\/([^/]+)$/);
  if (orderDetail) return `user is viewing order #${orderDetail[1]}`;
  const documentDetail = path.match(/^\/dashboard\/documents\/([^/]+)$/);
  if (documentDetail)
    return `user is viewing document ${documentDetail[1]}`;

  // List + admin pages.
  if (path === "/dashboard/orders") return "user is on the orders list page";
  if (path === "/dashboard/documents")
    return "user is on the documents list page";
  if (path === "/dashboard/data")
    return "user is on the data management page (countries / categories / ports / suppliers / products / exchange-rates)";
  if (path === "/dashboard/settings")
    return "user is on the settings page (field schemas / order formats / supplier templates / delivery locations / company info)";
  if (path === "/dashboard/users")
    return "user is on the user management page";

  // Dashboard root — too generic to mention.
  if (path === "/dashboard" || path === "") return null;

  // Catch-all for any future routes — better generic context than none.
  return `user is on the ${path} page`;
}

/**
 * React hook variant. Memoized so the string identity is stable
 * across renders for the same pathname (lets callers use it in
 * dependency arrays without thrashing).
 */
export function usePageContext(): string | null {
  const pathname = usePathname();
  return useMemo(() => buildPageContext(pathname), [pathname]);
}
