/**
 * Answers that cannot be right, caught while the person can still fix them (round 2, bug 7). The server
 * refuses the same answers when the request is filed (intake/service.py: looks_like_email and the
 * form's `not_before` rule), so what the wizard accepts is what filing accepts.
 */

/** One "@", something either side of it, a dot in the domain, no spaces. Only sending can tell whether the
 *  mailbox exists; this catches the typos that can never be delivered. */
export function looksLikeEmail(text: string): boolean {
  const t = text.trim();
  const at = t.indexOf("@");
  if (t.length > 254 || at < 1 || t.indexOf("@", at + 1) !== -1 || /\s/.test(t)) return false;
  const domain = t.slice(at + 1);
  return /^[^.].*\.[^.]+$/.test(domain) && !domain.includes("..");
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export const EMAIL_PROBLEM = "Enter a full email address, like name@company.com.";

/**
 * What is wrong with an answer that is there, or null. `kind` is the server's kind for the question;
 * `notBefore` is the answer to the date this one must not come before, with that question's label.
 */
export function answerProblem(
  kind: string | undefined,
  value: string | string[] | undefined,
  notBefore?: { label: string; value: string | string[] | undefined },
): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  if (kind === "email" && !looksLikeEmail(value)) return EMAIL_PROBLEM;
  if (kind === "date" && notBefore && typeof notBefore.value === "string") {
    // Both are ISO days (YYYY-MM-DD), which sort the way the days do.
    if (ISO_DAY.test(value) && ISO_DAY.test(notBefore.value) && value < notBefore.value)
      return `Can't be before ${notBefore.label.toLowerCase()}.`;
  }
  return null;
}
