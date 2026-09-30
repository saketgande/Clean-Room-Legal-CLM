"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Input,
  MessageBar,
  Modal,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
  useConfirm,
} from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { can } from "@/lib/intake";
import { approvalChainsApi, rolesApi } from "@/lib/endpoints";
import { formatConditionText } from "@/lib/approval-chains";
import type {
  ChainApprovalMode,
  ChainStepResponse,
  ChainStepRuleResponse,
  ChainSubjectType,
  ConditionExpression,
  ConditionFieldCatalogResponse,
  ConditionFieldDescriptor,
  ConditionOperator,
  RoleResponse,
} from "@/lib/types";

// FR-1/FR-2/FR-3 admin surface: pick a subject type (each has its own active
// definition + fact surface, FR-22), review that definition's steps and
// per-step rules (base requirement vs condition-triggered), and add/edit/
// delete rules through a field/operator Select pair populated exclusively
// from `approvalChainsApi.fields(module)` so the UI can only ever compose one
// of the five whitelisted comparisons over a whitelisted field.
export function ConditionRulesTab() {
  const { user } = useAuth();
  const canManage = can(user, "approval_chain:manage");
  const qc = useQueryClient();

  const [subjectType, setSubjectType] = useState<ChainSubjectType>("contract");
  const [definitionId, setDefinitionId] = useState<string>("");
  const [ruleEditor, setRuleEditor] = useState<{
    step: ChainStepResponse;
    rule?: ChainStepRuleResponse;
  } | null>(null);
  const [deletingRuleId, setDeletingRuleId] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  const {
    data: definitions,
    isLoading: definitionsLoading,
    error: definitionsError,
  } = useQuery({
    queryKey: ["approval-chain-definitions", subjectType],
    queryFn: () => approvalChainsApi.definitions({ module: subjectType }),
  });

  const {
    data: fieldCatalog,
    isLoading: fieldsLoading,
    error: fieldsError,
  } = useQuery({
    queryKey: ["approval-chain-fields", subjectType],
    queryFn: () => approvalChainsApi.fields(subjectType),
  });

  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });

  const activeDefinitionId = useMemo(() => {
    if (definitionId) return definitionId;
    const active = (definitions ?? []).find((d) => d.is_active);
    return active?.id ?? (definitions ?? [])[0]?.id ?? "";
  }, [definitionId, definitions]);

  const selectedDefinition = (definitions ?? []).find(
    (d) => d.id === activeDefinitionId,
  );

  function refresh() {
    qc.invalidateQueries({ queryKey: ["approval-chain-definitions", subjectType] });
  }

  async function deleteRule(ruleId: string) {
    const ok = await confirm({
      title: "Delete condition rule",
      message: "Delete this rule? Materialized chain instances that already fired it are unaffected.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setDeletingRuleId(ruleId);
    try {
      await approvalChainsApi.deleteRule(ruleId);
      refresh();
    } catch {
      // surfaced by the modal's own toast pathway for create/edit; deletion
      // failures are rare (e.g. concurrent delete) so a best-effort refresh
      // still runs to reflect current server state.
      refresh();
    } finally {
      setDeletingRuleId(null);
    }
  }

  const isLoading = definitionsLoading || fieldsLoading;
  const error = definitionsError || fieldsError;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Condition rules</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <Field label="Subject type">
            <Select
              value={subjectType}
              onChange={(e) => {
                setSubjectType(e.target.value as ChainSubjectType);
                setDefinitionId("");
              }}
              className="max-w-xs"
            >
              <option value="contract">Contracts</option>
              <option value="intake_request">Intake requests</option>
            </Select>
          </Field>

          {isLoading ? (
            <CenterSpinner label="Loading approval-chain configuration…" />
          ) : error ? (
            <ErrorState error={error} />
          ) : (definitions ?? []).length === 0 ? (
            <EmptyState
              title="No approval-chain definition"
              description="No chain definition exists yet for this subject type."
            />
          ) : (
            <>
              {(definitions ?? []).length > 1 && (
                <Field label="Definition">
                  <Select
                    value={activeDefinitionId}
                    onChange={(e) => setDefinitionId(e.target.value)}
                    className="max-w-md"
                  >
                    {(definitions ?? []).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} (v{d.version}
                        {d.is_active ? ", active" : ""})
                      </option>
                    ))}
                  </Select>
                </Field>
              )}

              {selectedDefinition?.is_default_seeded && (
                <MessageBar intent="warning">
                  This is the default chain created at cutover — it requires one
                  approver from the approver role. Review it against the routing
                  rules you had configured.
                </MessageBar>
              )}

              {selectedDefinition &&
                selectedDefinition.steps
                  .slice()
                  .sort((a, b) => a.sequence_order - b.sequence_order)
                  .map((step) => (
                    <StepRulesCard
                      key={step.id}
                      step={step}
                      canManage={canManage}
                      deletingRuleId={deletingRuleId}
                      onAddRule={() => setRuleEditor({ step })}
                      onEditRule={(rule) => setRuleEditor({ step, rule })}
                      onDeleteRule={deleteRule}
                    />
                  ))}
            </>
          )}
        </CardBody>
      </Card>

      {ruleEditor && fieldCatalog && (
        <RuleModal
          step={ruleEditor.step}
          rule={ruleEditor.rule}
          fieldCatalog={fieldCatalog}
          roles={roles ?? []}
          onClose={() => setRuleEditor(null)}
          onSaved={() => {
            refresh();
            setRuleEditor(null);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}

const MODE_TONE: Record<ChainApprovalMode, "blue" | "violet"> = {
  sequential: "blue",
  parallel: "violet",
};

function StepRulesCard({
  step,
  canManage,
  deletingRuleId,
  onAddRule,
  onEditRule,
  onDeleteRule,
}: {
  step: ChainStepResponse;
  canManage: boolean;
  deletingRuleId: string | null;
  onAddRule: () => void;
  onEditRule: (rule: ChainStepRuleResponse) => void;
  onDeleteRule: (ruleId: string) => void;
}) {
  const rules = step.rules.slice().sort((a, b) => a.sequence_order - b.sequence_order);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-2">
          <CardTitle>{step.name}</CardTitle>
          <Badge tone={MODE_TONE[step.approval_mode]}>
            {step.approval_mode === "sequential" ? "Sequential" : "Parallel"}
          </Badge>
        </div>
        {canManage && (
          <Button size="sm" onClick={onAddRule}>
            <Plus className="h-3.5 w-3.5" />
            Add rule
          </Button>
        )}
      </CardHeader>
      <CardBody>
        {rules.length === 0 ? (
          <EmptyState
            title="No rules on this step"
            description="Add a base requirement or a condition rule to require an approver."
          />
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Type</TH>
                <TH>Condition</TH>
                <TH>Required role</TH>
                <TH>Sequence</TH>
                <TH>Active</TH>
                {canManage && <TH className="text-right">Actions</TH>}
              </tr>
            </THead>
            <tbody>
              {rules.map((rule) => (
                <TR key={rule.id}>
                  <TD>
                    <Badge tone={rule.is_base_requirement ? "slate" : "amber"}>
                      {rule.is_base_requirement ? "Base" : "Conditional"}
                    </Badge>
                  </TD>
                  <TD>
                    {rule.is_base_requirement
                      ? "—"
                      : rule.condition_expression
                        ? (rule.condition_text ?? formatConditionText(rule.condition_expression))
                        : "—"}
                  </TD>
                  <TD className="font-medium text-slate-900">
                    {rule.required_role_name}
                  </TD>
                  <TD>{rule.sequence_order}</TD>
                  <TD>
                    <Badge tone={rule.is_active ? "green" : "slate"}>
                      {rule.is_active ? "Active" : "Inactive"}
                    </Badge>
                  </TD>
                  {canManage && (
                    <TD className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onEditRule(rule)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          loading={deletingRuleId === rule.id}
                          onClick={() => onDeleteRule(rule.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TD>
                  )}
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}

// Casts a raw string form-value into the shape the selected field's declared
// type expects, per `ConditionFieldDescriptor.type`.
function castValue(
  raw: string,
  fieldType: ConditionFieldDescriptor["type"],
): string | number | boolean {
  if (fieldType === "number") return Number(raw);
  if (fieldType === "boolean") return raw === "true";
  return raw;
}

function RuleModal({
  step,
  rule,
  fieldCatalog,
  roles,
  onClose,
  onSaved,
}: {
  step: ChainStepResponse;
  rule?: ChainStepRuleResponse;
  fieldCatalog: ConditionFieldCatalogResponse;
  roles: RoleResponse[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [isBase, setIsBase] = useState(rule?.is_base_requirement ?? false);
  const [fieldName, setFieldName] = useState(
    rule?.condition_expression?.field ?? fieldCatalog.fields[0]?.name ?? "",
  );
  const [operator, setOperator] = useState<ConditionOperator>(
    rule?.condition_expression?.operator ?? fieldCatalog.operators[0] ?? "gt",
  );
  const [valueRaw, setValueRaw] = useState<string>(() => {
    const v = rule?.condition_expression?.value;
    if (v === undefined || v === null) return "";
    if (Array.isArray(v)) return v.join(", ");
    return String(v);
  });
  const [requiredRoleId, setRequiredRoleId] = useState(rule?.required_role_id ?? "");
  const [sequenceOrder, setSequenceOrder] = useState(rule?.sequence_order ?? 1);
  const [description, setDescription] = useState(rule?.description ?? "");
  const [isActive, setIsActive] = useState(rule?.is_active ?? true);
  const [busy, setBusy] = useState(false);

  const selectedField = fieldCatalog.fields.find((f) => f.name === fieldName);
  const fieldType = selectedField?.type ?? "string";
  const canSave = Boolean(requiredRoleId) && (isBase || (fieldName && operator));

  function buildConditionExpression(): ConditionExpression | null {
    if (isBase) return null;
    if (operator === "in") {
      const value = valueRaw
        .split(",")
        .map((part) => part.trim())
        .filter((part) => part.length > 0)
        .map((part) => castValue(part, fieldType));
      return { field: fieldName, operator, value };
    }
    return { field: fieldName, operator, value: castValue(valueRaw, fieldType) };
  }

  async function save() {
    setBusy(true);
    try {
      const condition_expression = buildConditionExpression();
      if (rule) {
        await approvalChainsApi.updateRule(rule.id, {
          condition_expression,
          required_role_id: requiredRoleId,
          sequence_order: sequenceOrder,
          description: description || null,
          is_active: isActive,
        });
        notify("Condition rule updated", "success");
      } else {
        await approvalChainsApi.createRule(step.id, {
          is_base_requirement: isBase,
          condition_expression,
          required_role_id: requiredRoleId,
          sequence_order: sequenceOrder,
          description: description || null,
          is_active: isActive,
        });
        notify("Condition rule created", "success");
      }
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={rule ? "Edit rule" : `Add rule — ${step.name}`}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>
            {rule ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <MessageBar intent="info">
          A compound rule (&quot;value over 1M AND jurisdiction is EU&quot;) is
          two separate rules requiring the same role.
        </MessageBar>

        {!rule && (
          <Field label="Requirement type">
            <Select
              value={isBase ? "base" : "conditional"}
              onChange={(e) => setIsBase(e.target.value === "base")}
            >
              <option value="conditional">Condition-triggered</option>
              <option value="base">Base requirement (always required)</option>
            </Select>
          </Field>
        )}

        {!isBase && (
          <>
            <Field label="Field">
              <Select
                value={fieldName}
                onChange={(e) => setFieldName(e.target.value)}
              >
                {fieldCatalog.fields.map((f) => (
                  <option key={f.name} value={f.name}>
                    {f.label}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Operator">
              <Select
                value={operator}
                onChange={(e) => setOperator(e.target.value as ConditionOperator)}
              >
                {fieldCatalog.operators.map((op) => (
                  <option key={op} value={op}>
                    {OPERATOR_SELECT_LABELS[op] ?? op}
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="Value"
              hint={
                operator === "in"
                  ? "Comma-separated list, e.g. EU, UK"
                  : undefined
              }
            >
              {fieldType === "boolean" ? (
                <Select value={valueRaw} onChange={(e) => setValueRaw(e.target.value)}>
                  <option value="">Select…</option>
                  <option value="true">True</option>
                  <option value="false">False</option>
                </Select>
              ) : (
                <Input
                  type={fieldType === "number" && operator !== "in" ? "number" : "text"}
                  value={valueRaw}
                  onChange={(e) => setValueRaw(e.target.value)}
                />
              )}
            </Field>
          </>
        )}

        <Field label="Required role">
          <Select
            value={requiredRoleId}
            onChange={(e) => setRequiredRoleId(e.target.value)}
          >
            <option value="">Select a role…</option>
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </Select>
        </Field>

        <Field label="Sequence order" hint="Position of this approver relative to others on the step">
          <Input
            type="number"
            min={1}
            value={sequenceOrder}
            onChange={(e) => setSequenceOrder(Number(e.target.value) || 1)}
          />
        </Field>

        <Field label="Description" hint="Optional">
          <Input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>

        <Field label="Status">
          <Select
            value={isActive ? "active" : "inactive"}
            onChange={(e) => setIsActive(e.target.value === "active")}
          >
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

const OPERATOR_SELECT_LABELS: Record<ConditionOperator, string> = {
  gt: "greater than",
  lt: "less than",
  eq: "equal to",
  in: "is one of",
  contains: "contains",
};
