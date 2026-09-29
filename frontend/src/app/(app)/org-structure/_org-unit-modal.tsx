"use client";

import { useState } from "react";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { orgUnitsApi } from "@/lib/endpoints";
import { flattenWithIndent, isDescendantOf } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import type { OrgUnitResponse } from "@/lib/types";

// Create / rename / re-parent ("move") org-unit modal (FR-1, FR-2, FR-26).
// The parent picker excludes the unit itself and every one of its descendants
// (client-side UX guard per plan.md — the server's cycle check, FR-3, stays
// authoritative).
export function OrgUnitModal({
  mode,
  unit,
  units,
  onClose,
  onSaved,
}: {
  mode: "create" | "rename" | "move";
  unit?: OrgUnitResponse; // required for rename/move
  units: OrgUnitResponse[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState(unit?.name ?? "");
  const [parentId, setParentId] = useState<string>(unit?.parent_id ?? "");
  const [busy, setBusy] = useState(false);

  const excluded = new Set(
    mode === "move" && unit
      ? units
          .filter((u) => isDescendantOf(units, u.id, unit.id))
          .map((u) => u.id)
      : [],
  );
  const parentOptions = flattenWithIndent(units).filter(
    (row) => !excluded.has(row.unit.id),
  );

  const title =
    mode === "create"
      ? "New org unit"
      : mode === "rename"
        ? `Rename ${unit?.name ?? ""}`
        : `Move ${unit?.name ?? ""}`;

  async function save() {
    setBusy(true);
    try {
      if (mode === "create") {
        await orgUnitsApi.create({
          name: name.trim(),
          parent_id: parentId || null,
        });
        notify("Org unit created", "success");
      } else if (unit && mode === "rename") {
        await orgUnitsApi.update(unit.id, { name: name.trim() });
        notify("Org unit renamed", "success");
      } else if (unit && mode === "move") {
        await orgUnitsApi.update(unit.id, { parent_id: parentId || null });
        notify("Org unit moved", "success");
      }
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const canSave =
    mode === "move" ? true : Boolean(name.trim());

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>
            {mode === "create" ? "Create" : "Save"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {mode !== "move" && (
          <Field label="Name">
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Region A"
            />
          </Field>
        )}
        {mode !== "rename" && (
          <Field
            label="Parent org unit"
            hint={
              mode === "create"
                ? "Leave unset only to create the organization's single root unit."
                : "Choosing no parent attempts to make this the root — rejected while another root exists."
            }
          >
            <Select
              value={parentId}
              onChange={(e) => setParentId(e.target.value)}
            >
              <option value="">(none — root)</option>
              {parentOptions.map(({ unit: u, depth }) => (
                <option key={u.id} value={u.id}>
                  {"  ".repeat(depth)}
                  {u.name}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>
    </Modal>
  );
}
