"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileWarning } from "lucide-react";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, Field, Input, MessageBar, Select, Textarea } from "@/components/ui";
import { trademarksApi } from "@/lib/endpoints";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Trademark, TrademarkStatus } from "@/lib/types";

// The stages an LTE can move a filed mark to (the server holds the same list).
const STAGES: TrademarkStatus[] = ["filed", "examination", "published", "opposition_period", "registered", "refused", "withdrawn"];

const isoDate = (value: string) => new Date(value).toISOString();

/**
 * Post-filing tracking, on the Filing tab: the LTE / Associates log what the office did and where the mark
 * stands now, and open the response workflow when an examination report or an opposition arrives. The LTE
 * Lead, TM Legal SPOC and the Business are told (the TM Head too for a response); everything is in the audit log.
 */
export function PostFilingCard({ trademark }: { trademark: Trademark }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const [note, setNote] = useState("");
  const [stage, setStage] = useState<TrademarkStatus | "">("");
  const [nextOn, setNextOn] = useState("");
  const [nextNote, setNextNote] = useState("");
  const [clearNext, setClearNext] = useState(false);

  const [responding, setResponding] = useState(false);
  const [kind, setKind] = useState<"examination" | "opposition">("examination");
  const [summary, setSummary] = useState("");
  const [reference, setReference] = useState("");
  const [dueOn, setDueOn] = useState("");

  function refresh() {
    qc.invalidateQueries({ queryKey: ["trademark", trademark.id] });
    qc.invalidateQueries({ queryKey: ["trademarks"] });
  }

  async function run(action: () => Promise<unknown>, success: string, failure: string) {
    setBusy(true);
    try {
      await action();
      refresh();
      notify(success, "success");
      return true;
    } catch (e) {
      notify(e instanceof Error ? e.message : failure, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function saveUpdate() {
    const ok = await run(
      () =>
        trademarksApi.postFilingUpdate(trademark.id, {
          note: note.trim(),
          ...(stage ? { stage } : {}),
          ...(clearNext ? { next_action_on: null } : nextOn ? { next_action_on: isoDate(nextOn), next_action_note: nextNote.trim() || null } : {}),
        }),
      "Update logged",
      "Could not log the update",
    );
    if (ok) {
      setNote("");
      setStage("");
      setNextOn("");
      setNextNote("");
      setClearNext(false);
    }
  }

  async function openResponse() {
    const ok = await run(
      () =>
        trademarksApi.openResponse(trademark.id, {
          kind,
          summary: summary.trim(),
          reference: reference.trim() || null,
          due_on: dueOn ? isoDate(dueOn) : null,
        }),
      "Response workflow opened",
      "Could not open the response",
    );
    if (ok) {
      setResponding(false);
      setSummary("");
      setReference("");
      setDueOn("");
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Post-filing tracking</CardTitle>
        <Badge tone={statusTone(trademark.status)}>{titleCase(trademark.status)}</Badge>
      </CardHeader>
      <CardBody className="space-y-4 text-[13px]">
        <MessageBar intent="info">
          The LTE team logs what the office did and where the mark stands. The LTE Lead, TM Legal SPOC and the Business
          are told. Log an examination report or an opposition to open its response workflow.
        </MessageBar>

        {trademark.next_action_on && (
          <p className="text-slate-700">
            <span className="font-medium">Next: </span>
            {trademark.next_action_note ?? "Due"} · {fmtDate(trademark.next_action_on)}
          </p>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Update" className="sm:col-span-2">
            <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What happened, or what you know now" />
          </Field>
          <Field label="Stage" hint="Leave as is to keep the current stage">
            <Select value={stage} onChange={(e) => setStage(e.target.value as TrademarkStatus | "")}>
              <option value="">Keep current ({titleCase(trademark.status)})</option>
              {STAGES.map((s) => (
                <option key={s} value={s}>
                  {titleCase(s)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Next date">
            <Input type="date" value={nextOn} disabled={clearNext} onChange={(e) => setNextOn(e.target.value)} />
          </Field>
          <Field label="What is due" className="sm:col-span-2">
            <Input value={nextNote} disabled={clearNext || !nextOn} onChange={(e) => setNextNote(e.target.value)} placeholder="e.g. Hearing, reply, end of opposition period" />
          </Field>
        </div>
        {trademark.next_action_on && (
          <label className="flex cursor-pointer items-center gap-2 text-slate-700">
            <input type="checkbox" className="accent-brand-600" checked={clearNext} onChange={(e) => setClearNext(e.target.checked)} />
            Clear the current next date
          </label>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button loading={busy} disabled={!note.trim()} onClick={saveUpdate}>
            Log update
          </Button>
          {!responding && (
            <Button variant="outline" onClick={() => setResponding(true)}>
              <FileWarning className="h-4 w-4" />
              Log examination report / opposition
            </Button>
          )}
        </div>

        {responding && (
          <div className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-4">
            <p className="text-[13px] font-semibold text-slate-900">Examination report or opposition</p>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <Field label="Type">
                <Select value={kind} onChange={(e) => setKind(e.target.value as "examination" | "opposition")}>
                  <option value="examination">Examination report</option>
                  <option value="opposition">Opposition</option>
                </Select>
              </Field>
              <Field label="Office reference">
                <Input value={reference} onChange={(e) => setReference(e.target.value)} />
              </Field>
              <Field label="Summary" className="sm:col-span-2">
                <Textarea rows={2} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="What the office or the opponent says" />
              </Field>
              <Field label="Response due">
                <Input type="date" value={dueOn} onChange={(e) => setDueOn(e.target.value)} />
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button loading={busy} disabled={!summary.trim()} onClick={openResponse}>
                Open response workflow
              </Button>
              <Button variant="ghost" onClick={() => setResponding(false)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
