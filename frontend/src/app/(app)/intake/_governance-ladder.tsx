"use client";

// Icon + wording per workflow step type. (The ladder stepper that lived here was
// replaced by the shared lifecycle view, components/lifecycle-view.tsx.)

import { Bell, Bot, FileText, PenLine, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { WorkflowRunStep } from "@/lib/types";

const STEP_META: Record<string, { icon: LucideIcon; label: string; wait: string; running: string }> = {
  ai_task: { icon: Bot, label: "AI step", wait: "Needs your review", running: "Agent is working…" },
  human_task: { icon: Users, label: "Human task", wait: "Waiting on you", running: "In progress" },
  approval: { icon: ShieldCheck, label: "Approval", wait: "Awaiting sign-off", running: "Awaiting sign-off" },
  signature: { icon: PenLine, label: "Signature", wait: "Out for signature", running: "Out for signature" },
  clm_draft: { icon: FileText, label: "Draft", wait: "Awaiting document", running: "Drafting…" },
  counterparty: { icon: Users, label: "Counterparty", wait: "With counterparty", running: "With counterparty" },
  notify: { icon: Bell, label: "Notify", wait: "Notifying", running: "Notifying" },
};
export function stepMeta(type: string) { return STEP_META[type] ?? STEP_META.human_task; }

/** What a waiting step says it is waiting for. "Waiting on you" used to be printed to every viewer, whoever the
 *  step was assigned to, so one page said "Waiting on you" and "Assigned to Erin Legal" together. */
export function waitLabel(step: Pick<WorkflowRunStep, "type" | "assignee_user_id" | "assignee_label" | "team_label">, meId?: string | null): string {
  const meta = stepMeta(step.type);
  if (step.type !== "human_task" && step.type !== "ai_task") return meta.wait;
  if (meId && step.assignee_user_id === meId) return meta.wait;
  const who = step.assignee_label || step.team_label;
  if (!who) return step.type === "ai_task" ? "Needs a person's review" : "Waiting on a person";
  return step.type === "ai_task" ? `Needs review by ${who}` : `Waiting on ${who}`;
}
