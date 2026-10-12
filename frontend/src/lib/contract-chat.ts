// The contract chat: what an answer's live events add up to, in one place that has
// no React in it. The server streams one answer as events (see backend
// assistant/routes.py): text arrives in pieces, tools start and finish, and an
// action that needs a yes pauses the answer with a confirmation.
//
// Nothing here talks to the server; the hook that does (_use-contract-chat.ts)
// feeds events into `applyEvent` and shows what comes out.

import type { AssistantMessage } from "./types";

/** `refused` is a rule saying no (for example "already in Signature"): the step was not done, but nothing broke. */
export type ToolStatus = "running" | "done" | "refused" | "error";

/** A tool step as the server saved it inside an answer (assistant message metadata). */
export interface ToolBlock {
  type: "tool";
  name: string;
  status: ToolStatus;
}
export interface ContentBlock {
  type: "content";
  text: string;
}
export type Block = ContentBlock | ToolBlock;

export interface ChatItem {
  id: string;
  role: "user" | "assistant" | "tool" | "system";
  text: string;
  /** A saved answer keeps its steps in order, so a reopened chat shows what happened. */
  blocks?: Block[];
  tool?: { name: string; status: ToolStatus; toolUseId?: string };
}

export interface Confirmation {
  confirmationId: string;
  assistantRunId: string;
  toolName: string;
  /** What the person is being asked to approve, as the server wrote it. */
  details: Record<string, string>;
}

export interface ChatState {
  items: ChatItem[];
  pending: Confirmation | null;
  runId: string | null;
  ended: boolean;
  /** A tool ran during this answer, so what is on the page may be out of date. */
  toolRan: boolean;
  error: string | null;
}

export const initialChatState: ChatState = { items: [], pending: null, runId: null, ended: false, toolRan: false, error: null };

export const clean = (state: ChatState): ChatState => ({ ...state, runId: null, ended: false, toolRan: false, error: null });

export function confirmationDetails(raw: unknown): Record<string, string> {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw as Record<string, unknown>).filter((e): e is [string, string] => typeof e[1] === "string" && e[1].length > 0),
  );
}

type Data = Record<string, unknown>;

/** Fold one live event into the chat. Pure: returns a new state. */
export function applyEvent(state: ChatState, event: string, data: Data, newId: () => string = () => crypto.randomUUID()): ChatState {
  switch (event) {
    case "session_started":
      return typeof data.assistant_run_id === "string" ? { ...state, runId: data.assistant_run_id } : state;

    case "message_delta": {
      const text = String(data.text ?? "");
      const last = state.items[state.items.length - 1];
      if (last && last.role === "assistant" && !last.tool && !last.blocks)
        return { ...state, items: [...state.items.slice(0, -1), { ...last, text: last.text + text }] };
      return { ...state, items: [...state.items, { id: newId(), role: "assistant", text }] };
    }

    case "tool_started": {
      const name = String(data.tool_name ?? "tool");
      const toolUseId = typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      if (toolUseId && state.items.some((i) => i.tool?.toolUseId === toolUseId)) return state;
      return { ...state, items: [...state.items, { id: newId(), role: "tool", text: name, tool: { name, status: "running", toolUseId } }] };
    }

    case "tool_finished": {
      const toolUseId = typeof data.tool_use_id === "string" ? data.tool_use_id : undefined;
      const items = state.items.map((i) =>
        i.tool && i.tool.status === "running" && (!toolUseId || i.tool.toolUseId === toolUseId)
          ? { ...i, tool: { ...i.tool, status: (data.error ? (data.refused ? "refused" : "error") : "done") as ToolStatus } }
          : i,
      );
      return { ...state, items, toolRan: true };
    }

    // A tracked change was written: whatever was on screen about this contract is stale.
    case "tracked_change_created":
      return { ...state, toolRan: true };

    case "confirmation_required":
      return {
        ...state,
        pending: {
          confirmationId: String(data.confirmation_id),
          assistantRunId: String(data.assistant_run_id),
          toolName: String(data.tool_name),
          details: confirmationDetails(data.details),
        },
      };

    case "error":
      return { ...state, error: String(data.message ?? "Aegis couldn't finish that answer.") };

    case "done":
      return { ...state, ended: true };

    default:
      return state;
  }
}

/** The saved conversation as chat items. An answer keeps its steps (blocks) when it has them. */
export function itemsFromMessages(messages: Pick<AssistantMessage, "id" | "role" | "content" | "metadata_json">[]): ChatItem[] {
  return messages.map((m) => {
    const raw = (m.metadata_json as { blocks?: unknown } | null | undefined)?.blocks;
    const blocks = m.role === "assistant" && Array.isArray(raw) ? (raw as Block[]).filter((b) => b && (b.type === "content" || b.type === "tool")) : [];
    return { id: m.id, role: m.role, text: m.content, ...(blocks.length ? { blocks } : {}) };
  });
}

// ---- how steps and results are shown ---------------------------------------------

const LABELS: Record<string, [string, string]> = {
  read_contract: ["Reading the contract", "Read the contract"],
  find_in_contract: ["Searching the contract", "Searched the contract"],
  list_contract_versions: ["Checking the versions", "Checked the versions"],
  compare_contract_versions: ["Comparing the versions", "Compared the versions"],
  get_contract_status: ["Checking the stage and what is left", "Checked the stage and what is left"],
  list_contract_suggestions: ["Checking the open suggestions", "Checked the open suggestions"],
  edit_contract: ["Drafting suggested changes", "Drafted suggested changes"],
  redraft_contract: ["Redrafting the contract", "Redrafted the contract"],
  redline_against_playbook: ["Redlining against the playbook", "Redlined against the playbook"],
  run_playbook_review: ["Reviewing against the playbook", "Reviewed against the playbook"],
  advance_contract_stage: ["Moving the contract", "Moved the contract"],
  add_contract_comment: ["Adding a comment", "Added a comment"],
  ask_contract_brain: ["Looking through the contract", "Looked through the contract"],
};

export function toolLabel(name: string, status: ToolStatus): string {
  const known = LABELS[name];
  const plain = name.replace(/_/g, " ");
  if (status === "error") return `${known ? known[0] : plain} didn't work`;
  if (status === "refused") return `${known ? known[0] : plain.charAt(0).toUpperCase() + plain.slice(1)} — not done`;
  if (known) return known[status === "done" ? 1 : 0];
  const sentence = plain.charAt(0).toUpperCase() + plain.slice(1);
  return status === "done" ? sentence : `${sentence}…`;
}

const SUGGESTION_TOOLS = new Set(["edit_contract", "redraft_contract", "redline_against_playbook", "list_contract_suggestions"]);
const STAGE_TOOLS = new Set(["advance_contract_stage", "get_contract_status"]);

export type ChatShortcut = "review" | "lifecycle";

/** Where a person might want to go next, from the tools that ran. */
export function shortcutsFor(toolNames: string[]): ChatShortcut[] {
  const out: ChatShortcut[] = [];
  if (toolNames.some((n) => SUGGESTION_TOOLS.has(n))) out.push("review");
  if (toolNames.some((n) => STAGE_TOOLS.has(n))) out.push("lifecycle");
  return out;
}

export function toolNamesOf(item: ChatItem): string[] {
  const ran = (status: ToolStatus) => status !== "error" && status !== "refused";
  if (item.tool) return ran(item.tool.status) ? [item.tool.name] : [];
  return (item.blocks ?? []).filter((b): b is ToolBlock => b.type === "tool" && ran(b.status)).map((b) => b.name);
}

/** What the confirmation asks, in words. The server's details say which contract and what happens. */
export function confirmCopy(toolName: string): { title: string; ok: string } {
  if (toolName === "advance_contract_stage") return { title: "Move this contract to another stage?", ok: "Confirm move" };
  const plain = toolName.replace(/_/g, " ");
  return { title: `Confirm: ${plain}?`, ok: "Confirm" };
}

export const STARTERS = [
  "Summarise the key risks",
  "What's still open before approval?",
  "Tighten the payment terms",
  "Send this for approval",
];

/** Questions worth one click at the stage the contract is at. "Send this for approval" and "What's still
 *  open before approval?" only make sense before approval; offering them on a contract that is already
 *  approved (or signed) invites a change that would start the contract over. */
export function startersFor(stage: string | null | undefined): string[] {
  switch (stage) {
    case "approval":
    case "signature":
      return ["Summarise the key risks", "What are the key dates and obligations?", "What are the payment terms?"];
    case "active":
    case "closed":
      return ["Summarise the key risks", "What are the key dates and obligations?", "How and when can this be ended?"];
    default:
      return STARTERS;
  }
}

export interface Turn {
  key: string;
  user: ChatItem | null;
  /** Everything Aegis did in answer: text, steps, notes. */
  replies: ChatItem[];
}

/** The conversation as question-and-answer turns, so shortcuts can sit under a whole answer. */
export function groupTurns(items: ChatItem[]): Turn[] {
  const turns: Turn[] = [];
  for (const item of items) {
    if (item.role === "user") {
      turns.push({ key: item.id, user: item, replies: [] });
      continue;
    }
    let last = turns[turns.length - 1];
    if (!last) {
      last = { key: item.id, user: null, replies: [] };
      turns.push(last);
    }
    last.replies.push(item);
  }
  return turns;
}
