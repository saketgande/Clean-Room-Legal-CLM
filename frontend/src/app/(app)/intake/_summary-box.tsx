"use client";

/**
 * "Your request in words" (spec 013): the live box in the form's aside, the editable box on the review step, and
 * the list of extra instructions. Presentational only: the wizard owns the state and calls the pure functions in
 * lib/summary-edits.ts. Nothing here changes an answer; an offer's button hands the click back to the wizard.
 */

import { Info } from "lucide-react";
import { Badge, Button, MessageBar, Textarea } from "@/components/ui";
import { differsNote, WORD_LIMIT, WORD_WARN } from "@/lib/summary-edits";
import type { SummaryLine } from "@/lib/request-summary";
import type { ExtraInstruction, SummaryBoxState } from "@/lib/types";

export { differsNote };

/** The id of the box, so the wizard can return focus to it after an offer is answered. */
export const SUMMARY_BOX_ID = "riw-box";

/** The box as generated: one sentence per line; the read-only note and the gaps are listed under it instead. */
export const editorText = (lines: SummaryLine[]): string => lines.filter((l) => l.editable).map((l) => l.text).join("\n");

/** The box after a resumed draft: generated sentences with any kept edits in place of their line. */
export const textWithEdits = (lines: SummaryLine[], box: SummaryBoxState): string =>
  lines.filter((l) => l.editable).map((l) => box.edits[l.id]?.text ?? l.text).filter((t) => t.trim()).join("\n");

const CARD = "rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card";

export function LiveSummary({ lines, instructions }: { lines: SummaryLine[] | null; instructions: number }) {
  return (
    <section id="request-in-words" aria-labelledby="riw-h" className={CARD}>
      <h3 id="riw-h" className="mb-3 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-slate-500">
        Your request in words
      </h3>
      {lines === null ? (
        <p className="text-[13px] text-slate-600">The summary could not be built. You can still file.</p>
      ) : (
        <ul className="space-y-1.5">
          {lines.map((l) => (
            <li key={l.id} className={l.kind === "sentence" ? "text-[13px] text-slate-800"
              : l.required ? "text-[13px] text-danger" : "text-[13px] italic text-slate-500"}>
              {l.text}
              {l.kind === "gap" && (l.required ? " (required)" : " (optional)")}
            </li>
          ))}
        </ul>
      )}
      {instructions > 0 && (
        <p className="mt-2 text-[12px] text-slate-500">
          {instructions} extra instruction{instructions === 1 ? "" : "s"} for Legal — see the Review step.
        </p>
      )}
      <p className="mt-3 flex gap-1.5 text-[11.5px] text-slate-500">
        <Info className="mt-px h-3.5 w-3.5 shrink-0" />Built from your answers. Updates as you answer.
      </p>
    </section>
  );
}

export function SummaryEditor({ lines, box, text, announce, onText, onCheck, onAccept, onDecline, onUndo }: {
  lines: SummaryLine[];
  box: SummaryBoxState;
  /** What the box holds now: what they typed, or the sentences when nothing was typed. */
  text: string;
  announce: string;
  onText: (text: string) => void;
  onCheck: () => void;
  onAccept: (offerId: string) => void;
  onDecline: (offerId: string) => void;
  onUndo: () => void;
}) {
  const dirty = Object.keys(box.edits).length > 0 || box.offers.length > 0 || text !== editorText(lines);
  const aside = lines.filter((l) => !l.editable);
  return (
    <div className="space-y-3 rounded-lg border border-slate-200 p-4">
      <label className="block space-y-1.5">
        <span className="block text-[12.5px] font-semibold text-slate-800">Your request in words</span>
        <Textarea id={SUMMARY_BOX_ID} rows={6} className="max-h-64 resize-y overflow-y-auto [field-sizing:content]"
          value={text} aria-describedby={box.offers.length ? "riw-offers" : undefined}
          onChange={(e) => onText(e.target.value)} onBlur={onCheck} />
      </label>
      <p className="text-[11.5px] text-slate-500">
        Aegis offers to update an answer when your words clearly change it. Nothing changes until you click.
      </p>
      {aside.length > 0 && (
        <ul className="space-y-1">
          {aside.map((l) => (
            <li key={l.id} className={l.kind === "gap" ? (l.required ? "text-[13px] text-danger" : "text-[13px] italic text-slate-500")
              : "text-[13px] text-slate-700"}>
              {l.text}
              {l.kind === "gap" && (l.required ? " (required)" : " (optional)")}
              {l.id === "note_approvers" && <span className="block text-[11.5px] text-slate-500">Edit it in Note to approvers below.</span>}
            </li>
          ))}
        </ul>
      )}
      {box.offers.length > 0 && (
        <div id="riw-offers" className="space-y-1.5">
          {box.offers.map((o) => (
            <div key={o.id} role="group" aria-label={o.kind === "clear" ? "Clear the answer?" : "Update the answer?"}
              className="flex flex-wrap items-center gap-2 rounded-md border border-info/40 bg-info-subtle p-2.5 text-[12.5px] text-slate-800">
              <span className="mr-auto">{o.label}: {o.oldText} → {o.newText}</span>
              <Button size="sm" onClick={() => onAccept(o.id)}>{o.kind === "clear" ? "Clear answer" : "Update answer"}</Button>
              <Button size="sm" variant="ghost" onClick={() => onDecline(o.id)}>Keep my answer</Button>
            </div>
          ))}
        </div>
      )}
      <div aria-live="polite" role="status" className="min-h-[1.25rem] text-[12px] text-slate-600">{announce}</div>
      <Button variant="ghost" size="sm" disabled={!dirty} onClick={onUndo}>Undo my edits</Button>
    </div>
  );
}

/** Said when the form has no file question to point to. */
export const NO_ATTACH = "Shorten them — this form has no place to attach a file.";

export function ExtraInstructions({ items, pending, words, attachHint, onPending, onAdd, onRemove }: {
  items: ExtraInstruction[];
  pending: string;
  words: number;
  attachHint: string | null;
  onPending: (text: string) => void;
  onAdd: () => void;
  onRemove: (index: number) => void;
}) {
  return (
    <section aria-labelledby="xi-h" className="space-y-2.5">
      <h4 id="xi-h" className="flex items-center gap-2 text-[12.5px] font-semibold text-slate-800">
        Extra instructions for Legal {items.length > 0 && <Badge tone="slate">{items.length}</Badge>}
      </h4>
      {items.length === 0 ? (
        <p className="text-[12px] text-slate-500">None yet. Edits you don&apos;t apply to an answer land here.</p>
      ) : (
        <ol aria-label="Extra instructions for Legal" className="space-y-2">
          {items.map((x, i) => {
            const note = differsNote(x.differs);
            return (
              <li key={i} className="flex items-start gap-2 rounded-md border border-slate-200 p-2.5">
                <div className="min-w-0 flex-1 text-[13px] text-slate-800">
                  <span className="whitespace-pre-line">{x.text}</span>
                  {note && <span className="mt-0.5 block text-[12px] text-warning">{note}</span>}
                </div>
                <Button size="sm" variant="ghost" aria-label={`Remove instruction ${i + 1}`} onClick={() => onRemove(i)}>Remove</Button>
              </li>
            );
          })}
        </ol>
      )}
      <Textarea rows={2} aria-label="Anything else for Legal?" value={pending} onChange={(e) => onPending(e.target.value)}
        placeholder="Anything the form has no question for, e.g. send a copy to procurement" />
      <div className="flex items-center gap-3">
        <Button size="sm" variant="outline" disabled={!pending.trim()} onClick={onAdd}>Add</Button>
        <span className="text-[11.5px] text-slate-500">{words} of about 2,000 words</span>
      </div>
      {words > WORD_LIMIT ? (
        <MessageBar intent="error">Over the limit of about 2,000 words. {attachHint ? `Shorten them, or ${attachHint}.` : NO_ATTACH}</MessageBar>
      ) : words >= WORD_WARN ? (
        <MessageBar intent="warning">About {WORD_LIMIT - words} words left before the limit.</MessageBar>
      ) : null}
    </section>
  );
}
