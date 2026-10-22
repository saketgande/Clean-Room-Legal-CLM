"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Field, MessageBar, Textarea } from "@/components/ui";
import { trademarksApi } from "@/lib/endpoints";
import { fmtDateTime, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Trademark, TrademarkDecision } from "@/lib/types";

const OPTIONS: { value: TrademarkDecision; label: string; hint: string }[] = [
  { value: "proceed", label: "Proceed", hint: "Clear to file" },
  { value: "conditional", label: "Conditional", hint: "File subject to the conditions below" },
  { value: "reject", label: "Reject", hint: "Do not file" },
];

const TONE = { proceed: "green", conditional: "amber", reject: "red" } as const;

// Selected look per option, from the same semantic tokens the badges use.
const SELECTED = {
  proceed: "border-success bg-success-subtle text-success",
  conditional: "border-warning bg-warning-subtle text-warning",
  reject: "border-danger bg-danger-subtle text-danger",
} as const;

/** Records the clearance decision. The server only accepts it from whoever the workflow's current step is waiting on. */
export function DecisionPanel({ trademark }: { trademark: Trademark }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [decision, setDecision] = useState<TrademarkDecision | "">(trademark.decision ?? "");
  const [conditions, setConditions] = useState(trademark.decision_conditions ?? "");
  const [escalate, setEscalate] = useState(false);
  const [busy, setBusy] = useState(false);
  const trademarkCase = trademark.case;

  if (!trademarkCase) {
    return <MessageBar intent="info">This trademark has no clearance case, so there is no decision to record.</MessageBar>;
  }

  async function save() {
    if (!decision) return;
    setBusy(true);
    try {
      await trademarksApi.recordDecision(trademark.id, {
        decision,
        conditions: conditions.trim() || null,
        escalate,
      });
      qc.invalidateQueries({ queryKey: ["trademark", trademark.id] });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      notify("Decision recorded", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not record the decision", "error");
    } finally {
      setBusy(false);
    }
  }

  const needsConditions = decision === "conditional" && conditions.trim().length === 0;
  const selected = OPTIONS.find((option) => option.value === decision);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Current decision</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2 text-[13px]">
          {trademark.decision ? (
            <>
              <Badge tone={TONE[trademark.decision]}>{titleCase(trademark.decision)}</Badge>
              {trademark.decision_conditions && (
                <p className="whitespace-pre-wrap text-slate-700">{trademark.decision_conditions}</p>
              )}
              <p className="text-xs text-slate-500">Recorded {fmtDateTime(trademark.decided_at)}</p>
            </>
          ) : (
            <p className="text-slate-500">No decision recorded yet.</p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Record a decision</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4 text-[13px]">
          <MessageBar intent="info">
            Workflow step: {trademarkCase.step_name ?? "—"}
            {trademarkCase.assignee_name && ` · with ${trademarkCase.assignee_name}`}. Only the person this step is
            assigned to (or an administrator) can record the decision; then complete the step in Progress.
          </MessageBar>
          <fieldset className="space-y-2">
            <legend className="text-[13px] font-medium text-slate-700">Decision</legend>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {OPTIONS.map((option) => (
                <label
                  key={option.value}
                  className={`flex cursor-pointer items-center justify-center rounded-lg border px-3 py-2 text-center font-medium transition-colors focus-within:ring-2 focus-within:ring-brand-500/35 ${
                    decision === option.value
                      ? SELECTED[option.value]
                      : "border-slate-300 bg-slate-100 text-slate-700 hover:border-slate-400 hover:bg-slate-50"
                  }`}
                >
                  <input
                    type="radio"
                    name="clearance-decision"
                    className="sr-only"
                    checked={decision === option.value}
                    onChange={() => setDecision(option.value)}
                  />
                  {option.label}
                </label>
              ))}
            </div>
            {selected && <p className="text-xs text-slate-500">{selected.hint}</p>}
          </fieldset>
          <Field label="Conditions" hint={decision === "conditional" ? "Required for a conditional decision." : undefined}>
            <Textarea rows={3} value={conditions} onChange={(e) => setConditions(e.target.value)} />
          </Field>
          <label className="flex cursor-pointer items-center gap-2 text-slate-700">
            <input
              type="checkbox"
              className="accent-brand-600"
              checked={escalate}
              onChange={(e) => setEscalate(e.target.checked)}
            />
            Escalate to the TM Head / GC after this decision
          </label>
          <Button loading={busy} disabled={!decision || needsConditions} onClick={save}>
            Save decision
          </Button>
        </CardBody>
      </Card>
    </div>
  );
}
