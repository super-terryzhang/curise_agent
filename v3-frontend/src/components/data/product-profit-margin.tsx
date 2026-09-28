type PriceInput = number | string | null | undefined;

const MAX_PRICE_CENTS = 9_999_999_999;

function roundHalfUpSigned(dividend: number, divisor: number): number {
  const absolute = Math.abs(dividend);
  const quotient = Math.floor(absolute / divisor);
  const remainder = absolute % divisor;
  const rounded = quotient + (remainder * 2 >= divisor ? 1 : 0);
  return dividend < 0 ? -rounded : rounded;
}

function parsePriceCents(value: PriceInput): number | null {
  if (value == null || (typeof value === "string" && value.trim() === "")) {
    return null;
  }
  const raw = String(value).trim();
  const parsed = Number(raw);
  if (!Number.isFinite(parsed) || parsed < 0 || raw.length > 64) return null;

  const match = /^(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/.exec(raw);
  if (!match || (!match[1] && !match[2])) return null;
  const exponent = Number(match[3] ?? 0);
  if (!Number.isInteger(exponent) || Math.abs(exponent) > 100) return null;

  const fraction = match[2] ?? "";
  const whole = match[1] ?? "";
  const digits = `${whole}${fraction}`;
  const scaledIndex = whole.length + exponent + 2;
  const centsDigits = scaledIndex <= 0
    ? "0"
    : scaledIndex <= digits.length
      ? digits.slice(0, scaledIndex)
      : `${digits}${"0".repeat(scaledIndex - digits.length)}`;
  const cents = Number(centsDigits) + (
    scaledIndex >= 0 && scaledIndex < digits.length && digits[scaledIndex] >= "5"
      ? 1
      : 0
  );
  return Number.isSafeInteger(cents) && cents <= MAX_PRICE_CENTS ? cents : null;
}

export function calculateProductProfitMargin(
  purchase: PriceInput,
  selling: PriceInput,
): number | null {
  const purchaseCents = parsePriceCents(purchase);
  const sellingCents = parsePriceCents(selling);
  if (purchaseCents == null || sellingCents == null || sellingCents <= 0) {
    return null;
  }

  const numerator = (sellingCents - purchaseCents) * 10_000;
  return roundHalfUpSigned(numerator, sellingCents) / 100;
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
