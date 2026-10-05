"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Field, Modal, Select } from "@/components/ui";
import { menuApi, orgUnitsApi, rolesApi, screenAccessApi, screensApi } from "@/lib/endpoints";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import type { ActionLevelCode } from "@/lib/types";

// FR-22/FR-23: assign a new role -> screen action-level grant, optionally
// scoped to an org unit (defaults to organization-wide when left blank).
export function AssignScreenGrantModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: screens } = useQuery({
    queryKey: ["screens"],
    queryFn: () => screensApi.list(),
  });
  const { data: orgUnits } = useQuery({
    queryKey: ["org-units"],
    queryFn: () => orgUnitsApi.list(),
  });
  const { data: actionLevels } = useQuery({
    queryKey: ["action-levels"],
    queryFn: () => menuApi.actionLevels(),
  });

  const [roleId, setRoleId] = useState("");
  const [screenId, setScreenId] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [actionLevel, setActionLevel] = useState<ActionLevelCode | "">("");
  const [busy, setBusy] = useState(false);

  const orgUnitRows = flattenWithIndent(orgUnits ?? []);
  const canSave = Boolean(roleId && screenId && actionLevel);

  async function save() {
    if (!canSave || !actionLevel) return;
    setBusy(true);
    try {
      await screenAccessApi.createGrant({
        role_id: roleId,
        screen_id: screenId,
        org_unit_id: orgUnitId || null,
        action_level: actionLevel,
      });
      notify("Screen-access grant created", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Grant failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Assign screen-access grant"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>
            Assign
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Role">
          <Select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
            <option value="">Select a role…</option>
            {(roles ?? []).map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Screen">
          <Select value={screenId} onChange={(e) => setScreenId(e.target.value)}>
            <option value="">Select a screen…</option>
            {(screens ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Action level">
          <Select
            value={actionLevel}
            onChange={(e) => setActionLevel(e.target.value as ActionLevelCode)}
          >
            <option value="">Select an action level…</option>
            {(actionLevels ?? []).map((l) => (
              <option key={l.id} value={l.code}>
                {l.code}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Org unit" hint="Optional — leave blank for organization-wide">
          <Select value={orgUnitId} onChange={(e) => setOrgUnitId(e.target.value)}>
            <option value="">Organization-wide</option>
            {orgUnitRows.map(({ unit: u, depth }) => (
              <option key={u.id} value={u.id}>
                {"  ".repeat(depth)}
                {u.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}
