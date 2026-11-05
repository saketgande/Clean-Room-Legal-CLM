"use client";

// One template, on the right of the Templates page (spec 006 FR-10, FR-24, FR-25, FR-59, FR-62, FR-64, D-11).
//
//   <TemplateDetail templateKey summary perms onDirtyChange />
//
//   summary         the LIST's copy of this template. The page polls the list, so its status and playbook are the
//                   freshest; the header, the Checks tab and the editor's save confirm read those from it.
//   perms           templatePermissions(user)
//   onDirtyChange   the editor holds unsaved wording. The page owns the leave guard and asks for this.
//
// This component owns what the tabs share: the saved template, the editor text, the suggestions applied from a
// check and the jump-to target. Mount it with `key={templateKey}`; it starts again for another template.
//
// The saved template is read once, then kept here and updated only by this person's own save, load-back, reset
// or playbook change (and by a 409, when they choose to see a colleague's version). It is NOT refreshed in the
// background: the editor's expected version follows it, so a refresh that quietly moved it would let stale text
// overwrite a colleague's save (FR-59). A 409 on save is how a colleague's change surfaces.
//
// Tabs: Wording and Checks. Wording is mounted only while shown (its textarea measures itself and cannot while
// hidden); Checks stays mounted, hidden, so the check the person is following keeps its selection.

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Replace } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  Field,
  LoadError,
  MessageBar,
  Modal,
  Select,
  SkeletonRows,
  Tabs,
  useConfirm,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { draftingTemplatesApi } from "@/lib/endpoints";
import { longDate } from "@/lib/form-checks";
import { toBodyText, toEditorText, type templatePermissions } from "@/lib/templates";
import type { DraftingTemplate, DraftingTemplateSummary, TemplateMutationResult } from "@/lib/types";
import { fmtDateTime } from "@/lib/utils";
import { CheckPanel } from "./_check-panel";
import { playbookText, ResultsVaryBadge, sourceText, StatusBadge } from "./_template-list";
import { VersionHistory } from "./_version-history";
import { WordingEditor, type FindingRef, type Range } from "./_wording-editor";

type Perms = ReturnType<typeof templatePermissions>;
type TabId = "wording" | "preview" | "checks" | "history";

const TABS: { id: TabId; label: string }[] = [
  { id: "wording", label: "Wording" },
  { id: "preview", label: "Preview" },
  { id: "checks", label: "Checks" },
  { id: "history", label: "History" },
];

export interface TemplateDetailProps {
  templateKey: string;
  summary: DraftingTemplateSummary;
  perms: Perms;
  onDirtyChange: (dirty: boolean) => void;
}

export function TemplateDetail(props: TemplateDetailProps) {
  const { data, error, refetch } = useQuery({
    queryKey: ["drafting-template", props.templateKey],
    queryFn: () => draftingTemplatesApi.get(props.templateKey),
  });
  if (!data) {
    return error ? (
      <LoadError error={error} what="this template" onRetry={() => refetch()} />
    ) : (
      <SkeletonRows rows={5} />
    );
  }
  return <Loaded initial={data} {...props} />;
}

/** The sentence under the title for a person who may not use every action (FR-64). */
function reasonLine(perms: Perms): string | null {
  if (perms.canChange && perms.canRun) return null;
  if (perms.canChange) return "Checking this template needs the permission to run playbook reviews.";
  if (perms.canRun) return "You can view and check templates; changing them needs the permission to edit playbooks.";
  return "You can view templates; checking them needs the permission to run playbook reviews, and changing them needs the permission to edit playbooks.";
}

/** Who reviews this template and whether that is settled (D-11). */
function playbookLine(t: DraftingTemplate): string {
  const text = playbookText(t);
  if (!t.playbook) return `${text}.`;
  return `${text} · ${t.playbook.pinned ? "locked to this template" : "matched by name; locked at its first check or draft"}`;
}

function Loaded({
  initial,
  templateKey: key,
  summary,
  perms,
  onDirtyChange,
}: TemplateDetailProps & { initial: DraftingTemplate }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog } = useConfirm();
  const [saved, setSaved] = useState(initial);
  const [editorText, setEditorText] = useState(() => toEditorText(initial.body, initial.placeholders));
  const [applied, setApplied] = useState<FindingRef[]>([]);
  const [jumpTo, setJumpTo] = useState<Range | null>(null);
  const [tab, setTab] = useState<TabId>("wording");
  const [choosing, setChoosing] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [adopting, setAdopting] = useState(false);
  // Strict like templatePermissions: only a held permission (or "*") offers the button the server would refuse.
  const { user } = useAuth();
  const canPublish = (user?.permissions ?? []).some((p) => p === "playbook:publish" || p === "*");

  // The saved template with the list's fresher status and playbook (a playbook can publish while the page is open).
  const live = useMemo<DraftingTemplate>(
    () => ({ ...saved, playbook: summary.playbook, status: summary.status, standard: summary.standard }),
    [saved, summary.playbook, summary.status, summary.standard],
  );
  const dirty = editorText !== toEditorText(saved.body, saved.placeholders);
  useEffect(() => onDirtyChange(dirty), [dirty, onDirtyChange]);

  const covered = live.playbook?.available === true;
  const reason = reasonLine(perms);
  const last = summary.status.last_completed;

  function adopt(next: DraftingTemplate) {
    setSaved(next);
    qc.setQueryData(["drafting-template", key], next);
  }
  function refreshAfterChange() {
    qc.invalidateQueries({ queryKey: ["drafting-templates"] });
    qc.invalidateQueries({ queryKey: ["drafting-template-versions", key] });
    qc.invalidateQueries({ queryKey: ["drafting-template-checks", key] });
  }
  /** The wording was replaced from outside the editor (load back, reset): the editor starts from it. */
  function adoptWording(result: TemplateMutationResult) {
    adopt(result.template);
    setEditorText(toEditorText(result.template.body, result.template.placeholders));
    setApplied([]);
    setJumpTo(null);
    refreshAfterChange();
  }

  async function reset() {
    const ok = await confirm({
      title: "Go back to the shipped wording?",
      message:
        "This makes the shipped wording the newest version of the template. Your edited versions stay in History. " +
        (covered
          ? `It starts one paid AI review of that wording against ${live.playbook?.name ?? "the playbook"}.`
          : "No playbook reviews this template, so no check will run.") +
        (dirty ? " The unsaved changes in the editor will be lost." : ""),
      confirmLabel: covered ? "Reset and start the check" : "Reset to shipped wording",
    });
    if (!ok) return;
    setResetting(true);
    try {
      const result = await draftingTemplatesApi.reset(key, { expected_version: saved.version });
      adoptWording(result);
      notify(
        !result.changed
          ? "Already on the shipped wording, so nothing changed."
          : result.check
            ? "Back to the shipped wording, and a check has started."
            : "Back to the shipped wording.",
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't reset the template.", "error");
    } finally {
      setResetting(false);
    }
  }

  async function useStandardRules() {
    const ok = await confirm({
      title: "Use the standard rules?",
      message: `This publishes the standard rules as the newest version of ${live.playbook?.name ?? "the playbook"}. Your own rules stay in its history.`,
      confirmLabel: "Use the standard rules",
    });
    if (!ok) return;
    setAdopting(true);
    try {
      const result = await draftingTemplatesApi.adoptStandardPlaybook(key, {});
      adopt(result.template);
      refreshAfterChange();
      const v = result.template.playbook?.version_number;
      notify(v ? `Standard rules published as version ${v}.` : "Standard rules published.", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't publish the standard rules.", "error");
    } finally {
      setAdopting(false);
    }
  }

  function showInTemplate(range: Range) {
    setTab("wording");
    setJumpTo({ start: range.start, end: range.end }); // a new object every time, so jumping twice to one place works
  }

  return (
    <Card className="min-w-0">
      {dialog}
      <header className="space-y-2 border-b border-slate-200 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1.5">
            <h2 className="text-[15px] font-semibold text-slate-900">{live.name}</h2>
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusBadge status={live.status} />
              {live.status.results_vary && <ResultsVaryBadge />}
              <span className="text-xs text-slate-600">{live.status.reason}</span>
            </div>
            <p className="text-xs text-slate-600">
              {last
                ? `Last checked ${fmtDateTime(last.finished_at)} against ${last.playbook_name ?? "the playbook"}${last.playbook_version_number ? ` (version ${last.playbook_version_number})` : ""}.`
                : "No check has finished yet."}
            </p>
            <p className="text-xs text-slate-600">{playbookLine(live)}</p>
            <p className="text-xs text-slate-600">
              Drafts {live.binding.label} · {sourceText(live)}
              {live.updated_by ? ` · changed by ${live.updated_by}${live.updated_at ? `, ${fmtDateTime(live.updated_at)}` : ""}` : ""}
            </p>
          </div>
          {perms.canChange && (
            <div className="flex flex-wrap gap-2">
              <Button type="button" size="sm" variant="outline" onClick={() => setChoosing(true)}>
                <Replace className="h-3.5 w-3.5" aria-hidden="true" />
                Change playbook
              </Button>
              {live.shipped && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  loading={resetting}
                  disabled={resetting || live.source === "default"}
                  title={live.source === "default" ? "Already on the shipped wording" : undefined}
                  onClick={reset}
                >
                  <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
                  Reset to shipped wording
                </Button>
              )}
            </div>
          )}
        </div>
        {reason && <p className="text-xs text-slate-500">{reason}</p>}
        {live.standard?.wording_behind && (
          <MessageBar intent="warning">
            Your saved wording is older than the standard wording of {longDate(live.standard.updated_on)}. The check
            below is the result on your wording.
            {perms.canChange && live.shipped && live.source !== "default" && (
              <Button type="button" size="sm" variant="outline" className="ml-2" loading={resetting} onClick={reset}>
                Reset to shipped wording
              </Button>
            )}
          </MessageBar>
        )}
        {live.standard?.playbook_behind && (
          <MessageBar intent="warning">
            {live.playbook?.name ?? "The playbook"} has rules older than the standard rules of{" "}
            {longDate(live.standard.updated_on)}.
            {canPublish && (
              <Button type="button" size="sm" variant="outline" className="ml-2" loading={adopting} onClick={useStandardRules}>
                Use the standard rules
              </Button>
            )}
          </MessageBar>
        )}
      </header>

      <div className="px-4">
        <Tabs
          label="Template sections"
          tabs={TABS}
          active={tab}
          onChange={(id) => {
            setJumpTo(null);
            setTab(id as TabId);
          }}
        />
      </div>

      {tab === "wording" && (
        <div role="tabpanel" aria-label="Wording">
          <WordingEditor
            template={live}
            canChange={perms.canChange}
            editorText={editorText}
            onEditorTextChange={setEditorText}
            appliedFindings={applied}
            onAppliedFindingsChange={setApplied}
            jumpTo={jumpTo}
            onSaved={(result) => {
              adopt(result.template);
              refreshAfterChange();
            }}
            onTemplateRefreshed={adopt}
          />
        </div>
      )}
      {tab === "preview" && (
        <div role="tabpanel" aria-label="Preview">
          <Preview template={live} editorText={editorText} />
        </div>
      )}
      <div role="tabpanel" aria-label="Checks" hidden={tab !== "checks"}>
        <CheckPanel
          template={live}
          status={summary.status}
          editorText={editorText}
          dirty={dirty}
          canRun={perms.canRun}
          canChange={perms.canChange}
          appliedFindings={applied}
          onApply={(range, replacement, ref) => {
            setEditorText(editorText.slice(0, range.start) + replacement + editorText.slice(range.end));
            setApplied([...applied, ref]);
            notify("Suggestion applied in the editor. It is saved only when you save the wording.", "info");
          }}
          onShowInTemplate={showInTemplate}
          onChecksChanged={() => {
            // Not the template itself: see the header comment.
            qc.invalidateQueries({ queryKey: ["drafting-templates"] });
            qc.invalidateQueries({ queryKey: ["drafting-template-versions", key] });
          }}
        />
      </div>
      {tab === "history" && (
        <div role="tabpanel" aria-label="History">
          <VersionHistory template={live} canChange={perms.canChange} dirty={dirty} onRestored={adoptWording} />
        </div>
      )}

      <ChangePlaybook
        open={choosing}
        onClose={() => setChoosing(false)}
        template={live}
        onChanged={(result) => {
          adopt(result.template);
          refreshAfterChange();
          setChoosing(false);
          notify(
            !result.changed
              ? "That playbook already reviews this template, so nothing changed."
              : result.check
                ? "Playbook changed, and a check against it has started."
                : "Playbook changed.",
            "success",
          );
        }}
      />
    </Card>
  );
}

// ---- Preview -------------------------------------------------------------------------------------------------
function Preview({ template, editorText }: { template: DraftingTemplate; editorText: string }) {
  const body = toBodyText(editorText, template.placeholders);
  const { data, error, isFetching } = useQuery({
    queryKey: ["drafting-template-preview", template.key, body],
    queryFn: () => draftingTemplatesApi.preview(template.key, body),
    retry: false,
  });
  // The fields the wording uses, by their plain names (FR-62). A Word-based template reports its own list.
  const used = template.editable
    ? template.placeholders.filter((p) => editorText.includes(`«${p.label}»`))
    : template.fields.map((f) => ({ name: f.name, label: f.label, sample: "" }));
  return (
    <div className="space-y-3 p-4">
      <p className="text-xs text-slate-500">
        The wording filled in with example values{isFetching ? " …" : ""}. This is roughly what a draft will read like.
        {template.editable && editorText !== toEditorText(template.body, template.placeholders)
          ? " It shows your unsaved changes."
          : ""}
      </p>
      {error ? (
        <MessageBar intent="error">{error instanceof Error ? error.message : "This wording can't be filled in."}</MessageBar>
      ) : (
        <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-[12.5px] leading-[1.65] text-slate-900">
          {data?.text ?? ""}
        </pre>
      )}
      <div className="space-y-1.5">
        <h3 className="text-xs font-semibold text-slate-900">Fields used in this wording</h3>
        {used.length === 0 ? (
          <p className="text-xs text-slate-500">None. Every draft reads the same.</p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {used.map((p) => (
              <li key={p.name} className="flex items-center gap-1 text-xs text-slate-600">
                <Badge tone="violet" className="normal-case tracking-normal">
                  {p.label}
                </Badge>
                {p.sample ? <span>e.g. {p.sample}</span> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

// ---- Change playbook (D-11) ----------------------------------------------------------------------------------
function ChangePlaybook({
  open,
  onClose,
  template,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  template: DraftingTemplate;
  onChanged: (result: TemplateMutationResult) => void;
}) {
  const options = useQuery({
    queryKey: ["drafting-template-options"],
    queryFn: () => draftingTemplatesApi.options(),
    enabled: open,
  });
  const [choice, setChoice] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) return;
    setChoice("");
    setError(null);
  }, [open]);
  const playbooks = options.data?.playbooks ?? [];
  const current = template.playbook;
  const value = choice || (current && playbooks.some((p) => p.id === current.id) ? current.id : "");
  // Choosing the playbook that is already pinned changes nothing. Choosing the name-matched one locks it in.
  const unchanged = value !== "" && value === current?.id && current.pinned;

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      onChanged(await draftingTemplatesApi.setPlaybook(template.key, { playbook_id: value }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't change the playbook.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => !saving && onClose()}
      title="Change playbook"
      footer={
        <>
          <Button type="button" variant="outline" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" loading={saving} disabled={!value || unchanged || saving} onClick={submit}>
            Change playbook and start the check
          </Button>
        </>
      }
    >
      <div className="space-y-3 text-sm text-slate-600">
        <p>
          Choose the playbook that reviews {template.name}. A paid AI review against the new playbook starts. Contracts
          already drafted keep the playbook they were drafted with.
        </p>
        <p>From now on this template, its checks and every contract drafted from it are reviewed by the playbook you choose.</p>
        {options.isLoading && <SkeletonRows rows={1} />}
        {options.error ? (
          <LoadError error={options.error} what="the playbooks" onRetry={() => options.refetch()} />
        ) : (
          !options.isLoading && (
            <Field label="Playbook">
              <Select value={value} onChange={(e) => setChoice(e.target.value)} disabled={saving}>
                <option value="">Choose a playbook…</option>
                {playbooks.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} (version {p.version_number})
                  </option>
                ))}
              </Select>
            </Field>
          )
        )}
        {!options.isLoading && !options.error && playbooks.length === 0 && (
          <p className="text-xs text-slate-500">No playbook of this organisation has a published version yet.</p>
        )}
        {error && <MessageBar intent="error">{error}</MessageBar>}
      </div>
    </Modal>
  );
}
