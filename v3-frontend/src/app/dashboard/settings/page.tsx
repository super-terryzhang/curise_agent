import {
  normalizeSettingsSection,
  SettingsCenter,
} from "./settings-center";

export default async function SettingsPage({
  searchParams,
}: {
  searchParams: Promise<{ section?: string | string[] }>;
}) {
  const query = await searchParams;
  return <SettingsCenter section={normalizeSettingsSection(query.section)} />;
}
