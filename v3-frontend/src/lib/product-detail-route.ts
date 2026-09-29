export type ProductDetailTab = "basic" | "prices" | "images";

export function normalizeProductDetailTab(value: string | null | undefined): ProductDetailTab {
  return value === "prices" || value === "images" ? value : "basic";
}

export function productDetailHref(
  productId: number,
  options: { tab?: ProductDetailTab; edit?: boolean } = {},
): string {
  const params = new URLSearchParams({ tab: options.tab ?? "basic" });
  if (options.edit) params.set("edit", "1");
  return `/dashboard/data/products/${productId}?${params.toString()}`;
}

export function legacyProductDetailHref(
  productId: string | null | undefined,
  action: string | null | undefined,
): string | null {
  const id = Number(productId);
  if (!Number.isInteger(id) || id <= 0) return null;
  if (action === "prices") return productDetailHref(id, { tab: "prices" });
  if (action === "edit") return productDetailHref(id, { tab: "basic", edit: true });
  return productDetailHref(id);
}
