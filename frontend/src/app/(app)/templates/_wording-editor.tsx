"use client";

// The Wording tab of the Templates page (spec 006: FR-13, FR-24, FR-34, FR-35, FR-59, FR-62).
//
// The page owns the editor text and the applied-suggestion list, so the Checks tab can act on the same text
// (Apply suggestion, Show in template). This component receives them and reports every change.
//
//   <WordingEditor
//     template                 the saved template (DraftingTemplate)
//     canChange                templatePermissions(user).canChange
//     editorText               the wording as the editor shows it, `«Plain name»` notation (toEditorText)
//     onEditorTextChange       the person typed, pasted, inserted a field or fixed a problem
//     appliedFindings          suggestions applied to editorText and not saved yet ({check_id, finding_index})
//     onAppliedFindingsChange  Discard (and a successful save, or loading theirs) report []
//     jumpTo                   {start, end} in editorText: selects and scrolls to it. A fresh object per jump.
//     onSaved(result)          a save went through; this component has already reset editorText to the saved
//                              wording, cleared appliedFindings and shown the success toast. The page updates
//                              its caches (template, list, versions, checks) from `result`.
//     onTemplateRefreshed?     after a 409 the latest saved template was fetched; the page should adopt it as
//                              the saved template WITHOUT resetting editorText (the person may keep theirs).
//   />
//
// Dirty = editorText !== toEditorText(template.body, template.placeholders).
// The save bar needs `canChange`; people without it see read-only wording (the reason line is the page's).
// A Word-based template (editable false, FR-40) renders as a read-only view with the explanation.

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { Plus } from "lucide-react";
import { Button, Input, MessageBar, Select, useConfirm } from "@/components/ui";
import { useToast } from "@/components/toast";
import { draftingTemplatesApi } from "@/lib/endpoints";
import {
  missingPartyFields,
  toBodyText,
  toEditorText,
  wordingProblems,
  type FieldLabel,
  type WordingProblem,
} from "@/lib/templates";
import type { DraftingTemplate, TemplateMutationResult, TemplateSavePayload } from "@/lib/types";
import { cn } from "@/lib/utils";

/** A suggestion applied in the editor: what a save sends as `applied_findings`. */
export type FindingRef = NonNullable<TemplateSavePayload["applied_findings"]>[number];
export type Range = { start: number; end: number };

const WORD_BASED_NOTE =
  "This template was made from a Word file, so its wording changes only by uploading a replacement: download it, edit it in Word, then upload it.";

// ---- chips: fill-in fields shown under their plain names (FR-62) --------------------------------------------
const CHIP_RE = /«([^«»]*)»/g;
type Mark = { start: number; end: number; kind: "chip" | "bad" | "hit" };

/** Wording with each known `«Plain name»` drawn as a chip, unknown `{code}` and stray braces flagged, and an
 *  optional range highlighted. `ghost` is the transparent copy behind the textarea: same characters, same
 *  metrics (no padding on a chip), so the colours line up with the text typed on top of them. */
export function ChipText({
  text,
  fields,
  hit,
  hitRef,
  ghost = false,
  flagProblems = true,
}: {
  text: string;
  fields: readonly FieldLabel[];
  hit?: Range | null;
  hitRef?: RefObject<HTMLElement | null>;
  ghost?: boolean;
  flagProblems?: boolean;
}) {
  const hitStart = hit?.start;
  const hitEnd = hit?.end;
  const pieces = useMemo(() => {
    const labels = new Set(fields.map((f) => f.label));
    const marks: Mark[] = [];
    for (const m of text.matchAll(CHIP_RE)) {
      if (labels.has(m[1])) marks.push({ start: m.index ?? 0, end: (m.index ?? 0) + m[0].length, kind: "chip" });
    }
    if (flagProblems) {
      for (const p of wordingProblems(text, fields)) {
        if (p.kind !== "empty") marks.push({ start: p.start, end: p.end, kind: "bad" });
      }
    }
    if (hitStart !== undefined && hitEnd !== undefined && hitEnd > hitStart) {
      marks.push({ start: Math.max(0, hitStart), end: Math.min(hitEnd, text.length), kind: "hit" });
    }
    const cuts = [...new Set([0, text.length, ...marks.flatMap((m) => [m.start, m.end])])]
      .filter((c) => c >= 0 && c <= text.length)
      .sort((a, b) => a - b);
    const out: { text: string; kinds: Mark["kind"][] }[] = [];
    for (let i = 0; i + 1 < cuts.length; i += 1) {
      const [a, b] = [cuts[i], cuts[i + 1]];
      out.push({ text: text.slice(a, b), kinds: marks.filter((m) => m.start <= a && m.end >= b).map((m) => m.kind) });
    }
    return out;
  }, [text, fields, hitStart, hitEnd, flagProblems]);

  let hitPlaced = false;
  return (
    <>
      {pieces.map((p, i) => {
        if (!p.kinds.length) return p.text;
        const isHit = p.kinds.includes("hit");
        const className = cn(
          p.kinds.includes("chip") &&
            (ghost
              ? "rounded-[3px] bg-brand-100 shadow-[0_0_0_1px_rgb(var(--color-brand-300))]"
              : "rounded-[3px] bg-brand-50 px-1 font-medium text-brand-700 shadow-[0_0_0_1px_rgb(var(--color-brand-200))]"),
          p.kinds.includes("bad") && "bg-danger-subtle underline decoration-danger decoration-wavy underline-offset-2",
          isHit && "rounded-[2px] bg-warning-subtle text-inherit shadow-[0_0_0_2px_rgb(var(--color-warning))]",
        );
        if (!isHit) return <span key={i} className={className}>{p.text}</span>;
        const ref = hitPlaced ? undefined : hitRef;
        hitPlaced = true;
        return (
          <mark key={i} ref={ref} className={className}>
            {p.text}
          </mark>
        );
      })}
    </>
  );
}

// The textarea and its mirror must lay text out identically, so both take exactly these classes.
const METRICS = "p-4 font-mono text-[12.5px] leading-[1.6] whitespace-pre-wrap break-words";

// ---- the editor ---------------------------------------------------------------------------------------------
export interface WordingEditorProps {
  template: DraftingTemplate;
  canChange: boolean;
  editorText: string;
  onEditorTextChange: (text: string) => void;
  appliedFindings: FindingRef[];
  onAppliedFindingsChange: (next: FindingRef[]) => void;
  jumpTo?: Range | null;
  onSaved: (result: TemplateMutationResult) => void;
  onTemplateRefreshed?: (latest: DraftingTemplate) => void;
}

export function WordingEditor(props: WordingEditorProps) {
  return props.template.editable ? <EditableWording {...props} /> : <ReadOnlyWording {...props} />;
}

function ReadOnlyWording({ template, editorText, jumpTo }: WordingEditorProps) {
  const hitRef = useRef<HTMLElement | null>(null);
  const [hit, setHit] = useState<Range | null>(null);
  useEffect(() => {
    if (jumpTo) setHit({ start: jumpTo.start, end: jumpTo.end });
  }, [jumpTo]);
  useEffect(() => {
    hitRef.current?.scrollIntoView?.({ block: "center" });
  }, [hit]);
  return (
    <div className="space-y-3 p-4">
      <MessageBar intent="info">{WORD_BASED_NOTE}</MessageBar>
      <FieldList template={template} onInsert={null} />
      <div
        role="region"
        aria-label={`${template.name} wording (read only)`}
        tabIndex={0}
        className={cn(METRICS, "max-h-[70vh] overflow-auto rounded-lg border border-slate-200 bg-slate-50 text-slate-900")}
      >
        <ChipText text={editorText} fields={template.placeholders} hit={hit} hitRef={hitRef} flagProblems={false} />
      </div>
    </div>
  );
}

function FieldList({ template, onInsert }: { template: DraftingTemplate; onInsert: ((label: string) => void) | null }) {
  return (
    <aside aria-label="Fill-in fields" className="space-y-2">
      <h3 className="text-[13px] font-semibold text-slate-900">Fill-in fields</h3>
      <p className="text-xs text-slate-500">
        Each field is filled in when a request is drafted.
        {onInsert ? " Insert puts one at the cursor." : ""} A literal brace is written {"{{"} or {"}}"}.
      </p>
      <ul className="space-y-1.5">
        {template.placeholders.map((p) => (
          <li key={p.name} className="flex items-start justify-between gap-2 rounded-lg border border-slate-200 bg-slate-100 px-2.5 py-2">
            <div className="min-w-0">
              <div className="text-[12.5px] font-semibold text-slate-900">{p.label}</div>
              <div className="text-xs text-slate-600">{p.about}</div>
              <div className="truncate text-xs text-slate-500">e.g. {p.sample}</div>
            </div>
            {onInsert && (
              <Button type="button" size="sm" variant="outline" aria-label={`Insert ${p.label}`} onClick={() => onInsert(p.label)}>
                <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                Insert
              </Button>
            )}
          </li>
        ))}
      </ul>
    </aside>
  );
}

const whoMissing = (missing: ("company" | "counterparty")[]) =>
  missing.map((p) => (p === "company" ? "our entity" : "the other party")).join(" or ");

function EditableWording({
  template,
  canChange,
  editorText,
  onEditorTextChange,
  appliedFindings,
  onAppliedFindingsChange,
  jumpTo,
  onSaved,
  onTemplateRefreshed,
}: WordingEditorProps) {
  const { notify } = useToast();
  const { confirm, dialog } = useConfirm();
  const fields = template.placeholders;
  const area = useRef<HTMLTextAreaElement>(null);
  const hitRef = useRef<HTMLElement | null>(null);

  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [hit, setHit] = useState<Range | null>(null);
  // The version the editor loaded: what a save is checked against (FR-59). Follows the template, and moves
  // on by hand when the person chooses to keep their text over a colleague's newer version.
  const [expected, setExpected] = useState(template.version);
  useEffect(() => setExpected(template.version), [template.version]);
  const [conflict, setConflict] = useState<{ message: string; theirs: DraftingTemplate } | null>(null);
  const [comparing, setComparing] = useState(false);

  const savedText = useMemo(() => toEditorText(template.body, fields), [template.body, fields]);
  const dirty = editorText !== savedText;
  const problems = useMemo(() => wordingProblems(editorText, fields), [editorText, fields]);
  const missing = useMemo(() => missingPartyFields(editorText, fields), [editorText, fields]);
  const covered = template.playbook?.available === true;
  const editable = canChange;

  // The textarea grows with its text, so the page scrolls instead of the box: the chip mirror behind it then
  // never has to follow an inner scrollbar.
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    if (el.scrollHeight > 0) el.style.height = `${el.scrollHeight}px`;
  }, [editorText]);

  // Jump-to (FR-24): select the located range, highlight it and bring it into view.
  useEffect(() => {
    if (!jumpTo) return;
    setHit({ start: jumpTo.start, end: jumpTo.end });
    const el = area.current;
    if (el) {
      el.focus({ preventScroll: true });
      el.setSelectionRange(jumpTo.start, jumpTo.end);
    }
  }, [jumpTo]);
  useEffect(() => {
    hitRef.current?.scrollIntoView?.({ block: "center" });
  }, [hit]);

  useEffect(() => {
    if (!missing.length) setAcknowledged(false);
  }, [missing.length]);

  function change(next: string) {
    setHit(null);
    onEditorTextChange(next);
  }

  function putAtCursor(chunk: string, from?: number, to?: number) {
    const el = area.current;
    const a = from ?? el?.selectionStart ?? editorText.length;
    const b = to ?? el?.selectionEnd ?? a;
    change(editorText.slice(0, a) + chunk + editorText.slice(b));
    requestAnimationFrame(() => {
      el?.focus();
      el?.setSelectionRange(a + chunk.length, a + chunk.length);
    });
  }

  // Pasting wording that holds known `{name}` codes turns them into chips (FR-62).
  function onPaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const pasted = e.clipboardData.getData("text");
    const converted = toEditorText(pasted, fields);
    if (converted === pasted) return;
    e.preventDefault();
    putAtCursor(converted, e.currentTarget.selectionStart, e.currentTarget.selectionEnd);
  }

  // The two fixes for a problem (FR-34): use one of our fields, or keep the braces as ordinary text.
  const fixWith = (p: WordingProblem, replacement: string) =>
    change(editorText.slice(0, p.start) + replacement + editorText.slice(p.end));
  const keepAsText = (p: WordingProblem) => fixWith(p, p.text.replace(/\{/g, "{{").replace(/\}/g, "}}"));

  function discard() {
    onEditorTextChange(savedText);
    onAppliedFindingsChange([]);
    setNote("");
    setError(null);
    setConflict(null);
    setComparing(false);
    setHit(null);
  }

  async function save() {
    const ok = await confirm({
      title: "Save this wording?",
      message: covered
        ? `Saving makes this the newest wording, and new drafts use it. Saving starts one paid AI review of the new wording against ${template.playbook?.name ?? "the playbook"}.`
        : "Saving makes this the newest wording, and new drafts use it. No playbook reviews this template, so no check will run.",
      confirmLabel: covered ? "Save and start the check" : "Save wording",
    });
    if (!ok) return;
    setSaving(true);
    setError(null);
    try {
      const result = await draftingTemplatesApi.save(template.key, {
        body: toBodyText(editorText, fields),
        note: note.trim() || undefined,
        expected_version: expected,
        applied_findings: appliedFindings,
        acknowledge_missing_parties: missing.length > 0 && acknowledged,
      });
      onEditorTextChange(toEditorText(result.template.body, result.template.placeholders));
      onAppliedFindingsChange([]);
      setExpected(result.template.version);
      setNote("");
      setAcknowledged(false);
      setHit(null);
      notify(
        !result.changed
          ? "Nothing changed, so no new version was made."
          : result.check
            ? "Template saved. New drafts use it, and a check has started."
            : "Template saved. New drafts use it.",
        "success",
      );
      onSaved(result);
    } catch (e) {
      await explain(e);
    } finally {
      setSaving(false);
    }
  }

  // A 409 can mean "someone saved after you opened it": fetch their version so it can be compared (FR-59).
  async function explain(e: unknown) {
    const message = e instanceof Error ? e.message : "Couldn't save the template.";
    const status = (e as { status?: number } | null)?.status;
    if (status === 409 || /after you opened it/.test(message)) {
      try {
        const latest = await draftingTemplatesApi.get(template.key);
        if (latest.version !== expected || /after you opened it/.test(message)) {
          setConflict({ message, theirs: latest });
          return;
        }
      } catch {
        /* fall through: show the server's own message */
      }
    }
    setError(message);
  }

  function resolveConflict(loadTheirs: boolean) {
    if (!conflict) return;
    const theirs = conflict.theirs;
    if (loadTheirs) {
      onEditorTextChange(toEditorText(theirs.body, theirs.placeholders));
      onAppliedFindingsChange([]);
      setNote("");
    } else {
      notify(`Your text is kept. Saving now replaces version ${theirs.version}.`, "info");
    }
    setExpected(theirs.version);
    onTemplateRefreshed?.(theirs);
    setConflict(null);
    setComparing(false);
  }

  const blocked = !dirty || saving || problems.length > 0 || (missing.length > 0 && !acknowledged) || conflict !== null;

  return (
    <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_260px]">
      {dialog}
      <div className="min-w-0 space-y-3">
        <div className="relative rounded-lg border border-slate-200 bg-slate-100">
          <div
            aria-hidden="true"
            className={cn(METRICS, "pointer-events-none absolute inset-0 overflow-hidden text-transparent")}
          >
            <ChipText text={editorText} fields={fields} hit={hit} hitRef={hitRef} ghost />
          </div>
          <textarea
            ref={area}
            value={editorText}
            onChange={(e) => change(e.target.value)}
            onPaste={onPaste}
            readOnly={!editable}
            spellCheck={false}
            aria-label={`${template.name} template text`}
            className={cn(
              METRICS,
              "relative block min-h-[62vh] w-full resize-none overflow-hidden rounded-lg bg-transparent text-slate-900 outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35",
            )}
          />
        </div>

        {editable && problems.length > 0 && (
          <MessageBar intent="error">
            <p className="font-medium">Fix {problems.length === 1 ? "this" : "these"} before saving.</p>
            <ul className="mt-1.5 space-y-2">
              {problems.map((p, i) => (
                <li key={`${p.kind}-${p.start}-${i}`} className="space-y-1">
                  <div>{p.message}</div>
                  {p.kind !== "empty" && (
                    <div className="flex flex-wrap items-center gap-2">
                      {p.kind === "unknown_field" && (
                        <Select
                          aria-label={`Use a field for ${p.text}`}
                          value=""
                          className="h-7 w-auto text-xs"
                          onChange={(e) => {
                            const f = fields.find((x) => x.name === e.target.value);
                            if (f) fixWith(p, `«${f.label}»`);
                          }}
                        >
                          <option value="">Use a field…</option>
                          {fields.map((f) => (
                            <option key={f.name} value={f.name}>
                              {f.label}
                            </option>
                          ))}
                        </Select>
                      )}
                      <Button type="button" size="sm" variant="outline" onClick={() => keepAsText(p)}>
                        Keep as ordinary text
                      </Button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </MessageBar>
        )}

        {editable && dirty && missing.length > 0 && problems.length === 0 && (
          <MessageBar intent="warning">
            <p>
              This wording has no field for {whoMissing(missing)}, so drafts will not name them. Confirm that this is
              intended to save it.
            </p>
            <label className="mt-2 flex items-center gap-2 text-[13px] text-slate-900">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => setAcknowledged(e.target.checked)}
                className="h-4 w-4 accent-brand-600"
              />
              This is intended: drafts need not name {whoMissing(missing)}
            </label>
          </MessageBar>
        )}

        {conflict && (
          <MessageBar intent="warning">
            <p>{conflict.message}</p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" aria-expanded={comparing} onClick={() => setComparing((c) => !c)}>
                Compare with their version
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => resolveConflict(true)}>
                Load theirs into the editor
              </Button>
              <Button type="button" size="sm" variant="outline" onClick={() => resolveConflict(false)}>
                Keep mine
              </Button>
            </div>
            {comparing && (
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <ComparePane title="Your text (not saved)" text={editorText} fields={fields} />
                <ComparePane
                  title={`Their version (version ${conflict.theirs.version})`}
                  text={toEditorText(conflict.theirs.body, conflict.theirs.placeholders)}
                  fields={fields}
                />
              </div>
            )}
          </MessageBar>
        )}

        {error && <MessageBar intent="error">{error}</MessageBar>}

        {editable && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5">
            <Input
              aria-label="What changed? (optional)"
              placeholder="What changed? (optional, shown in History)"
              value={note}
              maxLength={300}
              onChange={(e) => setNote(e.target.value)}
              className="min-w-[200px] flex-1"
            />
            <Button type="button" variant="outline" disabled={!dirty || saving} onClick={discard}>
              Discard changes
            </Button>
            <Button type="button" loading={saving} disabled={blocked} onClick={save}>
              Save
            </Button>
            {covered && dirty && (
              <p className="basis-full text-xs text-slate-500">Saving starts one paid AI review of the new wording.</p>
            )}
            {appliedFindings.length > 0 && (
              <p className="basis-full text-xs text-slate-500">
                {appliedFindings.length === 1
                  ? "1 suggestion applied from a check; saving records it."
                  : `${appliedFindings.length} suggestions applied from checks; saving records them.`}
              </p>
            )}
          </div>
        )}
      </div>

      <FieldList template={template} onInsert={editable ? (label) => putAtCursor(`«${label}»`) : null} />
    </div>
  );
}

function ComparePane({ title, text, fields }: { title: string; text: string; fields: readonly FieldLabel[] }): ReactNode {
  return (
    <section aria-label={title} className="min-w-0">
      <h4 className="mb-1 text-xs font-semibold text-slate-900">{title}</h4>
      <pre className="max-h-[40vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-100 p-3 font-mono text-xs leading-[1.6] text-slate-900">
        <ChipText text={text} fields={fields} flagProblems={false} />
      </pre>
    </section>
  );
}
