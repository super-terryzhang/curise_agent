// Cross-page module cache for the documents list + detail views.
//
// Lives outside the page components so `handleLogout` can wipe it — otherwise
// the module state survives client-side navigation and briefly leaks the
// previous user's cached list after re-login. Post-refactor (2026-07-03) the
// data is company-wide so this is UX polish, not a security fix.

import type { DocumentDetail, DocumentSummary } from "./documents-api";

let _cachedDocuments: DocumentSummary[] | null = null;
let _cachedTotal = 0;
const _docDetailCache = new Map<number, DocumentDetail>();

export function getCachedDocumentsList(): {
  items: DocumentSummary[] | null;
  total: number;
} {
  return { items: _cachedDocuments, total: _cachedTotal };
}

export function setCachedDocumentsList(
  items: DocumentSummary[],
  total: number,
): void {
  _cachedDocuments = items;
  _cachedTotal = total;
}

export function getCachedDocumentDetail(id: number): DocumentDetail | undefined {
  return _docDetailCache.get(id);
}

export function setCachedDocumentDetail(id: number, doc: DocumentDetail): void {
  _docDetailCache.set(id, doc);
}

export function clearDocumentsCache(): void {
  _cachedDocuments = null;
  _cachedTotal = 0;
  _docDetailCache.clear();
}
