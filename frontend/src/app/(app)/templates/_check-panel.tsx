"use client";

// The Checks tab of the Templates page (spec 006: FR-5, FR-8, FR-10, FR-11, FR-16, FR-18, FR-23 to FR-25).
//
// A check is one paid AI review of the template's wording, run by the server as a background job and recorded
// (draftingTemplatesApi.check -> 202). This panel starts checks, follows the one in progress (it polls
// `checkDetail` every 3 s while a check is queued or running), shows a recorded check with its findings, and
// lists the check history.
//
//   <CheckPanel
//     template            the saved template (key, name, playbook, version, body, placeholders, editable)
//     status              TemplateStatus from the list (fresher than template.status: the page polls the list)
//     editorText          the wording as the editor holds it, `«Plain name»` notation (what findings are located in)
//     dirty               editorText differs from the saved wording
//     canRun              templatePermissions(user).canRun    (Check now / Try again)
//     canChange           templatePermissions(user).canChange (Apply suggestion)
//     appliedFindings?    suggestions already applied in the editor and not saved (hides their Apply button)
//     onApply(range, replacement, ref)   replace `range` of editorText with `replacement` and add `ref` to the
//                                         page's appliedFindings (Discard clears both)
//     onShowInTemplate(range)            switch to the Wording tab and pass `range` as WordingEditor `jumpTo`
//     onChecksChanged?()  a check was started or finished: refresh the list, the template and its versions
//   />
//
// Keep this component mounted across tab switches if you can (hidden, not unmounted): it remembers which
// unsaved check is the person's own. Query keys used: ["drafting-template-checks", key] and
// ["drafting-template-check", key, id].
// Also exported: CheckStatements, the three FR-10 sentences, for the page's standing bar.

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Crosshair, Sparkles } from "lucide-react";
import { Badge, Button, LoadError, MessageBar, Spinner, Table, TD, TH, THead, TR } from "@/components/ui";
import { useToast } from "@/components/toast";
import { draftingTemplatesApi } from "@/lib/endpoints";
import {
  canApplyFinding,
  locatePassage,
  severityTone,
  sha256Hex,
  toBodyText,
  toEditorText,
  type BadgeTone,
} from "@/lib/templates";
import type {
  DraftingTemplate,
  FindingSeverity,
  TemplateCheck,
  TemplateCheckFinding,
  TemplateCheckState,
  TemplateCheckSummary,
  TemplateCheckTrigger,
  TemplateStatus,
  TemplateStatusState,
} from "@/lib/types";
import { fmtDateTime, plural } from "@/lib/utils";
import { ChipText, type FindingRef, type Range } from "./_wording-editor";

export const CHECK_POLL_MS = 3000;

/** The three things the page must say wherever a status or a check result is shown (FR-10). */
export const FR10_STATEMENTS = [
  "A check is an AI review, and it can report different findings on identical wording.",
  "Passes means the latest review found nothing. It is not a guarantee about any contract.",
  "A check reads the template filled in with example details, as a draft would read. It does not judge the answers a requester picks.",
] as const;

export function CheckStatements() {
  return (
    <MessageBar intent="info">
      <ul className="list-disc space-y-0.5 pl-4">
        {FR10_STATEMENTS.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </MessageBar>
  );
}

const SEVERITY_LABEL: Record<FindingSeverity, string> = { low: "Low", medium: "Medium", high: "High", critical: "Critical" };

const TRIGGER_LABEL: Record<TemplateCheckTrigger, string> = {
  saved: "Saved",
  uploaded: "Uploaded",
  restored: "Loaded back",
  reset: "Reset to shipped wording",
  type_created: "Type created",
  type_reactivated: "Type reactivated",
  playbook_published: "Playbook published",
  playbook_changed: "Playbook changed",
  manual: "Checked by hand",
  check_all: "Check all",
  retry: "Tried again",
  sample: "Sample answers",
};

const STATUS_INTENT: Record<TemplateStatusState, "info" | "success" | "warning" | "error"> = {
  passes: "success",
  has_findings: "warning",
  checking: "info",
  out_of_date: "warning",
  partly_checked: "warning",
  couldnt_check: "error",
  never_checked: "info",
  no_playbook: "info",
};

const ERROR_WORDS: Record<string, string> = {
  ai_unavailable: "The AI review was unavailable.",
  cost_cap: "The organisation's AI spending limit was reached.",
  no_rules: "The playbook version has no rules to check against.",
  no_playbook: "No published playbook covers this template.",
  unfillable: "The wording could not be filled in.",
  wording_changed: "The wording changed before the check started.",
  interrupted: "The check was interrupted before it finished.",
};

const isActive = (s: TemplateCheckState | undefined) => s === "queued" || s === "running";
const rank = (s: TemplateCheckState) => (s === "queued" ? 0 : s === "running" ? 1 : 2);
const versionName = (v: number) => (v === 0 ? "shipped wording" : `version ${v}`);
const clauseLabel = (c: string) => {
  const t = c.replace(/_/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : "Other";
};

// The unsaved checks this browser tab started. A saved check is the template's own record, but an unsaved one
// may be applied only by the person who made it, in the session they made it (FR-25), so it is remembered here
// and not read from the server.
const ownUnsavedChecks = new Set<string>();

/** Of two copies of one check, the one further along (a stale list never rolls a finished check back). */
const furtherAlong = (a: TemplateCheck | undefined, b: TemplateCheck): TemplateCheck =>
  a && rank(a.state) > rank(b.state) ? a : b;

function byText(c: Pick<TemplateCheckSummary, "requested_by" | "on_behalf">): string {
  if (!c.requested_by) return "System";
  return c.on_behalf ? `System, for ${c.requested_by}` : c.requested_by;
}

/** A check's result in words, with the badge tone that goes with it (also used by the version history). */
export function checkOutcome(c: TemplateCheckSummary): { word: string; tone: BadgeTone; detail: string } {
  switch (c.state) {
    case "queued": return { word: "Waiting", tone: "blue", detail: "to start" };
    case "running": return { word: "Running", tone: "blue", detail: "" };
    case "superseded": return { word: "Replaced", tone: "slate", detail: "by a check of newer wording" };
    case "failed":
      return {
        word: "Couldn't check",
        tone: "red",
        detail: c.error_message ?? (c.error_code ? ERROR_WORDS[c.error_code] : "") ?? "",
      };
    case "completed": {
      const n = c.finding_count ?? 0;
      const part = c.text_was_truncated ? "; only part of the template was read" : "";
      return n === 0
        ? { word: "No findings", tone: c.text_was_truncated ? "amber" : "green", detail: part.replace(/^; /, "") }
        : {
            word: plural(n, "finding"),
            tone: severityTone(c.highest_severity),
            detail: `highest ${SEVERITY_LABEL[c.highest_severity ?? "low"]}${part}`,
          };
    }
  }
}

// ---- the panel ----------------------------------------------------------------------------------------------
export interface CheckPanelProps {
  template: DraftingTemplate;
  status: TemplateStatus;
  editorText: string;
  dirty: boolean;
  canRun: boolean;
  canChange: boolean;
  appliedFindings?: FindingRef[];
  onApply: (range: Range, replacement: string, ref: FindingRef) => void;
  onShowInTemplate: (range: Range) => void;
  onChecksChanged?: () => void;
}

export function CheckPanel({
  template,
  status,
  editorText,
  dirty,
  canRun,
  canChange,
  appliedFindings = [],
  onApply,
  onShowInTemplate,
  onChecksChanged,
}: CheckPanelProps) {
  const key = template.key;
  const listKey = ["drafting-template-checks", key];
  const qc = useQueryClient();
  const { notify } = useToast();
  const [started, setStarted] = useState<TemplateCheck | null>(null);
  const [details, setDetails] = useState<Record<string, TemplateCheck>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [currentSha, setCurrentSha] = useState("");
  const changed = useRef(onChecksChanged);
  useEffect(() => {
    changed.current = onChecksChanged;
  });

  // Another template: forget the selection and the check started on the last one.
  useEffect(() => {
    setStarted(null);
    setDetails({});
    setSelectedId(null);
    setRequestError(null);
  }, [key]);

  // sha256 of the saved wording: a saved check can be applied only while it still describes it.
  useEffect(() => {
    let live = true;
    sha256Hex(template.body).then(
      (s) => live && setCurrentSha(s),
      () => live && setCurrentSha(""), // no WebCrypto (plain http): saved checks then offer no Apply
    );
    return () => {
      live = false;
    };
  }, [template.body]);

  const list = useQuery({ queryKey: listKey, queryFn: () => draftingTemplatesApi.checks(key) });

  // The status moved on (a check started, finished or was replaced): the history is out of date.
  const signature = [status.state, status.active_check?.id, status.decided_by?.id, status.last_completed?.id].join("|");
  const firstSignature = useRef(true);
  useEffect(() => {
    if (firstSignature.current) {
      firstSignature.current = false;
      return;
    }
    qc.invalidateQueries({ queryKey: ["drafting-template-checks", key] });
  }, [signature, qc, key]);

  // Every copy of every check we know, the furthest along winning.
  const known = new Map<string, TemplateCheck>();
  for (const c of [...(started ? [started] : []), ...(list.data ?? []), ...Object.values(details)]) {
    known.set(c.id, furtherAlong(known.get(c.id), c));
  }
  const stateOf = (id: string) => known.get(id)?.state;

  // The check being followed: the one the person is looking at when it is still going, else the one the status
  // says is in progress.
  const activeId = status.active_check?.id;
  const watchId =
    selectedId && isActive(stateOf(selectedId))
      ? selectedId
      : activeId && (stateOf(activeId) === undefined || isActive(stateOf(activeId)))
        ? activeId
        : null;
  const detail = useQuery({
    queryKey: ["drafting-template-check", key, watchId],
    queryFn: () => draftingTemplatesApi.checkDetail(key, watchId as string),
    enabled: watchId !== null,
    refetchInterval: (q) => (q.state.status === "error" || (q.state.data && !isActive(q.state.data.state)) ? false : CHECK_POLL_MS),
  });
  useEffect(() => {
    const d = detail.data;
    if (!d) return;
    setDetails((prev) => (prev[d.id] === d ? prev : { ...prev, [d.id]: d }));
    if (!isActive(d.state)) {
      qc.invalidateQueries({ queryKey: ["drafting-template-checks", key] });
      changed.current?.();
    }
  }, [detail.data, qc, key]);

  const shownId = selectedId ?? status.decided_by?.id ?? status.last_completed?.id ?? null;
  const shown = shownId ? known.get(shownId) : undefined;

  async function run(unsaved: boolean) {
    setStarting(true);
    setRequestError(null);
    try {
      const check = await draftingTemplatesApi.check(key, unsaved ? toBodyText(editorText, template.placeholders) : undefined);
      setStarted(check);
      if (check.unsaved) ownUnsavedChecks.add(check.id);
      setSelectedId(check.unsaved ? check.id : null);
      qc.invalidateQueries({ queryKey: ["drafting-template-checks", key] });
      changed.current?.();
      notify("Check started. It runs in the background, so you can leave this page.", "info");
    } catch (e) {
      setRequestError(e instanceof Error ? e.message : "The check could not be started.");
    } finally {
      setStarting(false);
    }
  }

  const playbook = template.playbook;
  const covered = playbook?.available === true;
  const lastRef = known.get(status.last_completed?.id ?? "") ?? known.get(status.decided_by?.id ?? "");
  const vary = status.results_vary ? varyCounts(list.data ?? [], lastRef) : null;
  const last = status.last_completed;
  const activeNote =
    status.active_check && shownId !== status.active_check.id && isActive(stateOf(status.active_check.id) ?? status.active_check.state);

  return (
    <section aria-label="Checks" className="space-y-3 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-2xl space-y-1">
          <h3 className="text-sm font-semibold text-slate-900">Check against the playbook</h3>
          <p className="text-xs text-slate-600">
            {covered
              ? `Runs the AI review of ${playbook.name}${playbook.version_number ? ` (version ${playbook.version_number})` : ""} on this template's wording. Each check is one paid AI review.`
              : playbook === null
                ? "No published playbook reviews this template, so there is nothing to check it against."
                : "The playbook that reviews this template can't be used right now, so there is nothing to check it against."}
          </p>
          <p className="text-xs text-slate-600">
            {dirty
              ? "Checks the text in the editor, including changes you have not saved. The result is shown to you and doesn't change the template's status."
              : "Checks the saved wording."}
          </p>
        </div>
        {canRun ? (
          <Button type="button" loading={starting} disabled={!covered || starting} onClick={() => run(dirty)}>
            {dirty ? "Check the unsaved wording (1 paid AI review)" : "Check now (1 paid AI review)"}
          </Button>
        ) : (
          <p className="max-w-xs text-xs text-slate-500">Running a check needs the permission to run playbook reviews.</p>
        )}
      </div>

      <CheckStatements />

      {requestError && <MessageBar intent="error">{requestError}</MessageBar>}

      <MessageBar intent={STATUS_INTENT[status.state]}>
        <p>{`Status: ${status.label}. ${status.reason}`}</p>
        {status.state === "couldnt_check" && canRun && (
          <Button type="button" size="sm" variant="outline" className="mt-2" loading={starting} onClick={() => run(false)}>
            Try again
          </Button>
        )}
      </MessageBar>

      {vary && (
        <MessageBar intent="warning">
          <p>
            {vary.withFindings + vary.none >= 2
              ? `Identical wording has given different results: ${plural(vary.withFindings, "check")} found findings, ${vary.none} found none.`
              : "Identical wording has given different results."}
          </p>
        </MessageBar>
      )}

      <p className="text-xs text-slate-600">
        {last
          ? `Last completed check: finished ${fmtDateTime(last.finished_at)}, against ${last.playbook_name ?? "the playbook"}${last.playbook_version_number ? ` version ${last.playbook_version_number}` : ""}, of ${versionName(last.template_version)}.`
          : "No check has finished yet."}
      </p>

      {activeNote && (
        <p role="status" className="flex items-center gap-2 text-xs text-slate-600">
          <Spinner className="h-4 w-4" />A check of the saved wording is in progress. The result below is the earlier one until it
          finishes.
        </p>
      )}

      {list.isError && <LoadError error={list.error} what="the check history" onRetry={() => list.refetch()} />}

      {shown ? (
        <ResultCard
          check={shown}
          template={template}
          editorText={editorText}
          canChange={canChange}
          currentSha={currentSha}
          appliedFindings={appliedFindings}
          onApply={onApply}
          onShowInTemplate={onShowInTemplate}
        />
      ) : shownId && list.isLoading ? (
        <p role="status" className="flex items-center gap-2 text-xs text-slate-500">
          <Spinner className="h-4 w-4" />
          Loading the check…
        </p>
      ) : null}

      <History checks={list.data ?? []} loading={list.isLoading} selectedId={shown?.id ?? null} onSelect={setSelectedId} />
    </section>
  );
}

function varyCounts(checks: TemplateCheck[], ref: TemplateCheck | undefined) {
  if (!ref) return { withFindings: 0, none: 0 };
  const same = checks.filter(
    (c) =>
      c.state === "completed" && !c.unsaved && c.wording_sha256 === ref.wording_sha256 && c.playbook_version_id === ref.playbook_version_id,
  );
  const withFindings = same.filter((c) => (c.finding_count ?? c.findings.length) > 0).length;
  return { withFindings, none: same.length - withFindings };
}

// ---- one check, with its findings ---------------------------------------------------------------------------
function ResultCard({
  check,
  template,
  editorText,
  canChange,
  currentSha,
  appliedFindings,
  onApply,
  onShowInTemplate,
}: {
  check: TemplateCheck;
  template: DraftingTemplate;
  editorText: string;
  canChange: boolean;
  currentSha: string;
  appliedFindings: FindingRef[];
  onApply: CheckPanelProps["onApply"];
  onShowInTemplate: CheckPanelProps["onShowInTemplate"];
}) {
  const n = check.findings.length;
  const older = !check.unsaved && check.template_version !== template.version;
  return (
    <section data-testid="check-result" aria-label="Check result" className="space-y-3 rounded-lg border border-slate-200 bg-slate-100 p-3">
      <header className="flex flex-wrap items-center gap-2">
        <h4 className="text-[13px] font-semibold text-slate-900">Check from {fmtDateTime(check.finished_at ?? check.created_at)}</h4>
        {check.unsaved && <Badge tone="violet">Unsaved wording</Badge>}
        {older && <Badge tone="amber">{`Older wording (${versionName(check.template_version)})`}</Badge>}
        <span className="text-xs text-slate-500">
          {`Wording: ${versionName(check.template_version)}. Playbook: ${check.playbook_name ?? "none"}${check.playbook_version_number ? ` version ${check.playbook_version_number}` : ""}.`}
        </span>
      </header>
      {check.unsaved && (
        <p className="text-xs text-slate-600">This check was of wording that was not saved, so it doesn&apos;t change the template&apos;s status.</p>
      )}

      {isActive(check.state) && (
        <p role="status" className="flex items-center gap-2 text-[13px] text-slate-700">
          <Spinner className="h-4 w-4" />
          {check.state === "queued"
            ? "Waiting to start. You can leave this page; the check carries on."
            : "Reading the wording against the playbook's rules. This can take a little while. You can leave this page; the check carries on."}
        </p>
      )}
      {check.state === "failed" && (
        <MessageBar intent="error">{`This check could not finish. ${checkOutcome(check).detail}`}</MessageBar>
      )}
      {check.state === "superseded" && (
        <MessageBar intent="info">This check was replaced by a check of newer wording, so it has no result.</MessageBar>
      )}

      {check.state === "completed" && (
        <>
          {/* check.summary is deliberately not shown: it is the model thinking aloud and can contradict the
              findings (a stored MSA summary talked about indemnification and "three deviations" while the one
              finding was service levels). The findings list below is the record. */}
          {n === 0 ? (
            <MessageBar intent={check.text_was_truncated ? "warning" : "success"}>
              <b>No findings.</b> The AI review of {check.playbook_name ?? "the playbook"} found nothing to flag in
              {check.text_was_truncated ? " the part of the wording it read" : " this wording"}
              {check.rule_count !== null ? ` (${plural(check.rule_count, "rule")} checked)` : ""}.
            </MessageBar>
          ) : (
            <MessageBar intent="warning">
              <b>{plural(n, "finding")}.</b> The AI review of {check.playbook_name ?? "the playbook"} flagged {n === 1 ? "this" : "these"} in
              this wording{check.rule_count !== null ? ` (${plural(check.rule_count, "rule")} checked)` : ""}.
            </MessageBar>
          )}
          {check.text_was_truncated && (
            <MessageBar intent="warning">
              This template is longer than the review can read, so the end of it was not checked
              {check.read_length !== null && check.text_length !== null
                ? ` (the review read ${check.read_length.toLocaleString()} of ${check.text_length.toLocaleString()} characters)`
                : ""}
              .
            </MessageBar>
          )}
          {n > 0 && (
            <ul className="space-y-2.5">
              {check.findings.map((f) => (
                <FindingCard
                  key={f.index}
                  check={check}
                  finding={f}
                  template={template}
                  editorText={editorText}
                  canChange={canChange}
                  currentSha={currentSha}
                  applied={appliedFindings.some((a) => a.check_id === check.id && a.finding_index === f.index)}
                  onApply={onApply}
                  onShowInTemplate={onShowInTemplate}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function FindingCard({
  check,
  finding,
  template,
  editorText,
  canChange,
  currentSha,
  applied,
  onApply,
  onShowInTemplate,
}: {
  check: TemplateCheck;
  finding: TemplateCheckFinding;
  template: DraftingTemplate;
  editorText: string;
  canChange: boolean;
  currentSha: string;
  applied: boolean;
  onApply: CheckPanelProps["onApply"];
  onShowInTemplate: CheckPanelProps["onShowInTemplate"];
}) {
  const fields = template.placeholders;
  const quote = finding.quote_template ?? finding.quote;
  const quoteText = quote === null ? null : toEditorText(quote, fields);
  const located = locatePassage(editorText, quoteText);
  const fix = finding.suggested_fix_template ?? finding.suggested_fix;
  const fixText = fix === null ? null : toEditorText(fix, fields);
  const ref: FindingRef = { check_id: check.id, finding_index: finding.index };
  // The caller's own unsaved check only: someone else's unsaved wording is not what this editor holds.
  const mine = !check.unsaved || ownUnsavedChecks.has(check.id);
  const canApply =
    !applied &&
    mine &&
    canApplyFinding({
      check, finding, editorText, currentVersion: template.version, currentSha, editable: template.editable, canChange, fields,
    });

  // Why an offered-looking suggestion is for reading only (FR-25): said, not silently dropped.
  let readOnlyWhy: string | null = null;
  if (fixText && !canApply && !applied && canChange) {
    if (!template.editable) readOnlyWhy = "Make this change in Word: download the file, edit it, then upload it again.";
    else if (!finding.suggestion_is_wording) readOnlyWhy = "This is advice rather than wording to put in, so it can't be applied.";
    else if (!mine) readOnlyWhy = "This check was of unsaved wording that isn't in your editor, so its suggestion can't be applied.";
    else if (check.template_version !== template.version) readOnlyWhy = "This check was of older wording, so its suggestion can't be applied.";
    else if (located.count > 1) readOnlyWhy = `The passage appears ${located.count} times in the wording, so the suggestion can't be applied automatically.`;
  }

  return (
    <li className="space-y-2 rounded-lg border border-slate-200 border-l-[3px] bg-slate-50 p-3" data-severity={finding.severity}>
      <div className="flex flex-wrap items-center gap-2">
        <b className="text-[13px] text-slate-900">{clauseLabel(finding.clause_type)}</b>
        <Badge tone={severityTone(finding.severity)}>{SEVERITY_LABEL[finding.severity]}</Badge>
        {finding.approval_required && <Badge tone="violet">Needs approval</Badge>}
      </div>
      <p className="text-[13px] text-slate-800">{finding.issue}</p>
      {quoteText && (
        <blockquote className="whitespace-pre-wrap break-words border-l-2 border-slate-300 bg-slate-100 px-2.5 py-1.5 font-mono text-xs text-slate-700">
          <ChipText text={quoteText} fields={fields} flagProblems={false} />
        </blockquote>
      )}
      {fixText && (
        <div className="text-[13px] text-slate-800">
          <b>{finding.suggestion_is_wording ? "Suggested wording:" : "Suggested change (advice):"}</b>{" "}
          <span className="whitespace-pre-wrap break-words">
            <ChipText text={fixText} fields={fields} flagProblems={false} />
          </span>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {quoteText && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={located.count === 0}
            onClick={() => onShowInTemplate({ start: located.start, end: located.end })}
          >
            <Crosshair className="h-3.5 w-3.5" aria-hidden="true" />
            Show in template
          </Button>
        )}
        {canApply && fixText && (
          <Button type="button" size="sm" onClick={() => onApply({ start: located.start, end: located.end }, fixText, ref)}>
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            Apply suggestion
          </Button>
        )}
      </div>
      {quoteText && located.count === 0 && (
        <p className="text-xs text-slate-500">Couldn&apos;t locate this passage in the current wording</p>
      )}
      {applied && (
        <p className="text-xs text-slate-600">Applied in the editor. It is not saved yet: save to keep it, or discard the changes.</p>
      )}
      {readOnlyWhy && <p className="text-xs text-slate-500">{readOnlyWhy}</p>}
    </li>
  );
}

// ---- check history (FR-18) ----------------------------------------------------------------------------------
function History({
  checks,
  loading,
  selectedId,
  onSelect,
}: {
  checks: TemplateCheck[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <section aria-label="Check history" className="space-y-1.5">
      <h4 className="text-[13px] font-semibold text-slate-900">Check history</h4>
      {checks.length === 0 ? (
        <p className="text-xs text-slate-500">{loading ? "Loading…" : "No checks have been recorded for this template yet."}</p>
      ) : (
        <Table aria-label="Recorded checks">
          <THead>
            <tr>
              <TH>When</TH>
              <TH>Started by</TH>
              <TH>Reason</TH>
              <TH>Wording</TH>
              <TH>Playbook</TH>
              <TH>Outcome</TH>
            </tr>
          </THead>
          <tbody>
            {checks.map((c) => {
              const o = checkOutcome(c);
              const when = fmtDateTime(c.finished_at ?? c.started_at ?? c.created_at);
              return (
                <TR key={c.id} className={c.id === selectedId ? "bg-brand-50" : undefined}>
                  <TD>
                    <button
                      type="button"
                      aria-pressed={c.id === selectedId}
                      aria-label={`Show the check from ${when}`}
                      onClick={() => onSelect(c.id)}
                      className="rounded text-left text-brand-700 underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35"
                    >
                      {when}
                    </button>
                  </TD>
                  <TD>{byText(c)}</TD>
                  <TD>
                    {TRIGGER_LABEL[c.trigger]}{c.unsaved ? " (unsaved wording)" : ""}
                    {typeof c.trigger_detail?.combination === "string" && (
                      <span className="block text-xs text-slate-500">{c.trigger_detail.combination}</span>
                    )}
                  </TD>
                  <TD>{c.template_version === 0 ? "Shipped wording" : `Version ${c.template_version}`}</TD>
                  <TD>
                    {c.playbook_name ?? "None"}
                    {c.playbook_version_number ? ` (version ${c.playbook_version_number})` : ""}
                  </TD>
                  <TD>
                    <Badge tone={o.tone}>{o.word}</Badge> {o.detail}
                  </TD>
                </TR>
              );
            })}
          </tbody>
        </Table>
      )}
    </section>
  );
}
