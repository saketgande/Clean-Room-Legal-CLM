import { describe, expect, it } from "vitest";
import {
  clearHandoff, composeChatMessage, composeRequestDescription, deriveSubject, readHandoff, stashHandoff,
  validateLegalQuestion, type LegalQuestionInput,
} from "./legal-question";

class MemoryStorage implements Storage {
  private m = new Map<string, string>();
  get length() { return this.m.size; }
  clear() { this.m.clear(); }
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  key(i: number) { return [...this.m.keys()][i] ?? null; }
  removeItem(k: string) { this.m.delete(k); }
  setItem(k: string, v: string) { this.m.set(k, v); }
}

const base: LegalQuestionInput = {
  question: "Can we terminate the supplier agreement early for repeated late deliveries?",
  jurisdiction: "India — Telangana",
  priority: "High",
  contract: { id: "c-1", title: "Supply Agreement", counterparty: "Acme" },
};

describe("validateLegalQuestion", () => {
  it("requires a question with some detail", () => {
    expect(validateLegalQuestion({ ...base, question: "   " }).question).toBeTruthy();
    expect(validateLegalQuestion({ ...base, question: "Can we?" }).question).toMatch(/more detail/);
    expect(validateLegalQuestion(base)).toEqual({});
  });
  it("jurisdiction is optional but bounded", () => {
    expect(validateLegalQuestion({ ...base, jurisdiction: "" })).toEqual({});
    expect(validateLegalQuestion({ ...base, jurisdiction: "x".repeat(121) }).jurisdiction).toBeTruthy();
  });
});

describe("composed text", () => {
  it("chat message carries the question and every fact the lawyer needs", () => {
    const m = composeChatMessage(base);
    expect(m.startsWith(base.question)).toBe(true);
    expect(m).toContain("Jurisdiction: India — Telangana");
    expect(m).toContain("Urgency: Within 48 hours");
    expect(m).toContain("Related contract: Supply Agreement (Acme)");
  });
  it("blank jurisdiction and no contract are stated, not dropped silently", () => {
    const m = composeChatMessage({ ...base, jurisdiction: " ", contract: null });
    expect(m).toContain("Jurisdiction: Not specified");
    expect(m).not.toContain("Related contract");
  });
  it("direct filing says no AI answer was requested", () => {
    expect(composeRequestDescription(base)).toMatch(/no AI answer was requested/);
  });
  it("subject is the first line, cut at a word boundary", () => {
    expect(deriveSubject("Short one\nmore")).toBe("Short one");
    const s = deriveSubject("word ".repeat(40), 30);
    expect(s.length).toBeLessThanOrEqual(31);
    expect(s.endsWith("…")).toBe(true);
  });
});

describe("hand-off storage", () => {
  it("survives a read until the chat exists, then is cleared", () => {
    const st = new MemoryStorage();
    const key = stashHandoff(base, st);
    const h = readHandoff(key, st);
    expect(h?.message).toBe(composeChatMessage(base));
    expect(h?.contractId).toBe("c-1");
    // A failed start (or a refresh) can read it again — the question is not lost.
    expect(readHandoff(key, st)?.message).toBe(h?.message);
    clearHandoff(key, st);
    expect(readHandoff(key, st)).toBeNull();
    expect(st.length).toBe(0);
  });
  it("expires after ten minutes and removes the stale entry", () => {
    const st = new MemoryStorage();
    const key = stashHandoff(base, st);
    expect(readHandoff(key, st, Date.now() + 11 * 60 * 1000)).toBeNull();
    expect(st.length).toBe(0);
  });
  it("rejects malformed entries and unknown keys", () => {
    const st = new MemoryStorage();
    st.setItem("aegis-legal-question:bad", "{not json");
    expect(readHandoff("bad", st)).toBeNull();
    expect(st.length).toBe(0);
    expect(readHandoff("missing", st)).toBeNull();
  });
});
