export function normalizeSettingsSection(
  section: string | string[] | undefined,
): string | undefined {
  return typeof section === "string" ? section : undefined;
}
