import { SettingsCenter } from "./settings-center";
import { normalizeSettingsSection } from "./settings-section";

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ section?: string | string[] }>;
}) {
  const query = await searchParams;
  return <SettingsCenter section={normalizeSettingsSection(query.section)} />;
}
