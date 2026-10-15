"use client";

// Drafting templates — our standard paper, one per playbook (spec 006). A request's "Draft" fills the template
// for its kind of agreement; the playbook that reviews it then reviews the result. This page shows whether each
// template currently passes that playbook's AI review, lets the right people check, edit and re-point it, and
// keeps every earlier wording and check on record.
//
// The page is the shell: header, the FR-10 statements, the status strip, the list and the polling. One template
// at a time is opened in _template-detail.tsx, which owns the editor text. The page owns the leave guard
// (FR-61), because leaving, refreshing and switching template all have to ask about that editor.

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileText } from "lucide-react";
import { Button, EmptyState, LoadError, SkeletonRows } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { draftingTemplatesApi } from "@/lib/endpoints";
import { needsAttention, templatePermissions } from "@/lib/templates";
import type { TemplateStatusState } from "@/lib/types";
import { CheckAll, CheckAllProgress, type CheckAllRun } from "./_check-all";
import { CHECK_POLL_MS, CheckStatements } from "./_check-panel";
import { useLeaveGuard } from "./_leave-guard";
import { TemplateDetail } from "./_template-detail";
import { STATUS_ICON, STATUS_WORDS, TemplateList } from "./_template-list";

type Filter = "all" | "attention" | TemplateStatusState;

// The order the spec lists the statuses in (FR-2).
const STATES: TemplateStatusState[] = [
  "passes", "has_findings", "checking", "out_of_date", "partly_checked", "couldnt_check", "never_checked", "no_playbook",
];

function FilterChip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button type="button" size="sm" variant={pressed ? "primary" : "outline"} aria-pressed={pressed} onClick={onClick}>
      {children}
    </Button>
  );
}

export default function TemplatesPage() {
  const { user } = useAuth();
  const perms = templatePermissions(user);
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["drafting-templates"],
    queryFn: () => draftingTemplatesApi.list(),
    // A check runs in the background: follow it while any template is being checked (FR-5).
    refetchInterval: (q) => (q.state.data?.some((t) => t.status.state === "checking") ? CHECK_POLL_MS : false),
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [key, setKey] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [run, setRun] = useState<CheckAllRun | null>(null);
  const { ask, dialog } = useLeaveGuard(dirty ? "You have unsaved changes to this template." : null);

  const all = useMemo(() => data ?? [], [data]);
  // The first template opens once and stays open: a refresh or a filter must never swap the editor under someone.
  useEffect(() => {
    if (key === null && all.length > 0) setKey(all[0].key);
  }, [key, all]);
  const selected = all.find((t) => t.key === key) ?? all[0] ?? null;

  const attention = all.filter((t) => needsAttention(t.status.state));
  const visible =
    filter === "all" ? all : filter === "attention" ? attention : all.filter((t) => t.status.state === filter);
  const toggle = (f: Filter) => setFilter(filter === f ? "all" : f);

  async function pick(next: string) {
    if (next === selected?.key) return;
    if (!(await ask())) return;
    setDirty(false);
    setKey(next);
  }

  return (
    <div className="space-y-4 px-6 pb-10 pt-5">
      {dialog}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">Templates</h1>
          <p className="mt-1 max-w-3xl text-[13px] text-slate-500">
            The standard paper each request is drafted from, and whether it passes the playbook that reviews it.
          </p>
        </div>
        {perms.canRun && all.length > 0 && <CheckAll templates={all} onStarted={setRun} />}
      </div>

      <CheckStatements />

      {run && <CheckAllProgress run={run} templates={all} onDismiss={() => setRun(null)} />}

      {isLoading ? (
        <SkeletonRows rows={5} />
      ) : error && !data ? (
        <LoadError error={error} what="the templates" onRetry={() => refetch()} />
      ) : all.length === 0 ? (
        <EmptyState
          icon={<FileText className="h-6 w-6" aria-hidden="true" />}
          title="No templates"
          description="This organisation has no active templates, so requests can't be drafted from our own paper. Ask an administrator to set the templates up."
        />
      ) : (
        <>
          <div role="group" aria-label="Filter templates by status" className="flex flex-wrap items-center gap-2">
            <FilterChip pressed={filter === "all"} onClick={() => setFilter("all")}>
              All ({all.length})
            </FilterChip>
            <FilterChip pressed={filter === "attention"} onClick={() => toggle("attention")}>
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              Needs attention ({attention.length})
            </FilterChip>
            {STATES.map((s) => {
              const n = all.filter((t) => t.status.state === s).length;
              if (n === 0 && filter !== s) return null;
              const Icon = STATUS_ICON[s];
              return (
                <FilterChip key={s} pressed={filter === s} onClick={() => toggle(s)}>
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {STATUS_WORDS[s]} ({n})
                </FilterChip>
              );
            })}
          </div>

          <div className="grid items-start gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
            {visible.length > 0 ? (
              <TemplateList templates={visible} selected={selected?.key ?? null} onSelect={pick} />
            ) : (
              <EmptyState
                icon={<CheckCircle2 className="h-6 w-6" aria-hidden="true" />}
                title={
                  filter === "attention" ? "No templates need attention" : `No templates with the status "${STATUS_WORDS[filter as TemplateStatusState]}"`
                }
                action={
                  <Button type="button" variant="outline" size="sm" onClick={() => setFilter("all")}>
                    Show all templates
                  </Button>
                }
              />
            )}
            {selected && (
              <TemplateDetail key={selected.key} templateKey={selected.key} summary={selected} perms={perms} onDirtyChange={setDirty} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
