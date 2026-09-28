import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { AGREEMENT_FORMS, classifyFor } from "./_agreement-forms";

type ServerField = { key: string; label: string; kind: string; required: boolean; options?: string[] };
type ServerForm = { key: string; name: string; fields: ServerField[] };

// The server validates wizard submissions against its own copy of these forms.
const server: ServerForm[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);
// Fields the wizard enforces in code rather than through a FieldSpec.
const STRUCTURAL = new Set(["entity", "entity_id", "counterparty", "counterparty_id", "parent_contract_id", "cancel_request_ref", "note_approvers"]);

describe("agreement forms match the server's copy", () => {
  it("has the same nine forms", () => {
    expect(server.map((f) => f.key)).toEqual(AGREEMENT_FORMS.map((d) => d.key));
  });

  for (const def of AGREEMENT_FORMS) {
    it(`${def.key}: same fields, labels, required flags and options`, () => {
      // If this fails, the wizard changed without backend/app/intake/agreement_forms.json:
      // the server would then accept or reject the wrong things.
      const specs = [...(def.parentFields ?? []), ...(def.partyFields ?? []), ...classifyFor(def), ...def.detail];
      const want = new Map<string, { label: string; required: boolean; options?: string[] }>();
      for (const f of specs) {
        const prev = want.get(f.k);
        if (prev) { prev.required ||= !!f.req; continue; }
        want.set(f.k, { label: f.label, required: !!f.req, options: f.options });
      }
      const form = server.find((s) => s.key === def.key)!;
      const got = form.fields.filter((f) => !STRUCTURAL.has(f.key) || want.has(f.key));
      expect(got.map((f) => f.key).sort()).toEqual([...want.keys()].sort());
      for (const f of got) {
        const w = want.get(f.key)!;
        expect([f.key, f.label, f.required]).toEqual([f.key, w.label, w.required]);
        if (f.kind === "select") expect([f.key, f.options]).toEqual([f.key, w.options]);
      }
      expect(form.fields.find((f) => f.key === "note_approvers")?.required).toBe(true);
    });
  }
});
