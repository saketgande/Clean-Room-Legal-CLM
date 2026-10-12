import { describe, expect, it } from "vitest";
import {
  applyEvent,
  confirmCopy,
  confirmationDetails,
  groupTurns,
  initialChatState,
  itemsFromMessages,
  shortcutsFor,
  startersFor,
  STARTERS,
  toolLabel,
  toolNamesOf,
  type ChatItem,
  type ChatState,
} from "./contract-chat";

let n = 0;
const id = () => `id-${++n}`;
const run = (events: [string, Record<string, unknown>][], from: ChatState = initialChatState) =>
  events.reduce((s, [e, d]) => applyEvent(s, e, d, id), from);

describe("applyEvent", () => {
  it("remembers which answer is being watched", () => {
    expect(run([["session_started", { assistant_run_id: "run-1" }]]).runId).toBe("run-1");
    expect(run([["session_started", {}]]).runId).toBeNull();
  });

  it("builds one answer out of its pieces", () => {
    const s = run([["message_delta", { text: "Two " }], ["message_delta", { text: "clauses." }]]);
    expect(s.items).toHaveLength(1);
    expect(s.items[0]).toMatchObject({ role: "assistant", text: "Two clauses." });
  });

  it("starts a new piece of text after a tool step", () => {
    const s = run([
      ["message_delta", { text: "Let me check. " }],
      ["tool_started", { tool_name: "get_contract_status", tool_use_id: "t1" }],
      ["tool_finished", { tool_use_id: "t1" }],
      ["message_delta", { text: "It is in Review." }],
    ]);
    expect(s.items.map((i) => [i.role, i.text])).toEqual([
      ["assistant", "Let me check. "],
      ["tool", "get_contract_status"],
      ["assistant", "It is in Review."],
    ]);
  });

  it("does not glue text onto a saved answer that has steps", () => {
    const saved: ChatState = { ...initialChatState, items: [{ id: "m1", role: "assistant", text: "Done.", blocks: [{ type: "content", text: "Done." }] }] };
    expect(run([["message_delta", { text: "More" }]], saved).items).toHaveLength(2);
  });

  it("shows a tool step running, then finished, matched by its id", () => {
    let s = run([
      ["tool_started", { tool_name: "read_contract", tool_use_id: "a" }],
      ["tool_started", { tool_name: "find_in_contract", tool_use_id: "b" }],
    ]);
    s = run([["tool_finished", { tool_use_id: "b" }]], s);
    expect(s.items.map((i) => i.tool?.status)).toEqual(["running", "done"]);
    expect(s.toolRan).toBe(true);
  });

  it("marks a step that failed", () => {
    const s = run([["tool_started", { tool_name: "edit_contract", tool_use_id: "a" }], ["tool_finished", { tool_use_id: "a", error: "No exact match" }]]);
    expect(s.items[0].tool?.status).toBe("error");
  });

  it("ignores a step announced twice (a reconnect replays events)", () => {
    const s = run([
      ["tool_started", { tool_name: "read_contract", tool_use_id: "a" }],
      ["tool_started", { tool_name: "read_contract", tool_use_id: "a" }],
    ]);
    expect(s.items).toHaveLength(1);
  });

  it("knows the page may be out of date once a tracked change is written", () => {
    expect(run([["tracked_change_created", { contract_id: "c-1" }]]).toolRan).toBe(true);
    expect(initialChatState.toolRan).toBe(false);
  });

  it("pauses for a confirmation and keeps only readable details", () => {
    const s = run([
      ["confirmation_required", {
        confirmation_id: "conf-1", assistant_run_id: "run-1", tool_name: "advance_contract_stage",
        details: { Contract: "Acme MSA", Move: "Review to Approval", skip: 3, blank: "" },
      }],
    ]);
    expect(s.pending).toEqual({
      confirmationId: "conf-1", assistantRunId: "run-1", toolName: "advance_contract_stage",
      details: { Contract: "Acme MSA", Move: "Review to Approval" },
    });
  });

  it("keeps an error and the end of the answer", () => {
    expect(run([["error", { message: "Rate limited" }]]).error).toBe("Rate limited");
    expect(run([["error", {}]]).error).toMatch(/couldn't finish/);
    expect(run([["done", {}]]).ended).toBe(true);
  });

  it("ignores events it does not show", () => {
    const s = run([["playbooks_offered", { playbooks: [] }], ["citation", {}], ["something_new", {}]]);
    expect(s).toEqual(initialChatState);
  });

  it("never changes the state it was given", () => {
    const before = Object.freeze({ ...initialChatState, items: Object.freeze([]) as unknown as ChatItem[] });
    expect(() => applyEvent(before, "message_delta", { text: "x" }, id)).not.toThrow();
    expect(before.items).toHaveLength(0);
  });
});

describe("confirmationDetails", () => {
  it("keeps non-empty text and drops everything else", () => {
    expect(confirmationDetails({ a: "x", b: 1, c: "", d: null })).toEqual({ a: "x" });
    expect(confirmationDetails(null)).toEqual({});
    expect(confirmationDetails("nope")).toEqual({});
  });
});

describe("itemsFromMessages", () => {
  const msg = (over: Record<string, unknown>) => ({ id: "m", role: "assistant" as const, content: "Hi", metadata_json: {}, ...over });

  it("turns saved messages into items", () => {
    expect(itemsFromMessages([msg({ id: "1", role: "user", content: "Hello" }), msg({ id: "2" })])).toEqual([
      { id: "1", role: "user", text: "Hello" },
      { id: "2", role: "assistant", text: "Hi" },
    ]);
  });

  it("keeps an answer's steps in order", () => {
    const blocks = [{ type: "tool", name: "read_contract", status: "done" }, { type: "content", text: "Hi" }];
    expect(itemsFromMessages([msg({ metadata_json: { blocks } })])[0].blocks).toEqual(blocks);
  });

  it("drops blocks it does not understand and does not give a question any", () => {
    const odd = { metadata_json: { blocks: [{ type: "weird" }, null, { type: "content", text: "ok" }] } };
    expect(itemsFromMessages([msg(odd)])[0].blocks).toEqual([{ type: "content", text: "ok" }]);
    expect(itemsFromMessages([msg({ role: "user", ...odd })])[0].blocks).toBeUndefined();
    expect(itemsFromMessages([msg({ metadata_json: { blocks: "nope" } })])[0].blocks).toBeUndefined();
  });
});

describe("how steps are shown", () => {
  it("words a known step as it runs and when done", () => {
    expect(toolLabel("edit_contract", "running")).toBe("Drafting suggested changes");
    expect(toolLabel("edit_contract", "done")).toBe("Drafted suggested changes");
    expect(toolLabel("advance_contract_stage", "done")).toBe("Moved the contract");
    expect(toolLabel("compare_contract_versions", "running")).toBe("Comparing the versions");
    expect(toolLabel("compare_contract_versions", "done")).toBe("Compared the versions");
    expect(toolLabel("list_contract_versions", "done")).toBe("Checked the versions");
  });
  it("says so when a step did not work", () => {
    expect(toolLabel("edit_contract", "error")).toBe("Drafting suggested changes didn't work");
  });
  it("says a step the rules refused was not done, rather than that it broke", () => {
    expect(toolLabel("advance_contract_stage", "refused")).toBe("Moving the contract — not done");
    expect(toolLabel("list_renewals", "refused")).toBe("List renewals — not done");
  });
  it("makes readable words of a tool it has no wording for", () => {
    expect(toolLabel("list_renewals", "running")).toBe("List renewals…");
    expect(toolLabel("list_renewals", "done")).toBe("List renewals");
  });

  it("offers the Review tab after suggestions and the Lifecycle tab after the stage", () => {
    expect(shortcutsFor(["edit_contract"])).toEqual(["review"]);
    expect(shortcutsFor(["list_contract_suggestions", "get_contract_status"])).toEqual(["review", "lifecycle"]);
    expect(shortcutsFor(["advance_contract_stage"])).toEqual(["lifecycle"]);
    expect(shortcutsFor(["read_contract"])).toEqual([]);
  });

  it("reads tool names from a live step or a saved answer, skipping failed steps", () => {
    const live: ChatItem = { id: "1", role: "tool", text: "x", tool: { name: "edit_contract", status: "done" } };
    expect(toolNamesOf(live)).toEqual(["edit_contract"]);
    expect(toolNamesOf({ ...live, tool: { name: "edit_contract", status: "error" } })).toEqual([]);
    const saved: ChatItem = {
      id: "2", role: "assistant", text: "",
      blocks: [{ type: "tool", name: "edit_contract", status: "done" }, { type: "tool", name: "advance_contract_stage", status: "error" }, { type: "content", text: "x" }],
    };
    expect(toolNamesOf(saved)).toEqual(["edit_contract"]);
  });

  it("asks about a stage move in its own words, and about anything else generically", () => {
    expect(confirmCopy("advance_contract_stage")).toEqual({ title: "Move this contract to another stage?", ok: "Confirm move" });
    expect(confirmCopy("add_contract_comment")).toEqual({ title: "Confirm: add contract comment?", ok: "Confirm" });
  });
});

describe("groupTurns", () => {
  const item = (id: string, role: ChatItem["role"]): ChatItem => ({ id, role, text: id });

  it("puts each answer under its question", () => {
    const turns = groupTurns([item("q1", "user"), item("a1", "assistant"), item("t1", "tool"), item("q2", "user"), item("a2", "assistant")]);
    expect(turns.map((t) => [t.user?.id, t.replies.map((r) => r.id)])).toEqual([
      ["q1", ["a1", "t1"]],
      ["q2", ["a2"]],
    ]);
  });

  it("keeps a reply that has no question before it", () => {
    const turns = groupTurns([item("a0", "assistant"), item("q1", "user")]);
    expect(turns.map((t) => [t.user?.id ?? null, t.replies.map((r) => r.id)])).toEqual([
      [null, ["a0"]],
      ["q1", []],
    ]);
  });

  it("has no turns for no messages", () => {
    expect(groupTurns([])).toEqual([]);
  });
});

describe("a step the rules refused", () => {
  const started: [string, Record<string, unknown>] = ["tool_started", { tool_name: "advance_contract_stage", tool_use_id: "t1" }];

  it("is marked refused, not failed, when the server says a rule said no", () => {
    const s = run([started, ["tool_finished", { tool_use_id: "t1", error: "“MSA” is already in Signature.", refused: true }]]);
    expect(s.items[0].tool?.status).toBe("refused");
  });

  it("is still marked failed when the tool itself broke", () => {
    const s = run([started, ["tool_finished", { tool_use_id: "t1", error: "Tool execution failed", refused: false }]]);
    expect(s.items[0].tool?.status).toBe("error");
    expect(run([started, ["tool_finished", { tool_use_id: "t1", error: "Tool execution failed" }]]).items[0].tool?.status).toBe("error");
  });

  it("does not count as something having changed on the contract", () => {
    const refused: ChatItem = { id: "1", role: "tool", text: "x", tool: { name: "advance_contract_stage", status: "refused" } };
    expect(toolNamesOf(refused)).toEqual([]);
    const saved: ChatItem = {
      id: "2", role: "assistant", text: "",
      blocks: [{ type: "tool", name: "advance_contract_stage", status: "refused" }, { type: "tool", name: "read_contract", status: "done" }],
    };
    expect(toolNamesOf(saved)).toEqual(["read_contract"]);
  });
});


describe("startersFor", () => {
  it("offers the before-approval questions while the contract is being drafted and reviewed", () => {
    for (const stage of ["intake", "drafting", "review", undefined, null, "something-new"]) expect(startersFor(stage)).toEqual(STARTERS);
  });

  it("does not offer to send for approval, or to change terms, once the contract is past review", () => {
    for (const stage of ["approval", "signature", "active", "closed"]) {
      const starters = startersFor(stage);
      expect(starters.length).toBeGreaterThan(0);
      expect(starters.join(" ")).not.toMatch(/send this for approval|tighten|before approval/i);
    }
  });
});
