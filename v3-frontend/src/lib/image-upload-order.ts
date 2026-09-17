export interface PlannedImage {
  token: string;
  source: "existing" | "staged";
}

export function buildDefaultImageOrder(
  existing: Array<{ id: number; display_order: number }>,
  staged: Array<{ id: number; upload_order: number }>,
): PlannedImage[] {
  const current = [...existing]
    .sort((a, b) => a.display_order - b.display_order || a.id - b.id)
    .map((image) => ({
      token: `existing:${image.id}`,
      source: "existing" as const,
    }));
  const pending = [...staged]
    .sort((a, b) => a.upload_order - b.upload_order || a.id - b.id)
    .map((image) => ({
      token: `staged:${image.id}`,
      source: "staged" as const,
    }));
  return [...current, ...pending];
}

export function moveImage<T>(items: readonly T[], from: number, to: number): T[] {
  const next = [...items];
  if (from < 0 || from >= next.length || to < 0 || to >= next.length || from === to) {
    return next;
  }
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function makePrimary<T extends { token: string }>(
  items: readonly T[],
  token: string,
): T[] {
  return moveImage(items, items.findIndex((item) => item.token === token), 0);
}
