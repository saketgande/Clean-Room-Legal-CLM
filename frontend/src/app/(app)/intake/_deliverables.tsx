"use client";

/**
 * The deliverables table of a Statement of Work request (007).
 *
 * Exports
 *  - `RowsTable`: any table question the server declares (`kind: "rows"`). With `onChange` it is the editor
 *    (labelled cells, add and remove, a cap, errors beside the cell, the running share total, the
 *    past-date note); without it, the same rows read-only. The rules it shows come from
 *    `@/lib/form-checks`, which the server's filing validator mirrors.
 *  - `RequestDeliverables`: the block for the request page. Props:
 *      request    the request (its `field_values` hold `deliverable_rows`, and for an older request the
 *                 free-text `deliverables`)
 *      canEdit    whether the person may change the request's answers (the server still decides)
 *      heldNote   the workflow's reason for a held draft; shown as a warning above the rows
 *      heldRunId  the held run's id; after the rows are saved the run is refreshed so the draft carries on
 *      onSaved    called with the updated request once the rows are saved
 *    It shows stored rows read-only, an older request's free text as "Earlier free-text answer", and, when
 *    the request has NO rows and `canEdit`, an editor that saves with `intakeApi.update` (the whole
 *    `field_values` back, as that call replaces them). It renders nothing for a form with no table question.
 *  - `useRequestForms`: the form definitions (`GET /intake/forms`). It lives here so this file and
 *    `_agreement-forms.tsx` need not import each other; `_agreement-forms.tsx` re-exports it.
 */

import { useId, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { Button, Input, MessageBar, Table, TD, TH, THead, TR } from "@/components/ui";
import { useToast } from "@/components/toast";
import { intakeApi, workflowsApi } from "@/lib/endpoints";
import {
  dateWindow, longDate, pastDueRows, rowsProblems, shareTotal, shownColumns, tidiedRows, tidyCell, type RowsProblem,
} from "@/lib/form-checks";
import { cn, fmtDate } from "@/lib/utils";
import type { FormRow, IntakeRequest, RequestFormField } from "@/lib/types";

export function useRequestForms() {
  return useQuery({ queryKey: ["intake-forms"], queryFn: intakeApi.forms, staleTime: Infinity });
}

/** Today as an ISO day in the person's own time zone (the past-date note is for them). */
function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** A stored share reads "40.00"; people write "40". */
const plainShare = (s: string) => (s.includes(".") ? s.replace(/\.?0+$/, "") : s);

/** A cell's own message: the row is already where the person is looking, so drop the `"Deliverables", row 2:` lead. */
const cellMessage = (m: string) => m.replace(/^"[^"]*", row \d+: /, "");

export function RowsTable({ field, rows, values, labels, onChange, problems = [], today }: {
  field: RequestFormField;
  rows: FormRow[];
  /** The form's other answers: they decide which columns show and the window the due dates must fall in. */
  values: Record<string, unknown>;
  labels: Record<string, string>;
  /** Present: an editor. Absent: the rows, read-only. */
  onChange?: (rows: FormRow[]) => void;
  problems?: RowsProblem[];
  /** ISO day the past-date note compares with (tests pin it). */
  today?: string;
}) {
  const uid = useId();
  const cols = shownColumns(field, values);
  const edit = !!onChange;
  const max = field.max_rows ?? Infinity;
  const atMax = rows.length >= max;
  const totalCol = cols.find((c) => c.total !== undefined);
  const dateCol = cols.find((c) => c.kind === "date");
  const win = dateCol ? dateWindow(dateCol, values) : null;
  const hintId = `${uid}-window`;

  const change = (next: FormRow[]) => onChange?.(next);
  const cellProblem = (n: number, key: string) => problems.find((p) => p.row === n && p.column === key);
  const tableProblems = problems.filter((p) => p.row === null || p.column === null);
  const passed = edit && dateCol
    ? pastDueRows(field, rows, today ?? todayIso()).filter((n) => {
      // A date outside the window gets the window's error, not "already passed" on top of it.
      const due = tidyCell(rows[n - 1][dateCol.key]);
      return !win || (due >= win.start && due <= win.end);
    })
    : [];
  const total = edit && totalCol ? shareTotal(field, rows) : null;

  return (
    <div className="space-y-2">
      <Table className="min-w-[680px]">
        <caption className="sr-only">{field.label}</caption>
        <THead>
          <tr>
            <TH scope="col" className="w-14">{edit ? "Row" : "Ref"}</TH>
            {cols.map((c) => <TH key={c.key} scope="col">{c.label}</TH>)}
            {edit && <TH scope="col" className="w-12"><span className="sr-only">Remove row</span></TH>}
          </tr>
        </THead>
        <tbody>
          {rows.map((row, i) => {
            const n = i + 1;
            return (
              <TR key={i}>
                <TD className="align-top text-[12px] text-slate-500">{edit ? n : `D${n}`}</TD>
                {cols.map((c) => {
                  const value = row[c.key] ?? "";
                  if (!edit) {
                    return (
                      <TD key={c.key} className="align-top text-slate-900">
                        {c.kind === "date" ? fmtDate(value) : c.kind === "percent" ? (value ? `${plainShare(value)}%` : "—") : value || "—"}
                      </TD>
                    );
                  }
                  const bad = cellProblem(n, c.key);
                  const errId = `${uid}-r${n}-${c.key}-error`;
                  const describedBy = [bad && errId, c.kind === "date" && win && hintId].filter(Boolean).join(" ");
                  return (
                    <TD key={c.key} className="align-top">
                      <Input
                        aria-label={`Row ${n}, ${c.label}`}
                        aria-invalid={bad ? true : undefined}
                        aria-describedby={describedBy || undefined}
                        type={c.kind === "date" ? "date" : "text"}
                        inputMode={c.kind === "percent" ? "decimal" : undefined}
                        min={c.kind === "date" ? win?.start : undefined}
                        max={c.kind === "date" ? win?.end : undefined}
                        value={value}
                        className={cn(bad && "border-danger bg-danger-subtle")}
                        onChange={(e) => change(rows.map((r, j) => (j === i ? { ...r, [c.key]: e.target.value } : r)))}
                      />
                      {bad && <p id={errId} className="mt-1 text-[11.5px] text-danger">{cellMessage(bad.message)}</p>}
                    </TD>
                  );
                })}
                {edit && (
                  <TD className="align-top">
                    <Button type="button" variant="ghost" size="icon" aria-label={`Remove row ${n}`}
                      disabled={rows.length <= 1} onClick={() => change(rows.filter((_, j) => j !== i))}>
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </TD>
                )}
              </TR>
            );
          })}
        </tbody>
        {total !== null && totalCol && (
          <tfoot>
            <tr className="border-t border-slate-300">
              <td colSpan={1 + cols.indexOf(totalCol)} />
              <td className={cn("px-3 py-2 text-[12.5px] font-medium", total === String(totalCol.total) ? "text-success" : "text-slate-600")}>
                <span aria-live="polite">Total {total}% of {totalCol.total}%</span>
              </td>
              <td colSpan={cols.length - cols.indexOf(totalCol)} />
            </tr>
          </tfoot>
        )}
      </Table>

      {edit && win && (
        <p id={hintId} className="text-[11.5px] text-slate-500">
          Due dates must fall between {longDate(win.start)} and {longDate(win.end)} ({labels[win.startKey] ?? win.startKey} to {labels[win.endKey] ?? win.endKey}).
        </p>
      )}
      {passed.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-warning">
          {passed.map((n) => <li key={n}>Row {n}: this due date has already passed.</li>)}
        </ul>
      )}
      {tableProblems.length > 0 && (
        <ul className="space-y-0.5 text-[12px] text-danger">
          {tableProblems.map((p) => <li key={p.message}>{p.message}</li>)}
        </ul>
      )}
      {edit && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" size="sm" disabled={atMax} aria-describedby={atMax ? `${uid}-cap` : undefined}
            onClick={() => change([...rows, {}])}>
            <Plus className="h-3.5 w-3.5" />Add a deliverable
          </Button>
          {atMax && <span id={`${uid}-cap`} className="text-[12px] text-slate-500">A table holds at most {max} deliverables.</span>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------- on the request page ----

/** An older request's free-text answer, shown unchanged and labelled for what it is. */
export function EarlierAnswer({ text, label, title = "Earlier free-text answer" }: { text: string; label?: string; title?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3">
      <div className="text-[11.5px] font-semibold text-slate-700">
        <span>{title}</span>
        {label && <span className="ml-1 font-normal text-slate-500">({label})</span>}
      </div>
      <p className="mt-1 select-text whitespace-pre-line text-[13px] text-slate-800">{text}</p>
    </div>
  );
}

export function RequestDeliverables({ request, canEdit, heldNote, heldRunId, onSaved }: {
  request: IntakeRequest;
  canEdit: boolean;
  heldNote?: string | null;
  heldRunId?: string | null;
  onSaved?: (updated: IntakeRequest) => void;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const { data: forms } = useRequestForms();
  const [rows, setRows] = useState<FormRow[]>([{}]);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);

  const fv = request.field_values ?? {};
  const form = forms?.find((f) => f.key === fv.request_form);
  const field = form?.fields.find((f) => f.kind === "rows");
  if (!form || !field) return null;

  const labels = Object.fromEntries(form.fields.map((f) => [f.key, f.label]));
  const stored = tidiedRows(field, fv[field.key], fv);
  const legacy = field.legacy_key ? fv[field.legacy_key] : undefined;
  const legacyText = typeof legacy === "string" && legacy.trim() ? legacy : null;
  const editing = canEdit && stored.length === 0;
  const problems = rowsProblems(field, rows, fv, labels);

  const save = async () => {
    if (problems.length) { setTried(true); return; }
    setBusy(true);
    try {
      const updated = await intakeApi.update(request.id, { field_values: { ...fv, [field.key]: rows } });
      // The held draft step is waiting for these rows; ask it to look again now instead of at the next sweep.
      if (heldRunId) {
        await workflowsApi.refreshRun(heldRunId).catch(() => notify("Saved. The draft step carries on by itself shortly.", "info"));
        qc.invalidateQueries({ queryKey: ["flow-run", request.id] });
      }
      onSaved?.(updated);
    } catch (e) {
      // The rows stay as typed; the server's message names the row at fault.
      notify(e instanceof Error ? e.message : "Couldn't save the deliverables", "error");
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-3">
      <h4 className="text-[13px] font-semibold text-slate-900">{field.label}</h4>
      {heldNote && <MessageBar intent="warning">{heldNote}</MessageBar>}
      {stored.length > 0 && <RowsTable field={field} rows={stored} values={fv} labels={labels} />}
      {legacyText && <EarlierAnswer text={legacyText} label={field.legacy_label} />}
      {stored.length === 0 && !editing && !legacyText && (
        <p className="text-[13px] text-slate-500">No deliverables have been entered.</p>
      )}
      {editing && (
        <div className="space-y-3">
          <RowsTable field={field} rows={rows} values={fv} labels={labels} onChange={setRows} problems={tried ? problems : []} />
          {tried && problems.length > 0 && (
            <MessageBar intent="error">
              <ul className="space-y-0.5">{problems.map((p) => <li key={p.message}>{p.message}</li>)}</ul>
            </MessageBar>
          )}
          <Button type="button" loading={busy} onClick={save}>Save deliverables</Button>
        </div>
      )}
    </div>
  );
}
