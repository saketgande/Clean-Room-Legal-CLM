"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, ShieldCheck } from "lucide-react";
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
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import {
  menuApi,
  orgUnitsApi,
  rolesApi,
  screenAccessApi,
  screensApi,
} from "@/lib/endpoints";
import type { ActionLevelCode } from "@/lib/types";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import { AssignScreenGrantModal } from "./_assign-screen-grant-modal";

const LEVEL_TONE: Record<string, "slate" | "blue" | "green" | "amber" | "red"> = {
  VIEW: "slate",
  ADD: "blue",
  EDIT: "amber",
  DELETE: "red",
};

// FR-23: view existing role -> screen action-level grants, filterable by
// role/screen/org unit; assign a new one or revoke an existing one. FR-25's
// bootstrap-lock rows (the built-in admin role's DELETE grant on this very
// screen) are visibly, not silently, protected: their controls are disabled
// with an explanatory message rather than failing only on submit.
export function ScreenGrantsPanel() {
  const { user } = useAuth();
  const canManage = can(user, "screen_access:manage");
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const [roleId, setRoleId] = useState("");
  const [screenId, setScreenId] = useState("");
  const [orgUnitId, setOrgUnitId] = useState("");
  const [assigning, setAssigning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [levelBusyId, setLevelBusyId] = useState<string | null>(null);

  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: actionLevels } = useQuery({
    queryKey: ["action-levels"],
    queryFn: () => menuApi.actionLevels(),
  });
  const { data: screens } = useQuery({
    queryKey: ["screens"],
    queryFn: () => screensApi.list(),
  });
  const { data: orgUnits } = useQuery({
    queryKey: ["org-units"],
    queryFn: () => orgUnitsApi.list(),
  });
  const orgUnitRows = flattenWithIndent(orgUnits ?? []);

  const filters = {
    ...(roleId ? { role_id: roleId } : {}),
    ...(screenId ? { screen_id: screenId } : {}),
    ...(orgUnitId ? { org_unit_id: orgUnitId } : {}),
  };

  const { data, isLoading, error } = useQuery({
    queryKey: ["screen-access-grants", roleId, screenId, orgUnitId],
    queryFn: () => screenAccessApi.grants(filters),
  });

  function refresh() {
    qc.invalidateQueries({ queryKey: ["screen-access-grants"] });
  }

  async function updateLevel(id: string, level: ActionLevelCode) {
    setLevelBusyId(id);
    try {
      await screenAccessApi.updateGrant(id, { action_level: level });
      refresh();
      notify("Screen-access grant updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setLevelBusyId(null);
    }
  }

  async function revoke(id: string, label: string) {
    const ok = await confirm({
      title: "Revoke screen-access grant",
      message: `Revoke the "${label}" grant? This stops that access immediately.`,
      confirmLabel: "Revoke",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(id);
    try {
      await screenAccessApi.revokeGrant(id);
      refresh();
      notify("Screen-access grant revoked", "success");
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
          <CardTitle>Screen-access grants</CardTitle>
          {canManage && (
            <Button size="sm" onClick={() => setAssigning(true)}>
              <Plus className="h-3.5 w-3.5" />
              Assign grant
            </Button>
          )}
        </CardHeader>
        <CardBody className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Filter by role">
              <Select value={roleId} onChange={(e) => setRoleId(e.target.value)}>
                <option value="">All roles</option>
                {(roles ?? []).map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Filter by screen">
              <Select value={screenId} onChange={(e) => setScreenId(e.target.value)}>
                <option value="">All screens</option>
                {(screens ?? []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Filter by org unit">
              <Select
                value={orgUnitId}
                onChange={(e) => setOrgUnitId(e.target.value)}
              >
                <option value="">All org units</option>
                {orgUnitRows.map(({ unit: u, depth }) => (
                  <option key={u.id} value={u.id}>
                    {"  ".repeat(depth)}
                    {u.name}
                  </option>
                ))}
              </Select>
            </Field>
          </div>

          {isLoading ? (
            <CenterSpinner label="Loading screen-access grants…" />
          ) : error ? (
            <ErrorState error={error} />
          ) : (data ?? []).length === 0 ? (
            <EmptyState
              icon={<ShieldCheck className="h-6 w-6" />}
              title="No screen-access grants"
              description="Assign a role's action level on a screen to get started."
            />
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Role</TH>
                  <TH>Screen</TH>
                  <TH>Org unit</TH>
                  <TH>Max level</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Actions</TH>
                </tr>
              </THead>
              <tbody>
                {(data ?? []).map((g) => (
                  <TR key={g.id}>
                    <TD className="font-medium text-slate-900">{g.role_name}</TD>
                    <TD>{g.screen_name}</TD>
                    <TD>{g.org_unit_name ?? "Organization-wide"}</TD>
                    <TD>
                      {g.is_active && canManage ? (
                        <div className="flex items-center gap-2">
                          <Select
                            value={g.max_action_level}
                            disabled={g.is_locked || levelBusyId === g.id}
                            title={
                              g.is_locked
                                ? "The built-in admin role's access to this screen cannot be reduced."
                                : undefined
                            }
                            onChange={(e) =>
                              updateLevel(g.id, e.target.value as ActionLevelCode)
                            }
                            className="w-28"
                          >
                            {(actionLevels ?? [])
                              .slice()
                              .sort((a, b) => a.rank - b.rank)
                              .map((lvl) => (
                                <option key={lvl.id} value={lvl.code}>
                                  {lvl.code}
                                </option>
                              ))}
                          </Select>
                          {g.is_locked && (
                            <span
                              className="inline-flex items-center gap-1 text-[11px] text-slate-500"
                              title="The built-in admin role's access to this screen cannot be reduced."
                            >
                              <Lock className="h-3 w-3" />
                            </span>
                          )}
                        </div>
                      ) : (
                        <Badge tone={LEVEL_TONE[g.max_action_level] ?? "slate"}>
                          {g.max_action_level}
                        </Badge>
                      )}
                    </TD>
                    <TD>
                      <Badge tone={g.is_active ? "green" : "slate"}>
                        {g.is_active ? "Active" : "Revoked"}
                      </Badge>
                    </TD>
                    <TD className="text-right">
                      {g.is_active && canManage && (
                        <div className="flex items-center justify-end gap-2">
                          {g.is_locked && (
                            <span
                              className="inline-flex items-center gap-1 text-[11px] text-slate-500"
                              title="The built-in admin role's access to this screen cannot be revoked."
                            >
                              <Lock className="h-3 w-3" />
                              Locked
                            </span>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={g.is_locked}
                            title={
                              g.is_locked
                                ? "The built-in admin role's access to this screen cannot be revoked."
                                : undefined
                            }
                            onClick={() =>
                              revoke(g.id, `${g.role_name} @ ${g.screen_name}`)
                            }
                            loading={busyId === g.id}
                          >
                            Revoke
                          </Button>
                        </div>
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
        <AssignScreenGrantModal
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
