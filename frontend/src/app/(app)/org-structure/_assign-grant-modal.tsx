"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { orgUnitsApi, rolesApi, roleGrantsApi, usersApi } from "@/lib/endpoints";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";

// FR-27 / AC-22: assign a role to a user at a chosen org-unit scope, with an
// optional effective-from/effective-until window.
export function AssignGrantModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: orgUnits } = useQuery({
    queryKey: ["org-units"],
    queryFn: () => orgUnitsApi.list(),
  });

  const [userId, setUserId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [validFrom, setValidFrom] = useState("");
  const [validTo, setValidTo] = useState("");
  const [busy, setBusy] = useState(false);

  const orgUnitRows = flattenWithIndent(orgUnits ?? []);
  const canSave = Boolean(userId && roleId && orgUnitId);

  async function save() {
    if (!canSave) return;
    setBusy(true);
    try {
      await roleGrantsApi.create({
        user_id: userId,
        role_id: roleId,
        org_unit_id: orgUnitId,
        valid_from: validFrom ? new Date(validFrom).toISOString() : null,
        valid_to: validTo ? new Date(validTo).toISOString() : null,
      });
      notify("Role grant created", "success");
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
      title="Assign role grant"
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
        <Field label="User">
          <Select value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Select a user…</option>
            {(users ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name} · {u.email}
              </option>
            ))}
          </Select>
        </Field>
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
        <Field label="Org unit">
          <Select
            value={orgUnitId}
            onChange={(e) => setOrgUnitId(e.target.value)}
          >
            <option value="">Select an org unit…</option>
            {orgUnitRows.map(({ unit: u, depth }) => (
              <option key={u.id} value={u.id}>
                {"  ".repeat(depth)}
                {u.name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Effective from" hint="Optional">
            <Input
              type="date"
              value={validFrom}
              onChange={(e) => setValidFrom(e.target.value)}
            />
          </Field>
          <Field label="Effective until" hint="Optional">
            <Input
              type="date"
              value={validTo}
              onChange={(e) => setValidTo(e.target.value)}
            />
          </Field>
        </div>
      </div>
    </Modal>
  );
}
