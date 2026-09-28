type PriceInput = number | string | null | undefined;

function parsePrice(value: PriceInput): number | null {
  if (value == null || (typeof value === "string" && value.trim() === "")) {
    return null;
  }
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

export function calculateProductProfitMargin(
  purchase: PriceInput,
  selling: PriceInput,
): number | null {
  const purchasePrice = parsePrice(purchase);
  const sellingPrice = parsePrice(selling);
  if (purchasePrice == null || sellingPrice == null || sellingPrice <= 0) {
    return null;
  }

  const margin = ((sellingPrice - purchasePrice) / sellingPrice) * 100;
  return Math.round((margin + Math.sign(margin) * Number.EPSILON) * 100) / 100;
}

export function ProductProfitMargin({
  value,
  emptyLabel = "未配置",
}: {
  value: number | null | undefined;
  emptyLabel?: string;
}) {
  if (value == null || !Number.isFinite(value)) {
    return (
      <span className="tabular-nums text-muted-foreground">{emptyLabel}</span>
    );
  }

  const color =
    value > 0
      ? "text-emerald-600 dark:text-emerald-400"
      : value < 0
        ? "text-red-600 dark:text-red-400"
        : "text-foreground";
  return (
    <span className={`font-medium tabular-nums ${color}`}>
      {value.toFixed(2)}%
    </span>
  );
}
