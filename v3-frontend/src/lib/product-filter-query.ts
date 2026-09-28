export function normalizeProductPortFilter(value: string): number | undefined {
  if (!/^[1-9]\d*$/.test(value)) return undefined;
  const portId = Number(value);
  return Number.isSafeInteger(portId) ? portId : undefined;
}
