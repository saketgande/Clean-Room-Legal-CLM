"use client";

// The History tab of the Templates page (spec 006: FR-57, FR-58).
//
// Every saved or uploaded wording stays readable as a version, with who made it, when, its note, its source
// and the checks recorded against it. A version is never altered; "Load this version back" makes a NEW latest
// version with that wording.
//
//   <VersionHistory
//     template      the saved template (key, version, placeholders, playbook)
//     canChange     templatePermissions(user).canChange (Load this version back)
//     dirty?        the editor holds unsaved changes: the confirm then warns they are lost
//     onRestored(result)   a version was loaded back (TemplateMutationResult): the page updates its caches and
//                          resets the editor text to result.template.body (editor notation) and clears applied
//                          findings. This component has already shown the toast.
//   />
//
// Query key: ["drafting-template-versions", key]; the page invalidates it after a save. The list polls every
// 3 s while any listed check is queued or running, so a version's outcome chip updates on its own.

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, History } from "lucide-react";
import { Badge, Button, EmptyState, LoadError, SkeletonRows, useConfirm } from "@/components/ui";
import { useToast } from "@/components/toast";
import { draftingTemplatesApi } from "@/lib/endpoints";
import { toEditorText } from "@/lib/templates";
import type { DraftingTemplate, DraftingTemplateVersion, TemplateMutationResult } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import { checkOutcome, CHECK_POLL_MS } from "./_check-panel";
import { ChipText } from "./_wording-editor";

export interface VersionHistoryProps {
  template: DraftingTemplate;
  canChange: boolean;
  dirty?: boolean;
  onRestored: (result: TemplateMutationResult) => void;
}

function sourceLabel(v: DraftingTemplateVersion): string {
  const file = v.original_file ? ` · ${v.original_file.filename}` : "";
  switch (v.source) {
    case "default": return "Shipped wording";
    case "text": return "Typed text";
    case "docx": return `Word file${file}`;
    case "pdf": return `PDF${file}`;
    case "scan":
      return `Scan${file}${v.ocr_confirmed_by ? ` · text confirmed by ${v.ocr_confirmed_by}${v.ocr_confirmed_at ? ` at ${fmtDateTime(v.ocr_confirmed_at)}` : ""}` : ""}`;
  }
}

const SEVERITY_WORD = { low: "Low", medium: "Medium", high: "High", critical: "Critical" } as const;
const clause = (c: string) => c.replace(/_/g, " ").replace(/^./, (m) => m.toUpperCase());

export function VersionHistory({ template, canChange, dirty = false, onRestored }: VersionHistoryProps) {
  const key = template.key;
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog } = useConfirm();
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const covered = template.playbook?.available === true;

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["drafting-template-versions", key],
    queryFn: () => draftingTemplatesApi.versions(key),
    refetchInterval: (q) =>
      q.state.data?.some((v) => v.checks.some((c) => c.state === "queued" || c.state === "running")) ? CHECK_POLL_MS : false,
  });

  async function loadBack(v: DraftingTemplateVersion) {
    const name = v.version === 0 ? "the shipped wording" : `version ${v.version}`;
    const ok = await confirm({
      title: `Load ${name} back?`,
      message:
        `This makes the wording of ${name} the newest version of the template. Earlier versions stay as they are. ` +
        (covered
          ? "Loading it back starts one paid AI review of that wording."
          : "No playbook reviews this template, so no check will run.") +
        (dirty ? " The unsaved changes in the editor will be lost." : ""),
      confirmLabel: covered ? "Load back and start the check" : "Load it back",
    });
    if (!ok) return;
    setBusy(v.version);
    try {
      const result = await draftingTemplatesApi.restore(key, v.version, { expected_version: template.version });
      qc.invalidateQueries({ queryKey: ["drafting-template-versions", key] });
      notify(
        !result.changed
          ? "That wording is already the newest version, so nothing changed."
          : result.check
            ? `Loaded ${name} back as a new version, and a check has started.`
            : `Loaded ${name} back as a new version.`,
        "success",
      );
      onRestored(result);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't load that version back.", "error");
    } finally {
      setBusy(null);
    }
  }

  if (isLoading) return <div className="p-4"><SkeletonRows rows={4} /></div>;
  if (error) {
    return (
      <div className="p-4">
        <LoadError error={error} what="the version history" onRetry={() => refetch()} />
      </div>
    );
  }
  if (!data?.length) {
    return (
      <div className="p-4">
        <EmptyState
          icon={<History className="h-5 w-5" aria-hidden="true" />}
          title="Never edited"
          description="Drafts use the shipped wording until a version is saved."
        />
      </div>
    );
  }

  return (
    <div className="p-4">
      {dialog}
      <ul aria-label="Versions" className="divide-y divide-slate-200 rounded-lg border border-slate-200 bg-slate-100">
        {data.map((v) => {
          const isOpen = open === v.version;
          const current = v.version === template.version;
          const title = v.version === 0 ? "Shipped wording" : `Version ${v.version}`;
          return (
            <li key={v.version} className="p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : v.version)}
                  className="flex min-w-0 flex-1 items-start gap-2 rounded text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35"
                >
                  {isOpen ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" /> : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
                  <span className="min-w-0 space-y-0.5">
                    <span className="flex flex-wrap items-center gap-2">
                      <b className="text-[13px] text-slate-900">{title}</b>
                      {current && <Badge tone="violet">Current</Badge>}
                      <span className="text-xs text-slate-600">{sourceLabel(v)}</span>
                    </span>
                    <span className="block text-xs text-slate-500">
                      {v.version === 0
                        ? "The wording the template shipped with"
                        : `${v.created_by ?? "Someone"} · ${fmtDateTime(v.created_at)}`}
                    </span>
                  </span>
                </button>
                {canChange && !current && (
                  <Button type="button" size="sm" variant="outline" loading={busy === v.version} disabled={busy !== null} onClick={() => loadBack(v)}>
                    Load this version back
                  </Button>
                )}
              </div>

              <div className="mt-1.5 space-y-1 pl-6 text-xs text-slate-600">
                {v.note && <p>{v.note}</p>}
                {v.restored_from_version !== null && <p>{`Loaded back from ${v.restored_from_version === 0 ? "the shipped wording" : `version ${v.restored_from_version}`}.`}</p>}
                {v.applied_findings.length > 0 && (
                  <p>
                    {`Applied ${v.applied_findings.length === 1 ? "1 suggestion" : `${v.applied_findings.length} suggestions`} from checks: `}
                    {v.applied_findings.map((a) => `${clause(a.clause_type)} (${SEVERITY_WORD[a.severity]})`).join(", ")}.
                  </p>
                )}
                {v.checks.length > 0 && (
                  <ul aria-label={`Checks of ${title}`} className="flex flex-wrap gap-1.5">
                    {v.checks.map((c) => {
                      const o = checkOutcome(c);
                      return (
                        <li key={c.id}>
                          <Badge tone={o.tone} className="normal-case tracking-normal">
                            {`${o.word}${c.playbook_version_number ? ` · playbook v${c.playbook_version_number}` : ""}`}
                          </Badge>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {isOpen && (
                <pre
                  aria-label={`Wording of ${title}`}
                  className="mt-2 max-h-[50vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-3 font-mono text-xs leading-[1.6] text-slate-900"
                >
                  <ChipText text={toEditorText(v.body, template.placeholders)} fields={template.placeholders} flagProblems={false} />
                </pre>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
