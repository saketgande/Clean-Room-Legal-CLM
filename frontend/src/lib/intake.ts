import type { UserResponse } from "./types";

// RBAC disabled by request: every logged-in user passes every permission
// check, so nav items / buttons gated on `can()` are visible to everyone.
// To restore RBAC, revert this to:
//   return !!user?.permissions?.some((p) => p === perm || p === "*");
// `perm` is kept (unused) so call sites already say which permission they need
// and re-enabling RBAC is the one-line change above.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function can(user: UserResponse | null | undefined, perm: string): boolean {
  return !!user;
}

// ---- request attachments ---------------------------------------------------
// Must match the server: backend/app/intake/service.py (_DOC_MAX_BYTES and the
// upload MIME allowlist in core/config.py). Checked here only to fail fast —
// the server checks again and is the authority.
const ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;
export const ATTACHMENT_ACCEPT = ".pdf,.doc,.docx,.txt,.png,.jpg,.jpeg";
export const ATTACHMENT_LIMITS_TEXT = "PDF, Word, text, PNG or JPEG · up to 25 MB";

/** Why this file can't be attached, or null if it can. */
export function attachmentProblem(file: File): string | null {
  const ext = file.name.toLowerCase().split(".").pop() ?? "";
  if (!ATTACHMENT_ACCEPT.split(",").includes(`.${ext}`)) {
    return `"${file.name}" is not a supported file type — use PDF, Word, text, PNG or JPEG.`;
  }
  if (file.size > ATTACHMENT_MAX_BYTES) {
    return `"${file.name}" is ${(file.size / 1024 / 1024).toFixed(1)} MB — the limit is 25 MB.`;
  }
  if (file.size === 0) return `"${file.name}" is empty.`;
  return null;
}

/** A file in the shape the filing call expects. */
export async function toAttachment(file: File): Promise<{ filename: string; mime_type: string; content_b64: string }> {
  const buf = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { filename: file.name, mime_type: file.type || "application/octet-stream", content_b64: btoa(bin) };
}
