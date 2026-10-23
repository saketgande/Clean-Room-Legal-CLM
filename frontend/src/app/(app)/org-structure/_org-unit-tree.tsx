"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderTree, Move, Pencil, Plus, Trash2 } from "lucide-react";
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
  MessageBar,
  Table,
  TD,
  TH,
  THead,
  TR,
  useConfirm,
} from "@/components/ui";
import { orgUnitsApi } from "@/lib/endpoints";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import type { OrgUnitDeleteResponse, OrgUnitResponse } from "@/lib/types";
import { OrgUnitModal } from "./_org-unit-modal";

// FR-26 / AC-21 / AC-8: the org-unit tree screen — create, rename, re-parent
// ("move") and soft-delete org units, surfacing cycle/second-root 409s
// verbatim and the auto-re-parented children after a delete.
export function OrgUnitTree() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [modal, setModal] = useState<{
    mode: "create" | "rename" | "move";
    unit?: OrgUnitResponse;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [lastDelete, setLastDelete] = useState<OrgUnitDeleteResponse | null>(
    null,
  );

  const { data, isLoading, error } = useQuery({
    queryKey: ["org-units"],
    queryFn: () => orgUnitsApi.list(),
  });

  const units = data ?? [];
  const rows = flattenWithIndent(units);

  function refresh() {
    qc.invalidateQueries({ queryKey: ["org-units"] });
  }

  async function remove(u: OrgUnitResponse) {
    const ok = await confirm({
      title: "Delete org unit",
      message: `Delete "${u.name}"? Any direct children are automatically re-parented to its former parent.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(u.id);
    try {
      const result = await orgUnitsApi.remove(u.id);
      setLastDelete(result.reparented.length > 0 ? result : null);
      refresh();
      notify("Org unit deleted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading org units…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      {lastDelete && lastDelete.reparented.length > 0 && (
        <MessageBar intent="info">
          Re-parented{" "}
          {lastDelete.reparented.map((r) => r.name).join(", ")} to their
          former parent&apos;s parent as a consequence of the deletion.
        </MessageBar>
      )}
      <Card>
        <CardHeader>
          <CardTitle>Org units</CardTitle>
          <Button size="sm" onClick={() => setModal({ mode: "create" })}>
            <Plus className="h-3.5 w-3.5" />
            New org unit
          </Button>
        </CardHeader>
        <CardBody className="p-0">
          {rows.length === 0 ? (
            <div className="p-5">
              <EmptyState
                icon={<FolderTree className="h-6 w-6" />}
                title="No org units yet"
                description="Create the root ('Global') org unit to get started."
              />
            </div>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Name</TH>
                  <TH>Grants</TH>
                  <TH className="text-right">Actions</TH>
                </tr>
              </THead>
              <tbody>
                {rows.map(({ unit: u, depth }) => (
                  <TR key={u.id}>
                    <TD>
                      <div
                        className="flex items-center gap-2"
                        style={{ paddingLeft: depth * 20 }}
                      >
                        <span className="font-medium text-slate-900">
                          {u.name}
                        </span>
                        {u.is_root && <Badge tone="blue">Root</Badge>}
                      </div>
                    </TD>
                    <TD className="tabular-nums text-slate-600">
                      {u.active_grant_count}
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setModal({ mode: "rename", unit: u })}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          Rename
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setModal({ mode: "move", unit: u })}
                        >
                          <Move className="h-3.5 w-3.5" />
                          Move
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => remove(u)}
                          loading={busyId === u.id}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      {modal && (
        <OrgUnitModal
          mode={modal.mode}
          unit={modal.unit}
          units={units}
          onClose={() => setModal(null)}
          onSaved={() => {
            refresh();
            setModal(null);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}
