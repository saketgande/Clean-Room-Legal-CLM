import { titleCase } from "@/lib/utils";

interface VersionLike {
  id: string;
  version_number: number;
  source: string;
}

/** One line for the version picker. The highest number is not always the live one (an AI proposal that was
 *  never applied is newer than the text in use), so say which is current, and which are not in use. */
export function versionLabel(v: VersionLike, current: Pick<VersionLike, "id" | "version_number"> | null | undefined): string {
  const base = `V${v.version_number} · ${titleCase(v.source)}`;
  if (!current) return base;
  if (v.id === current.id) return `${base} · current`;
  return v.version_number > current.version_number ? `${base} · not in use` : `${base} · earlier`;
}
