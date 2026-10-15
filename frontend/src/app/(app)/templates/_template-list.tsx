"use client";

// The Templates page's list (spec 006 FR-1, FR-11, FR-65): one keyboard-focusable button per template, showing
// without opening it the name, the status, the playbook that reviews it, what it drafts for and the wording it
// uses. Also exports StatusBadge, the status as words plus an icon plus a tone (never colour alone), and the
// words themselves for the page's status strip.

import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  CircleDashed,
  CircleHelp,
  Clock,
  Loader2,
  Shuffle,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui";
import { statusTone } from "@/lib/templates";
import type { DraftingTemplateSummary, TemplateStatus, TemplateStatusState } from "@/lib/types";
import { cn } from "@/lib/utils";

export const STATUS_WORDS: Record<TemplateStatusState, string> = {
  passes: "Passes",
  has_findings: "Has findings",
  checking: "Checking",
  out_of_date: "Out of date",
  partly_checked: "Partly checked",
  couldnt_check: "Couldn't check",
  never_checked: "Never checked",
  no_playbook: "No playbook",
};

export const STATUS_ICON: Record<TemplateStatusState, LucideIcon> = {
  passes: CheckCircle2,
  has_findings: AlertTriangle,
  checking: Loader2,
  out_of_date: Clock,
  partly_checked: CircleDashed,
  couldnt_check: XCircle,
  never_checked: CircleHelp,
  no_playbook: Ban,
};

export function StatusBadge({ status }: { status: Pick<TemplateStatus, "state" | "highest_severity"> }) {
  const Icon = STATUS_ICON[status.state];
  return (
    <Badge tone={statusTone(status.state, status.highest_severity)}>
      <Icon className={cn("h-3 w-3", status.state === "checking" && "animate-spin")} aria-hidden="true" />
      {STATUS_WORDS[status.state]}
    </Badge>
  );
}

/** "Results vary" (FR-11): shown beside the status, never instead of it. */
export function ResultsVaryBadge() {
  return (
    <span title="Identical wording has given different results.">
      <Badge tone="violet">
        <Shuffle className="h-3 w-3" aria-hidden="true" />
        Results vary
      </Badge>
    </span>
  );
}

/** The wording a template uses and its version: "Shipped wording", "Edited · v4", "Word file · v2 · MSA.docx". */
export function sourceText(t: Pick<DraftingTemplateSummary, "source" | "version" | "original_filename">): string {
  const file = t.original_filename ? ` · ${t.original_filename}` : "";
  switch (t.source) {
    case "default": return t.version > 0 ? `Shipped wording · v${t.version}` : "Shipped wording";
    case "text": return `Edited · v${t.version}`;
    case "docx": return `Word file · v${t.version}${file}`;
    case "pdf": return `PDF · v${t.version}${file}`;
    case "scan": return `Scan · v${t.version}${file}`;
  }
}

/** Who reviews the template: "Reviewed by NDA Playbook v2", or why nobody does. */
export function playbookText(t: Pick<DraftingTemplateSummary, "playbook">): string {
  const p = t.playbook;
  if (!p) return "No playbook reviews it";
  if (!p.available) return `Reviewed by ${p.name}, which is deleted or has no published version`;
  return `Reviewed by ${p.name}${p.version_number ? ` (version ${p.version_number})` : ""}`;
}

export function TemplateList({
  templates,
  selected,
  onSelect,
}: {
  templates: DraftingTemplateSummary[];
  selected: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <nav aria-label="Templates">
      <ul className="space-y-2">
        {templates.map((t) => {
          const on = t.key === selected;
          return (
            <li key={t.key}>
              <button
                type="button"
                aria-current={on ? "true" : undefined}
                onClick={() => onSelect(t.key)}
                className={cn(
                  "w-full space-y-1.5 rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35",
                  on
                    ? "border-brand-500 bg-brand-50"
                    : "border-slate-200 bg-slate-100 hover:border-slate-400",
                )}
              >
                <span className="block text-[13px] font-semibold text-slate-900">{t.name}</span>
                <span className="flex flex-wrap items-center gap-1.5">
                  <StatusBadge status={t.status} />
                  {t.status.results_vary && <ResultsVaryBadge />}
                </span>
                <span className="block text-xs text-slate-600">{t.status.reason}</span>
                <span className="block text-xs text-slate-500">{playbookText(t)}</span>
                <span className="block text-xs text-slate-500">
                  Drafts {t.binding.label} · {sourceText(t)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
