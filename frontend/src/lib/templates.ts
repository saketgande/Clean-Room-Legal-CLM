// Pure helpers for the Templates page (spec 006): the plain-name editor notation, the wording checks that
// mirror the server's `check_body`, finding jump-to / apply rules, and permission and status helpers.
import type {
  DraftingTemplatePlaceholder,
  FindingSeverity,
  TemplateCheck,
  TemplateCheckFinding,
  TemplateStatusState,
  UserResponse,
} from "./types";

/** What the converters need from a placeholder: the code and the plain name shown in its place. */
export type FieldLabel = Pick<DraftingTemplatePlaceholder, "name" | "label">;
export type BadgeTone = "slate" | "blue" | "green" | "amber" | "red" | "violet" | "cyan";

// ---- reading `str.format` wording ------------------------------------------------------------
// Drafting fills a template with Python `str.format`, so `{{` and `}}` are literal braces and a field is a
// bare `{identifier}`. This is the same tokenizer the server uses (`string.Formatter().parse`), cut down to
// what the page needs: where the fields are, and where a brace has no partner.
type Token =
  | { type: "field"; name: string; start: number; end: number }
  | { type: "unpaired"; start: number; end: number };

function scan(text: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < text.length) {
    const c = text[i];
    if ((c === "{" || c === "}") && text[i + 1] === c) {
      i += 2; // an escaped literal brace
    } else if (c === "}") {
      out.push({ type: "unpaired", start: i, end: i + 1 });
      i += 1;
    } else if (c === "{") {
      let j = i + 1;
      while (j < text.length && text[j] !== "}" && text[j] !== "{") j += 1;
      if (text[j] === "}") {
        out.push({ type: "field", name: text.slice(i + 1, j), start: i, end: j + 1 });
        i = j + 1;
      } else {
        out.push({ type: "unpaired", start: i, end: i + 1 });
        i += 1;
      }
    } else {
      i += 1;
    }
  }
  return out;
}

function splice(text: string, edits: { start: number; end: number; with: string }[]): string {
  let out = "";
  let at = 0;
  for (const e of edits) {
    out += text.slice(at, e.start) + e.with;
    at = e.end;
  }
  return out + text.slice(at);
}

// ---- editor notation (FR-62) -----------------------------------------------------------------
/** Wording as the editor shows it: each known `{name}` becomes `«Plain name»`. Literal braces and unknown
 *  codes are left exactly as they are, so they stay visible (and are flagged by `wordingProblems`). */
export function toEditorText(text: string, fields: readonly FieldLabel[]): string {
  const label = new Map(fields.map((f) => [f.name, f.label]));
  return splice(
    text,
    scan(text).flatMap((t) =>
      t.type === "field" && label.has(t.name) ? [{ start: t.start, end: t.end, with: `«${label.get(t.name)}»` }] : [],
    ),
  );
}

/** The inverse of `toEditorText`: each `«Plain name»` of a known field becomes `{name}`. */
export function toBodyText(text: string, fields: readonly FieldLabel[]): string {
  const name = new Map(fields.map((f) => [f.label, f.name]));
  return text.replace(/«([^«»]*)»/g, (whole, label: string) => (name.has(label) ? `{${name.get(label)}}` : whole));
}

// ---- wording problems (FR-34) ----------------------------------------------------------------
export interface WordingProblem {
  kind: "unknown_field" | "unpaired_brace" | "empty";
  line: number; // 1-based
  start: number; // offsets into the text that was examined
  end: number;
  text: string; // the offending `{code}` or brace
  message: string; // the server's wording for the same case
}

/** What `check_body` would refuse, with where: an empty template, a `{code}` drafting does not fill (a
 *  conversion or format spec like `{company!r}` counts as unknown), and a brace with no partner. Works on
 *  editor text or on body text: `«Plain name»` chips hold no braces and known `{name}` codes are fine. */
export function wordingProblems(text: string, fields: readonly FieldLabel[]): WordingProblem[] {
  if (!text.trim()) {
    return [{ kind: "empty", line: 1, start: 0, end: 0, text: "", message: "The template is empty." }];
  }
  const known = new Set(fields.map((f) => f.name));
  const lineAt = (offset: number) => text.slice(0, offset).split("\n").length;
  const problems: WordingProblem[] = [];
  for (const t of scan(text)) {
    const line = lineAt(t.start);
    const shown = text.slice(t.start, t.end);
    if (t.type === "unpaired") {
      problems.push({
        kind: "unpaired_brace", line, start: t.start, end: t.end, text: shown,
        message: `The braces don't pair up (line ${line}). Write a literal brace as {{ or }}.`,
      });
    } else if (!known.has(t.name)) {
      problems.push({
        kind: "unknown_field", line, start: t.start, end: t.end, text: shown,
        message: `Unknown placeholder ${shown} (line ${line}). Use one from the list, or keep it as ordinary text by writing it as {${shown}}.`,
      });
    }
  }
  return problems;
}

/** The two parties drafting names. Which of them the wording has no field for (FR-35: saving then needs
 *  an explicit acknowledgement). */
export function missingPartyFields(text: string, fields: readonly FieldLabel[]): ("company" | "counterparty")[] {
  const used = new Set(scan(toBodyText(text, fields)).flatMap((t) => (t.type === "field" ? [t.name] : [])));
  return (["company", "counterparty"] as const).filter((p) => !used.has(p));
}

// ---- finding jump-to and apply (FR-24, FR-25) ------------------------------------------------
export interface Located { start: number; end: number; count: number }

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Where a quoted passage sits in the wording. Exact text first; when that is nowhere, the same words
 *  with any run of whitespace matching any other. `count` is how many places match (overlaps included, so
 *  an ambiguous passage is never read as unique); `start`/`end` locate the first. 0 matches: -1, -1. */
export function locatePassage(text: string, passage: string | null | undefined): Located {
  const none: Located = { start: -1, end: -1, count: 0 };
  if (!passage || !passage.trim()) return none;
  const hits: [number, number][] = [];
  for (let at = text.indexOf(passage); at !== -1; at = text.indexOf(passage, at + 1)) {
    hits.push([at, at + passage.length]);
  }
  if (!hits.length) {
    const loose = new RegExp(passage.trim().split(/\s+/).map(escapeRegExp).join("\\s+"), "g");
    for (let m = loose.exec(text); m !== null; m = loose.exec(text)) {
      hits.push([m.index, m.index + m[0].length]);
      loose.lastIndex = m.index + 1;
    }
  }
  return hits.length ? { start: hits[0][0], end: hits[0][1], count: hits.length } : none;
}

export interface ApplyInput {
  check: Pick<TemplateCheck, "state" | "unsaved" | "template_version" | "wording_sha256">;
  finding: Pick<
    TemplateCheckFinding,
    "suggestion_is_wording" | "quote" | "quote_template" | "suggested_fix" | "suggested_fix_template"
  >;
  editorText: string; // the editor as it is now, in `«Plain name»` notation
  currentVersion: number; // the saved version the editor loaded
  currentSha: string; // sha256 of that saved wording (see `sha256Hex`)
  editable: boolean; // false for a Word-based template (FR-40)
  canChange: boolean; // `templatePermissions(user).canChange`
  fields: readonly FieldLabel[]; // the template's placeholders: the quote is turned into editor notation first
}

/** Whether "Apply suggestion" is offered (FR-25). All of: the template is editable and the person may
 *  change it; the review gave suggested wording (not advice); the check completed, and is either a saved
 *  check of the current saved version and sha, or an unsaved check made against this version (the caller
 *  passes only the person's own unsaved check; a newer load moves the version on); and the passage occurs
 *  exactly once in the editor right now. */
export function canApplyFinding({
  check, finding, editorText, currentVersion, currentSha, editable, canChange, fields,
}: ApplyInput): boolean {
  if (!editable || !canChange || !finding.suggestion_is_wording) return false;
  if (!(finding.suggested_fix_template ?? finding.suggested_fix)) return false;
  if (check.state !== "completed" || check.template_version !== currentVersion) return false;
  if (!check.unsaved && check.wording_sha256 !== currentSha) return false;
  const passage = finding.quote_template ?? finding.quote;
  return locatePassage(editorText, passage === null ? null : toEditorText(passage, fields)).count === 1;
}

/** Hex SHA-256 of wording: the form of `TemplateCheck.wording_sha256` (the server hashes the body text). */
export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---- permissions, status (FR-63, FR-64) ------------------------------------------------------
/** Strict on purpose: `can()` in `lib/intake.ts` returns true for anyone, which would offer a legal
 *  reviewer controls the server then refuses. */
export function templatePermissions(user: Pick<UserResponse, "permissions"> | null | undefined) {
  const held = user?.permissions ?? [];
  const has = (p: string) => held.includes(p) || held.includes("*");
  return { canView: has("playbook:read"), canRun: has("playbook:run"), canChange: has("playbook:update") };
}

export function severityTone(severity: FindingSeverity | null | undefined): BadgeTone {
  return severity === "critical" || severity === "high" ? "red" : severity === "medium" ? "amber" : "blue";
}

/** Colour is only the second signal: the badge always carries the status in words too (FR-65). */
export function statusTone(state: TemplateStatusState, severity?: FindingSeverity | null): BadgeTone {
  switch (state) {
    case "passes": return "green";
    case "has_findings": return severity === "critical" || severity === "high" ? "red" : "amber";
    case "checking": return "blue";
    case "out_of_date":
    case "partly_checked": return "amber";
    case "couldnt_check": return "red";
    case "never_checked":
    case "no_playbook": return "slate";
  }
}

const ATTENTION: ReadonlySet<TemplateStatusState> = new Set([
  "has_findings", "out_of_date", "partly_checked", "couldnt_check", "never_checked",
]);

/** The statuses the "Needs attention" filter narrows to (FR-63). */
export const needsAttention = (state: TemplateStatusState): boolean => ATTENTION.has(state);
