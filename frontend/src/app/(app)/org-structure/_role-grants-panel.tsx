"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck } from "lucide-react";
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
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
  useConfirm,
} from "@/components/ui";
import { roleGrantsApi, usersApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { fmtDateTime } from "@/lib/utils";
import { AssignGrantModal } from "./_assign-grant-modal";

// FR-27 / AC-22: assign/revoke role grants at an org-unit scope, filterable
// by user.
export function RoleGrantsPanel() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [userId, setUserId] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["role-grants", userId],
    queryFn: () => roleGrantsApi.list(userId ? { user_id: userId } : {}),
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["role-grants"] });
  }

  async function revoke(id: string, label: string) {
    const ok = await confirm({
      title: "Revoke role grant",
      message: `Revoke the "${label}" grant? This stops granting access immediately.`,
      confirmLabel: "Revoke",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(id);
    try {
      await roleGrantsApi.revoke(id);
      refresh();
      notify("Role grant revoked", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Revoke failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Role grants</CardTitle>
          <Button size="sm" onClick={() => setAssigning(true)}>
            <Plus className="h-3.5 w-3.5" />
            Assign grant
          </Button>
        </CardHeader>
        <CardBody className="space-y-4">
          <Field label="Filter by user">
            <Select value={userId} onChange={(e) => setUserId(e.target.value)}>
              <option value="">All users</option>
              {(users ?? []).map((u) => (
                <option key={u.id} value={u.id}>
                  {u.full_name} · {u.email}
                </option>
              ))}
            </Select>
          </Field>

          {isLoading ? (
            <CenterSpinner label="Loading role grants…" />
          ) : error ? (
            <ErrorState error={error} />
          ) : (data ?? []).length === 0 ? (
            <EmptyState
              icon={<ShieldCheck className="h-6 w-6" />}
              title="No role grants"
              description="Assign a role at an org-unit scope to get started."
            />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>User</TH>
                  <TH>Role</TH>
                  <TH>Org unit</TH>
                  <TH>Validity</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Actions</TH>
                </tr>
              </THead>
              <tbody>
                {(data ?? []).map((g) => (
                  <TR key={g.id}>
                    <TD className="font-medium text-slate-900">
                      {g.user_label}
                    </TD>
                    <TD>{g.role_name}</TD>
                    <TD>{g.org_unit_name}</TD>
                    <TD className="text-xs text-slate-600">
                      {g.valid_from ? fmtDateTime(g.valid_from) : "—"}
                      {" → "}
                      {g.valid_to ? fmtDateTime(g.valid_to) : "No expiry"}
                    </TD>
                    <TD>
                      <Badge tone={g.is_active ? "green" : "slate"}>
                        {g.is_active ? "Active" : "Inactive"}
                      </Badge>
                    </TD>
                    <TD className="text-right">
                      {g.is_active && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            revoke(g.id, `${g.role_name} @ ${g.org_unit_name}`)
                          }
                          loading={busyId === g.id}
                        >
                          Revoke
                        </Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      {assigning && (
        <AssignGrantModal
          onClose={() => setAssigning(false)}
          onSaved={() => {
            refresh();
            setAssigning(false);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}
