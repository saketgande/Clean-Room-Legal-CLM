"use client";

// Approval-ladder builder — the routing-rule list + create modal. Extracted from
// approvals/page.tsx so both the Approvals page and the Legal Intake "Workflows"
// tab can mount the same builder (a page.tsx may not export components).

import { useEffect, useState } from "react";
import { ArrowDown, ArrowUp, GitBranch, Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  Select,
  SkeletonRows,
} from "@/components/ui";
import { approvalsApi } from "@/lib/endpoints";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ApprovalRoutingRule } from "@/lib/types";

/** Plain-language chips for a rule's match criteria — the "when" of a route.
 * Empty criteria means the rule is the catch-all fallback. */
function criteriaChips(criteria: Record<string, unknown>): string[] {
  const c = criteria || {};
  const num = (v: unknown) => Number(v).toLocaleString();
  const chips: string[] = [];
  if (c.min_value != null) chips.push(`Value ≥ ${num(c.min_value)}`);
  if (c.max_value != null) chips.push(`Value ≤ ${num(c.max_value)}`);
  const ct = c.contract_type ?? c.contract_types;
  if (ct) chips.push(`Type: ${(Array.isArray(ct) ? ct.join(", ") : String(ct)).toUpperCase()}`);
  const rb = c.risk_band ?? c.risk_bands;
  if (rb) chips.push(`Risk: ${titleCase(Array.isArray(rb) ? rb.join(", ") : String(rb))}`);
  if (c.jurisdiction) chips.push(`Jurisdiction: ${String(c.jurisdiction)}`);
  for (const [k, v] of Object.entries(c)) {
    if (["min_value", "max_value", "contract_type", "contract_types", "risk_band", "risk_bands", "jurisdiction"].includes(k)) continue;
    chips.push(`${titleCase(k.replace(/_/g, " "))}: ${String(v)}`);
  }
  return chips;
}

/** Rebuild the create/update payload from an existing rule (used by the active
 * toggle, which must re-send the whole rule). */
function ruleToPayload(rule: ApprovalRoutingRule): Record<string, unknown> {
  return {
    name: rule.name,
    priority: rule.priority,
    is_active: rule.is_active,
    criteria: rule.criteria,
    steps: rule.steps
      .slice()
      .sort((a, b) => a.step_order - b.step_order)
      .map((s) =>
        s.approver_group_id
          ? { approver_group_id: s.approver_group_id, mode: s.mode || "any" }
          : s.approver_user_id
            ? { approver_user_id: s.approver_user_id }
            : { approver_role: s.approver_role },
      ),
  };
}

/** The ordered approver rungs of a rule, each with its quorum mode. */
function chainRungs(rule: ApprovalRoutingRule): { label: string; mode: string }[] {
  if (rule.steps?.length) {
    return rule.steps
      .slice()
      .sort((a, b) => a.step_order - b.step_order)
      .map((s) => ({
        label:
          s.approver_group_name ||
          s.approver_user_name ||
          (s.approver_role ? titleCase(s.approver_role) : "?"),
        mode: s.mode || "any",
      }));
  }
  if (rule.approver_role) return [{ label: titleCase(rule.approver_role), mode: "any" }];
  return [{ label: rule.approver_user_id ?? "—", mode: "any" }];
}

export function RulesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  // null = closed; {rule: null} = create; {rule} = edit.
  const [modal, setModal] = useState<{ rule: ApprovalRoutingRule | null } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-routing-rules"],
    queryFn: approvalsApi.routingRules,
  });

  // Evaluated top-to-bottom by priority — show them in that order.
  const rules = (data ?? [])
    .slice()
    .sort((a, b) => (Number(a.priority) || 0) - (Number(b.priority) || 0));

  const refresh = () => qc.invalidateQueries({ queryKey: ["approval-routing-rules"] });

  async function toggleActive(rule: ApprovalRoutingRule) {
    setBusyId(rule.id);
    try {
      await approvalsApi.updateRoutingRule(rule.id, {
        ...ruleToPayload(rule),
        is_active: !rule.is_active,
      });
      refresh();
      notify(rule.is_active ? "Route paused" : "Route activated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(id: string) {
    setBusyId(id);
    try {
      await approvalsApi.deleteRoutingRule(id);
      refresh();
      notify("Route deleted", "success");
      setConfirmDelete(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-brand-200 bg-brand-50/60 px-3 py-2 text-xs text-slate-600">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0 text-brand-600" />
        <span>
          A request takes the <b className="font-medium text-slate-900">first route it matches</b>,
          checked top-to-bottom by priority. Each route decides who signs off — at every workflow
          Approval step, plus Delegation-of-Authority gates and the NDA fast-lane.
        </span>
        <a href="/workflow-builder" className="ml-auto shrink-0 font-medium text-brand-700 hover:underline">
          Manage workflows →
        </a>
      </div>
      <div className="flex justify-end">
        <Button onClick={() => setModal({ rule: null })}>
          <Plus className="h-4 w-4" />
          New route
        </Button>
      </div>

      {isLoading ? (
        <SkeletonRows rows={4} />
      ) : error ? (
        <ErrorState error={error} />
      ) : rules.length === 0 ? (
        <EmptyState
          icon={<GitBranch className="h-6 w-6" />}
          title="No routes yet"
          description="Create a route to send matching requests through an ordered chain of approvers."
          action={
            <Button onClick={() => setModal({ rule: null })}>
              <Plus className="h-4 w-4" />
              New route
            </Button>
          }
        />
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => {
            const chips = criteriaChips(rule.criteria);
            return (
              <Card key={rule.id} className={`p-4 ${rule.is_active ? "" : "opacity-60"}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex min-w-0 items-center gap-2">
                    <span
                      className="shrink-0 rounded-md bg-slate-100 px-1.5 py-0.5 text-[11px] font-semibold text-slate-500"
                      title="Priority — lower is checked first"
                    >
                      #{rule.priority}
                    </span>
                    <span className="truncate font-semibold text-slate-900">{rule.name}</span>
                    {!rule.is_active && <Badge tone="slate">Paused</Badge>}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={busyId === rule.id}
                      onClick={() => toggleActive(rule)}
                    >
                      {rule.is_active ? "Pause" : "Activate"}
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setModal({ rule })}>
                      <Pencil className="h-3.5 w-3.5" />
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      aria-label="Delete route"
                      onClick={() => setConfirmDelete(rule.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    When
                  </span>
                  {chips.length ? (
                    chips.map((c) => (
                      <span
                        key={c}
                        className="rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                      >
                        {c}
                      </span>
                    ))
                  ) : (
                    <span className="text-xs italic text-slate-500">Any request (fallback route)</span>
                  )}
                </div>

                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                    Then
                  </span>
                  {chainRungs(rule).map((r, i, arr) => (
                    <span key={i} className="flex items-center gap-1.5">
                      <span className="rounded-md bg-brand-50 px-2 py-0.5 text-xs font-medium text-brand-700">
                        {r.label}
                        {r.mode === "all" && (
                          <span className="ml-1 text-[10px] uppercase text-brand-500">· all</span>
                        )}
                      </span>
                      {i < arr.length - 1 && <span className="text-slate-300">→</span>}
                    </span>
                  ))}
                </div>

                {confirmDelete === rule.id && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-xs">
                    <span className="text-danger">
                      Delete this route? Existing approvals already in flight are unaffected.
                    </span>
                    <Button
                      size="sm"
                      variant="danger"
                      className="ml-auto"
                      loading={busyId === rule.id}
                      onClick={() => remove(rule.id)}
                    >
                      Delete
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setConfirmDelete(null)}>
                      Cancel
                    </Button>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <RuleModal
        open={!!modal}
        rule={modal?.rule ?? null}
        onClose={() => setModal(null)}
        onSaved={(verb) => {
          refresh();
          notify(`Route ${verb}`, "success");
          setModal(null);
        }}
      />
    </div>
  );
}

function RuleModal({
  open,
  rule,
  onClose,
  onSaved,
}: {
  open: boolean;
  rule: ApprovalRoutingRule | null;
  onClose: () => void;
  onSaved: (verb: "created" | "updated") => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [priority, setPriority] = useState("100");
  const [minValue, setMinValue] = useState("");
  const [maxValue, setMaxValue] = useState("");
  const [contractType, setContractType] = useState("");
  const [riskBand, setRiskBand] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [isActive, setIsActive] = useState(true);
  // Each step holds an encoded target: "group:<id>:<mode>" or "user:<id>" (or "").
  const [steps, setSteps] = useState<string[]>([""]);
  const [busy, setBusy] = useState(false);

  const { data: groups } = useQuery({
    queryKey: ["approval-groups"],
    queryFn: approvalsApi.groups,
    enabled: open,
  });
  const { data: people } = useQuery({
    queryKey: ["eligible-approvers"],
    queryFn: approvalsApi.eligibleApprovers,
    enabled: open,
  });

  // Load the rule into the form when opening for edit; clear it for create.
  useEffect(() => {
    if (!open) return;
    if (rule) {
      const c = rule.criteria || {};
      const str = (v: unknown) => (v == null ? "" : String(v));
      const first = (v: unknown) => (Array.isArray(v) ? v[0] : v);
      setName(rule.name);
      setPriority(rule.priority);
      setMinValue(str(c.min_value));
      setMaxValue(str(c.max_value));
      setContractType(str(first(c.contract_type ?? c.contract_types)));
      setRiskBand(str(first(c.risk_band ?? c.risk_bands)));
      setJurisdiction(str(c.jurisdiction));
      setIsActive(rule.is_active);
      const encoded = rule.steps
        .slice()
        .sort((a, b) => a.step_order - b.step_order)
        .map((s) =>
          s.approver_group_id
            ? `group:${s.approver_group_id}:${s.mode || "any"}`
            : s.approver_user_id
              ? `user:${s.approver_user_id}`
              : "",
        );
      setSteps(encoded.length ? encoded : [""]);
    } else {
      reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rule]);

  function reset() {
    setName("");
    setPriority("100");
    setMinValue("");
    setMaxValue("");
    setContractType("");
    setRiskBand("");
    setJurisdiction("");
    setIsActive(true);
    setSteps([""]);
  }

  function setStep(i: number, value: string) {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? value : s)));
  }
  function addStep() {
    setSteps((prev) => [...prev, ""]);
  }
  function removeStep(i: number) {
    setSteps((prev) => (prev.length === 1 ? prev : prev.filter((_, idx) => idx !== i)));
  }
  function move(i: number, dir: -1 | 1) {
    setSteps((prev) => {
      const next = [...prev];
      const j = i + dir;
      if (j < 0 || j >= next.length) return prev;
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  }

  async function submit() {
    const chosen = steps.filter(Boolean);
    if (!name.trim() || chosen.length === 0) return;
    setBusy(true);
    try {
      const criteria: Record<string, unknown> = {};
      if (minValue) criteria.min_value = Number(minValue);
      if (maxValue) criteria.max_value = Number(maxValue);
      if (contractType.trim()) criteria.contract_type = contractType.trim().toLowerCase();
      if (riskBand) criteria.risk_band = riskBand;
      if (jurisdiction.trim()) criteria.jurisdiction = jurisdiction.trim();
      const payload = {
        name: name.trim(),
        priority: String(Number(priority) || 0),
        is_active: isActive,
        criteria,
        steps: chosen.map((s) => {
          const [kind, id, mode] = s.split(":");
          return kind === "group"
            ? { approver_group_id: id, mode: mode || "any" }
            : { approver_user_id: id };
        }),
      };
      if (rule) await approvalsApi.updateRoutingRule(rule.id, payload);
      else await approvalsApi.createRoutingRule(payload);
      reset();
      onSaved(rule ? "updated" : "created");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const canSave = !!name.trim() && steps.some(Boolean);

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={rule ? "Edit route" : "New route"}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!canSave}>
            {rule ? "Save changes" : "Create route"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            value={name}
            placeholder="e.g. Standard contract chain"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field label="Priority" hint="Lower wins when rules overlap.">
            <Input
              type="number"
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
            />
          </Field>
          <Field label="Risk band" hint="Match this risk level.">
            <Select value={riskBand} onChange={(e) => setRiskBand(e.target.value)}>
              <option value="">Any risk</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </Select>
          </Field>
        </div>

        <div className="space-y-1.5">
          <span className="text-sm font-medium text-slate-700">Applies when… (all optional)</span>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Value ≥" hint="Lower bound.">
              <Input
                type="number"
                placeholder="Any value"
                value={minValue}
                onChange={(e) => setMinValue(e.target.value)}
              />
            </Field>
            <Field label="Value ≤" hint="Upper bound.">
              <Input
                type="number"
                placeholder="Any value"
                value={maxValue}
                onChange={(e) => setMaxValue(e.target.value)}
              />
            </Field>
            <Field label="Contract type" hint="e.g. nda, msa, dpa, vendor.">
              <Input
                placeholder="Any type"
                value={contractType}
                onChange={(e) => setContractType(e.target.value)}
              />
            </Field>
            <Field label="Jurisdiction" hint="e.g. US, EU, India.">
              <Input
                placeholder="Any jurisdiction"
                value={jurisdiction}
                onChange={(e) => setJurisdiction(e.target.value)}
              />
            </Field>
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium text-slate-700">Approval steps</span>
            <span className="text-xs text-slate-400">Approved in order, top to bottom</span>
          </div>
          {steps.map((value, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="w-12 shrink-0 text-xs font-medium text-slate-500">
                Step {i + 1}
              </span>
              <Select
                className="flex-1"
                // The mode lives in a 3rd segment ("group:id:all") that the
                // options below don't carry — match on kind:id only.
                value={value.startsWith("group:") ? value.split(":").slice(0, 2).join(":") : value}
                onChange={(e) => {
                  const picked = e.target.value;
                  // Preserve the chosen quorum mode when swapping the group.
                  if (picked.startsWith("group:")) {
                    setStep(i, `${picked}:${value.split(":")[2] || "any"}`);
                  } else {
                    setStep(i, picked);
                  }
                }}
              >
                <option value="">Select approver…</option>
                <optgroup label="Groups">
                  {(groups ?? []).map((g) => (
                    <option key={g.id} value={`group:${g.id}`}>
                      {g.name}
                      {g.members.length ? ` (${g.members.length})` : " (no members)"}
                    </option>
                  ))}
                </optgroup>
                <optgroup label="People">
                  {(people ?? []).map((u) => (
                    <option key={u.id} value={`user:${u.id}`}>
                      {u.full_name} — {u.email}
                    </option>
                  ))}
                </optgroup>
              </Select>
              {value.startsWith("group:") && (
                <Select
                  className="w-32 shrink-0"
                  value={value.split(":")[2] || "any"}
                  onChange={(e) =>
                    setStep(i, `group:${value.split(":")[1]}:${e.target.value}`)
                  }
                  title="Any one member can approve, or require all members (quorum)"
                >
                  <option value="any">any one</option>
                  <option value="all">all members</option>
                </Select>
              )}
              <button
                type="button"
                className="rounded p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                disabled={i === 0}
                onClick={() => move(i, -1)}
                aria-label="Move step up"
              >
                <ArrowUp className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="rounded p-1 text-slate-400 hover:text-slate-700 disabled:opacity-30"
                disabled={i === steps.length - 1}
                onClick={() => move(i, 1)}
                aria-label="Move step down"
              >
                <ArrowDown className="h-4 w-4" />
              </button>
              <button
                type="button"
                className="rounded p-1 text-slate-400 hover:text-danger disabled:opacity-30"
                disabled={steps.length === 1}
                onClick={() => removeStep(i)}
                aria-label="Remove step"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          <Button variant="outline" size="sm" onClick={addStep}>
            <Plus className="h-4 w-4" />
            Add step
          </Button>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          Active
        </label>
      </div>
    </Modal>
  );
}
