"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { delegationsApi, usersApi } from "@/lib/endpoints";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";

// Only the delegator's own eligible role/org-unit combinations are offered
// here (FR-14 enforced at the UI layer) — the server remains authoritative.
export function DelegationModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { user } = useAuth();
  const { notify } = useToast();

  const { data: eligibility } = useQuery({
    queryKey: ["delegation-eligibility"],
    queryFn: () => delegationsApi.eligibility(),
    enabled: open,
  });
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
    enabled: open,
  });

  const [delegateUserId, setDelegateUserId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [busy, setBusy] = useState(false);

  const eligibleRoles = useMemo(() => {
    const seen = new Map<string, string>();
    for (const e of eligibility ?? []) seen.set(e.role_id, e.role_name);
    return [...seen.entries()];
  }, [eligibility]);

  const eligibleOrgUnits = useMemo(() => {
    const seen = new Map<string, string>();
    for (const e of eligibility ?? []) {
      // If a role is chosen, only offer org units eligible under that role.
      if (roleId && e.role_id !== roleId) continue;
      seen.set(e.org_unit_id, e.org_unit_name);
    }
    return [...seen.entries()];
  }, [eligibility, roleId]);

  const delegateCandidates = (users ?? []).filter((u) => u.id !== user?.id);

  const canSave = Boolean(delegateUserId && startDate && endDate);

  function reset() {
    setDelegateUserId("");
    setRoleId("");
    setOrgUnitId("");
    setStartDate("");
    setEndDate("");
  }

  async function submit() {
    if (!canSave) return;
    setBusy(true);
    try {
      await delegationsApi.create({
        delegate_user_id: delegateUserId,
        role_id: roleId || null,
        org_unit_id: orgUnitId || null,
        start_date: startDate,
        end_date: endDate,
      });
      notify("Delegation created", "success");
      reset();
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delegation failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="New delegation"
      footer={
        <>
          <Button
            variant="outline"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!canSave}>
            Create delegation
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Delegate to">
          <Select
            value={delegateUserId}
            onChange={(e) => setDelegateUserId(e.target.value)}
          >
            <option value="">Select a colleague…</option>
            {delegateCandidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name} · {u.email}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Role"
          hint="Optional — narrow the delegation to one of the roles you hold. Leave blank to delegate everything you hold."
        >
          <Select
            value={roleId}
            onChange={(e) => {
              setRoleId(e.target.value);
              setOrgUnitId("");
            }}
          >
            <option value="">All roles I hold</option>
            {eligibleRoles.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        </Field>
        <Field
          label="Org unit"
          hint="Optional — narrow the delegation to one org unit you hold eligibility at. Leave blank for every unit you hold."
        >
          <Select value={orgUnitId} onChange={(e) => setOrgUnitId(e.target.value)}>
            <option value="">All org units I hold</option>
            {eligibleOrgUnits.map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Start date">
            <Input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
            />
          </Field>
          <Field label="End date">
            <Input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
            />
          </Field>
        </div>
      </div>
    </Modal>
  );
}
