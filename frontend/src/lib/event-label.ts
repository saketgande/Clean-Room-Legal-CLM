// A readable name for a notification's event code: "lifecycle.sla_breach" -> "Lifecycle SLA breach".
// A plain title-casing turned the acronym into "Sla", so acronyms keep their capitals here.

const ACRONYMS = new Set(["sla", "ai", "nda", "msa", "id", "url", "api", "ocr", "ip", "pdf", "docx"]);

export function eventLabel(eventType: string | null | undefined): string {
  const words = (eventType ?? "")
    .split(/[._\s-]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase());
  if (!words.length) return "";
  return words
    .map((w, i) => (ACRONYMS.has(w) ? w.toUpperCase() : i === 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(" ");
}
