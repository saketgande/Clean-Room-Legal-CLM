// General legal question — the hand-off between Legal Intake and Ask Aegis.
//
// The intake card collects the question and the facts a lawyer needs (where,
// how urgent, which contract). From there the requester either:
//   * asks Ask Aegis first — the question is handed to a NEW chat session of
//     type "legal_question" and sent as its first message, or
//   * files it straight with Legal — a "Legal Question — General" request.
// Both paths build their text here so the two never drift apart.

/** The exact type label the backend workflow ("General Legal Question") matches. */
export const LEGAL_QUESTION_TYPE = "Legal Question — General";

/** Assistant session type for chats started from this card. */
export const LEGAL_QUESTION_SESSION_TYPE = "legal_question";

export type LegalQuestionPriority = "Low" | "Medium" | "High" | "Critical";

/** Urgency is asked in plain words and stored as the request priority. */
export const URGENCY_OPTIONS: { value: LegalQuestionPriority; label: string }[] = [
  { value: "Low", label: "No rush" },
  { value: "Medium", label: "Within a week" },
  { value: "High", label: "Within 48 hours" },
  { value: "Critical", label: "Today — it's urgent" },
];

export const QUESTION_MIN_CHARS = 15;
export const QUESTION_MAX_CHARS = 4000;
const JURISDICTION_MAX_CHARS = 120;

export interface LegalQuestionInput {
  question: string;
  jurisdiction: string;
  priority: LegalQuestionPriority;
  contract: { id: string; title: string; counterparty: string | null } | null;
}

export type LegalQuestionErrors = Partial<Record<"question" | "jurisdiction", string>>;

export function validateLegalQuestion(input: LegalQuestionInput): LegalQuestionErrors {
  const errors: LegalQuestionErrors = {};
  const q = input.question.trim();
  if (!q) errors.question = "Tell us what you want to know.";
  else if (q.length < QUESTION_MIN_CHARS)
    errors.question = `Add a little more detail (at least ${QUESTION_MIN_CHARS} characters).`;
  else if (q.length > QUESTION_MAX_CHARS)
    errors.question = `Keep it under ${QUESTION_MAX_CHARS} characters.`;
  if (input.jurisdiction.trim().length > JURISDICTION_MAX_CHARS)
    errors.jurisdiction = `Keep it under ${JURISDICTION_MAX_CHARS} characters.`;
  return errors;
}

function urgencyLabel(p: LegalQuestionPriority): string {
  return URGENCY_OPTIONS.find((o) => o.value === p)?.label ?? p;
}

function contractLabel(c: NonNullable<LegalQuestionInput["contract"]>): string {
  return c.counterparty ? `${c.title} (${c.counterparty})` : c.title;
}

/** The facts block shared by the chat message and the filed request. */
function factLines(input: LegalQuestionInput): string[] {
  return [
    `Jurisdiction: ${input.jurisdiction.trim() || "Not specified"}`,
    `Urgency: ${urgencyLabel(input.priority)}`,
    ...(input.contract ? [`Related contract: ${contractLabel(input.contract)}`] : []),
  ];
}

/** First message of the Ask Aegis chat — also what the user sees as their bubble. */
export function composeChatMessage(input: LegalQuestionInput): string {
  return [input.question.trim(), "", ...factLines(input)].join("\n");
}

/** Description of a request filed straight to Legal (no AI answer yet). */
export function composeRequestDescription(input: LegalQuestionInput): string {
  return [
    input.question.trim(),
    "",
    ...factLines(input),
    "",
    "Sent straight to Legal from the intake form — no AI answer was requested.",
  ].join("\n");
}

/** A short subject line from the question (first line, cut at a word boundary). */
export function deriveSubject(question: string, max = 90): string {
  const first = question.trim().split(/\r?\n/)[0].replace(/\s+/g, " ");
  if (first.length <= max) return first;
  const cut = first.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return `${(space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

// ---- hand-off to Ask Aegis --------------------------------------------------
// The question travels in sessionStorage, not the URL: it can be long and it is
// confidential, so it must not land in browser history, logs or referrers. The
// URL carries only a random key; the entry expires, and is removed once the
// chat it starts exists (see readHandoff / clearHandoff).

const HANDOFF_PREFIX = "aegis-legal-question:";
const HANDOFF_TTL_MS = 10 * 60 * 1000;

export interface LegalQuestionHandoff {
  message: string;
  title: string;
  contractId: string | null;
  createdAt: number;
}

export function stashHandoff(input: LegalQuestionInput, storage: Storage = sessionStorage): string {
  const key = crypto.randomUUID();
  const payload: LegalQuestionHandoff = {
    message: composeChatMessage(input),
    title: deriveSubject(input.question, 60),
    contractId: input.contract?.id ?? null,
    createdAt: Date.now(),
  };
  storage.setItem(HANDOFF_PREFIX + key, JSON.stringify(payload));
  return key;
}

/**
 * Read a hand-off WITHOUT consuming it. A missing, malformed or expired entry
 * returns null (and an unusable one is removed). A valid one stays in storage
 * until the chat for it exists — call clearHandoff() then — so a failure while
 * starting the chat (backend down, timeout, expired login) never loses the
 * question: a retry or a refresh can still use it.
 */
export function readHandoff(key: string, storage: Storage = sessionStorage, now = Date.now()): LegalQuestionHandoff | null {
  const storageKey = HANDOFF_PREFIX + key;
  const raw = storage.getItem(storageKey);
  if (raw === null) return null;
  let parsed: LegalQuestionHandoff | null = null;
  try {
    const p = JSON.parse(raw) as Partial<LegalQuestionHandoff>;
    if (typeof p.message === "string" && p.message.trim() && typeof p.createdAt === "number" && now - p.createdAt <= HANDOFF_TTL_MS) {
      parsed = {
        message: p.message,
        title: typeof p.title === "string" && p.title ? p.title : "Legal question",
        contractId: typeof p.contractId === "string" && p.contractId ? p.contractId : null,
        createdAt: p.createdAt,
      };
    }
  } catch {
    parsed = null;
  }
  if (!parsed) storage.removeItem(storageKey);
  return parsed;
}

/** Consume a hand-off once its chat has been created. */
export function clearHandoff(key: string, storage: Storage = sessionStorage): void {
  storage.removeItem(HANDOFF_PREFIX + key);
}
