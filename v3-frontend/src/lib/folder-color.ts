// Resolve the display color for a folder — user-picked value wins, palette
// fallback fills in for legacy rows.
//
// Same folder id → same fallback color, across sidebar + document rows +
// detail-page breadcrumb. When the user picks a specific hex in the UI, it
// lands on `folder.color` and every render site starts using it immediately
// (they all look up the same folder object from the shared folder list).
// Root docs / null id → null so callers can render "no dot" instead of
// picking an arbitrary hue.

const PALETTE = [
  "#ef4444", // red
  "#f97316", // orange
  "#f59e0b", // amber
  "#eab308", // yellow
  "#84cc16", // lime
  "#22c55e", // green
  "#10b981", // emerald
  "#06b6d4", // cyan
  "#3b82f6", // blue
  "#6366f1", // indigo
  "#8b5cf6", // violet
  "#ec4899", // pink
];

// Public: the swatches surfaced in the color-picker popover. Keep in sync
// with the backend `_ALLOWED_COLORS` set in
// v3_backend/domains/document/folders/service.py (mirrored intentionally so
// palette shifts don't require a schema migration).
export const FOLDER_COLOR_SWATCHES = PALETTE;

type FolderLike = {
  id: number;
  color?: string | null;
};

/**
 * Return the hex color to render for this folder.
 *
 * Accepts either a folder object (preferred — picks up the user's stored
 * choice) or a bare id (legacy call sites; always falls back to palette).
 * Returns null for null/undefined input so callers render "no dot" for
 * root-level docs.
 */
export function folderColor(
  folder: FolderLike | number | null | undefined,
): string | null {
  if (folder == null) return null;
  if (typeof folder === "number") {
    return PALETTE[folder % PALETTE.length];
  }
  if (folder.color) return folder.color;
  return PALETTE[folder.id % PALETTE.length];
}
