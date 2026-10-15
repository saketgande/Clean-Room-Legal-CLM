"use client";

/**
 * The answers a template draft is made from, on the request page (008; the Statement of Work's block is 007's).
 *
 * `RequestAnswers` takes the props of `RequestDeliverables`:
 *   request    the request (its `field_values` hold the answers)
 *   canEdit    whether the person may change the request's answers (the server still decides)
 *   heldNote   the workflow's reason for a held draft step; shown as a warning above the answers
 *   heldRunId  the held run's id; after the answers are saved the run is refreshed so the draft carries on
 *   onSaved    called with the updated request once the answers are saved
 *
 * What it shows, by the request's form:
 *  - Statement of Work: `RequestDeliverables`, unchanged.
 *  - Consultancy and DPA: the answers the draft is made from (the questions that carry `fills`), read-only and
 *    readable: tables as tables, lists as lists, the rate with its currency, an older single choice (such as
 *    "Several") as the earlier answer it is. Where an answer is missing and the person may change the request,
 *    an editor asks for them with the wizard's own `Question`, and saves them with `intakeApi.update` (the whole
 *    `field_values` back, as that call replaces them).
 *  - A DPA that came by email or chat (no form): only the reason the draft is held.
 *  - Anything else: nothing.
 */

import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MessageBar, Button } from "@/components/ui";
import { useToast } from "@/components/toast";
import { intakeApi, workflowsApi } from "@/lib/endpoints";
import {
  answerProblem, lacksAnswer, needsDraftAnswers, rowsProblems, rulesFor, shown, tidiedRows, tidyCell,
} from "@/lib/form-checks";
import type { FormValue, IntakeRequest, RequestFormDef, RequestFormField } from "@/lib/types";
import { EarlierAnswer, RequestDeliverables, RowsTable, useRequestForms } from "./_deliverables";
import { Question } from "./_agreement-forms";

interface Props {
  request: IntakeRequest;
  canEdit: boolean;
  heldNote?: string | null;
  heldRunId?: string | null;
  onSaved?: (updated: IntakeRequest) => void;
}

type Values = Record<string, FormValue>;

const NEEDED = "Needed before you save.";

export function RequestAnswers(props: Props) {
  const { data: forms } = useRequestForms();
  const fv = props.request.field_values ?? {};
  if (fv.request_form === "sow") return <RequestDeliverables {...props} />;
  const doc = props.request.draftable_doc_type;
  if (doc !== "consultancy" && doc !== "dpa") return null;
  // Email or chat: it has none of the form's answers, so there is nothing to show but why no draft is made.
  if (!fv.request_form) return props.heldNote ? <MessageBar intent="warning">{props.heldNote}</MessageBar> : null;
  const form = forms?.find((f) => f.key === fv.request_form);
  return form ? <Answers {...props} form={form} /> : null;
}

/** The questions whose answers the template is built from. */
const fillsOf = (form: RequestFormDef) => form.fields.filter((f) => !!f.fills?.length);

/** A multi-tick question that holds an older single choice which is not one of today's options ("Several"). */
const earlierChoice = (f: RequestFormField, v: unknown): string | null =>
  f.kind === "multiselect" && typeof v === "string" && tidyCell(v) !== "" && !(f.options ?? []).includes(tidyCell(v)) ? v : null;

function Answers({ form, request, canEdit, heldNote, heldRunId, onSaved }: Props & { form: RequestFormDef }) {
  const fv = request.field_values ?? {};
  const asked = fillsOf(form).filter((f) => shown(f.show, fv));
  if (asked.length === 0 && !heldNote) return null;
  const labels = Object.fromEntries(form.fields.map((f) => [f.key, f.label]));
  // The editor is for an own-paper request the draft would be held on; the server decides who may save.
  const editing = canEdit && needsDraftAnswers(form, fv).length > 0;

  return (
    <div className="space-y-3">
      <h4 className="text-[13px] font-semibold text-slate-900">Answers for the draft</h4>
      {heldNote && <MessageBar intent="warning">{heldNote}</MessageBar>}
      {editing
        ? <AnswersEditor form={form} request={request} labels={labels} heldRunId={heldRunId} onSaved={onSaved} />
        : asked.map((f) => <Answer key={f.key} field={f} fv={fv} labels={labels} />)}
    </div>
  );
}

// ------------------------------------------------------------ read-only ----

/** "INR 25,000": the rate in the currency of the total value, as the wizard's review step writes it. */
function rateText(currency: unknown, v: unknown): string {
  const n = Number(String(v).replace(/,/g, ""));
  const c = typeof currency === "string" && currency ? currency : "INR";
  return Number.isFinite(n) ? `${c} ${n.toLocaleString(c === "INR" ? "en-IN" : "en-US", { maximumFractionDigits: 2 })}` : String(v);
}

function Answer({ field, fv, labels }: { field: RequestFormField; fv: Record<string, unknown>; labels: Record<string, string> }) {
  const v = fv[field.key];
  let body: ReactNode = null;
  if (field.kind === "rows") {
    const rows = tidiedRows(field, v, fv);
    if (rows.length > 0) body = <RowsTable field={field} rows={rows} values={fv} labels={labels} />;
  } else if (field.kind === "multiselect") {
    const picks = (Array.isArray(v) ? v : typeof v === "string" ? [v] : []).filter((x): x is string => typeof x === "string" && tidyCell(x) !== "");
    const earlier = earlierChoice(field, v);
    if (earlier) body = <EarlierAnswer text={earlier} title="Earlier answer (kept as filed)" />;
    else if (picks.length > 0) {
      body = <ul aria-label={field.label} className="list-disc space-y-0.5 pl-5 text-[13px] text-slate-900">{picks.map((p) => <li key={p}>{p}</li>)}</ul>;
    }
  } else if (typeof v === "string" ? tidyCell(v) !== "" : typeof v === "number") {
    body = <p className="select-text whitespace-pre-line text-[13px] text-slate-900">{field.key === "fee_rate" ? rateText(fv.currency, v) : String(v).trim()}</p>;
  }
  // A question that is optional and was not answered (the "advice only" tick) has nothing to say.
  if (body === null && !field.required) return null;
  return (
    <div className="space-y-1">
      <div className="text-[11.5px] text-slate-500">{field.label}</div>
      {body ?? <p className="text-[13px] text-slate-500">Not answered yet</p>}
    </div>
  );
}

// -------------------------------------------------------------- editor ----

/** What each question of the draft starts with: the stored answer, in the shape its control holds. A multi-tick
 *  question that holds a single choice is ticked with it when it is one of today's options; "Several" ticks nothing. */
function startValues(form: RequestFormDef, fv: Record<string, unknown>): Values {
  return Object.fromEntries(fillsOf(form).map((f): [string, FormValue] => {
    const v = fv[f.key];
    if (f.kind === "rows") return [f.key, tidiedRows(f, v, fv)];
    if (f.kind === "multiselect") {
      const picks = Array.isArray(v) ? v : typeof v === "string" ? [v] : [];
      return [f.key, picks.filter((x): x is string => typeof x === "string" && (f.options ?? []).includes(tidyCell(x)))];
    }
    return [f.key, typeof v === "string" || typeof v === "number" ? String(v) : ""];
  }));
}

function AnswersEditor({ form, request, labels, heldRunId, onSaved }: {
  form: RequestFormDef; request: IntakeRequest; labels: Record<string, string>;
  heldRunId?: string | null; onSaved?: (updated: IntakeRequest) => void;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const fv = request.field_values ?? {};
  const [edited, setEdited] = useState<Values>(() => startValues(form, fv));
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);

  // The stored answers with the edits over them: what decides which questions apply (ticking "advice only"
  // hides the table) and the window the due dates fall in. The question box only reads its own key and the others.
  const values = { ...fv, ...edited } as Values; // fv holds numbers too; a Question reads only strings, lists and rows
  const asked = fillsOf(form).filter((f) => shown(f.show, values));

  /** What is wrong with a question's answer, in words, or null. A table lists its own messages. */
  const problemsOf = (f: RequestFormField): string[] => {
    const v = values[f.key];
    if (f.kind === "rows") return !f.required && lacksAnswer(f, v) ? [] : rowsProblems(f, v, values, labels).map((p) => p.message);
    if (lacksAnswer(f, v)) return f.required ? [NEEDED] : [];
    const p = answerProblem(f.kind, v, undefined, rulesFor(f, values));
    return p ? [p] : [];
  };
  const all = asked.flatMap((f) => problemsOf(f).map((p) => (f.kind === "rows" ? p : `${f.label}: ${p}`)));

  const save = async () => {
    if (all.length) { setTried(true); return; }
    setBusy(true);
    try {
      // Every stored answer goes back, as the update replaces them whole. A question that no longer applies, or
      // an optional one left empty, is left out (the server drops both anyway).
      const body: Record<string, unknown> = { ...fv, ...edited };
      for (const f of fillsOf(form)) {
        // An older single choice that was ticked and left alone goes back as it was filed (FR-41), not as a list.
        const was = fv[f.key];
        const now = edited[f.key];
        if (f.kind === "multiselect" && typeof was === "string" && Array.isArray(now) && now.length === 1 && now[0] === tidyCell(was)) body[f.key] = was;
        if (!shown(f.show, body) || (!f.required && lacksAnswer(f, body[f.key]))) delete body[f.key];
      }
      const updated = await intakeApi.update(request.id, { field_values: body });
      // The held draft step is waiting for these answers; ask it to look again now instead of at the next sweep.
      if (heldRunId) {
        await workflowsApi.refreshRun(heldRunId).catch(() => notify("Saved. The draft step carries on by itself shortly.", "info"));
        qc.invalidateQueries({ queryKey: ["flow-run", request.id] });
      }
      onSaved?.(updated);
    } catch (e) {
      // The entries stay as typed; the server's message names the row or the answer at fault.
      notify(e instanceof Error ? e.message : "Couldn't save the answers", "error");
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-4">
      {asked.map((f) => (
        <Question key={f.key} k={f.key} label={f.label} req={f.required} field={f} values={values} labels={labels}
          set={(patch) => setEdited((prev) => ({ ...prev, ...patch }))}
          bad={tried && problemsOf(f).length > 0} problem={problemsOf(f)[0]}
          earlier={earlierChoice(f, fv[f.key]) ?? undefined} />
      ))}
      {tried && all.length > 0 && (
        <MessageBar intent="error">
          <ul className="space-y-0.5">{all.map((m) => <li key={m}>{m}</li>)}</ul>
        </MessageBar>
      )}
      <Button type="button" loading={busy} onClick={save}>Save answers</Button>
    </div>
  );
}
