"use client";

// Icon + wording per workflow step type. (The ladder stepper that lived here was
// replaced by the shared lifecycle view, components/lifecycle-view.tsx.)

import { Bell, Bot, FileText, PenLine, ShieldCheck, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export const STEP_META: Record<string, { icon: LucideIcon; label: string; wait: string; running: string }> = {
  ai_task: { icon: Bot, label: "AI step", wait: "Needs your review", running: "Agent is working…" },
  human_task: { icon: Users, label: "Human task", wait: "Waiting on you", running: "In progress" },
  approval: { icon: ShieldCheck, label: "Approval", wait: "Awaiting sign-off", running: "Awaiting sign-off" },
  signature: { icon: PenLine, label: "Signature", wait: "Out for signature", running: "Out for signature" },
  clm_draft: { icon: FileText, label: "Draft", wait: "Awaiting document", running: "Drafting…" },
  counterparty: { icon: Users, label: "Counterparty", wait: "With counterparty", running: "With counterparty" },
  notify: { icon: Bell, label: "Notify", wait: "Notifying", running: "Notifying" },
};
export function stepMeta(type: string) { return STEP_META[type] ?? STEP_META.human_task; }
