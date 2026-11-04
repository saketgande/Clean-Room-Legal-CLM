"use client";

import { Fragment, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Lock, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { ErrorState, useConfirm } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { menuApi, orgUnitsApi, rolesApi, screenAccessApi, screensApi } from "@/lib/endpoints";
import type { ActionLevelCode, ScreenGrantResponse } from "@/lib/types";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import { titleCase } from "@/lib/utils";
import { AdBtn, Empty, KitStyles, Pill, Search, Seg } from "../admin/_admin-kit";
import { AssignScreenGrantModal } from "./_assign-screen-grant-modal";
import { LevelLadder } from "./_level-ladder";

type Group = "screen" | "role" | "none";
type State = "active" | "all" | "revoked";
const COLS = "minmax(0,1.6fr) minmax(0,1.3fr) minmax(0,1.3fr) 300px 44px";

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
  const [q, setQ] = useState("");
  const [state, setState] = useState<State>("active");
  const [group, setGroup] = useState<Group>("screen");
  const [shut, setShut] = useState<Set<string>>(new Set());
  const [assigning, setAssigning] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [levelBusyId, setLevelBusyId] = useState<string | null>(null);

  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: actionLevels } = useQuery({ queryKey: ["action-levels"], queryFn: () => menuApi.actionLevels() });
  const { data: screens } = useQuery({ queryKey: ["screens"], queryFn: () => screensApi.list() });
  const { data: orgUnits } = useQuery({ queryKey: ["org-units"], queryFn: () => orgUnitsApi.list() });
  const orgUnitRows = flattenWithIndent(orgUnits ?? []);
  const levels = useMemo(() => (actionLevels ?? []).slice().sort((a, b) => a.rank - b.rank).map((l) => l.code as ActionLevelCode), [actionLevels]);

  const filters = {
    ...(roleId ? { role_id: roleId } : {}),
    ...(screenId ? { screen_id: screenId } : {}),
    ...(orgUnitId ? { org_unit_id: orgUnitId } : {}),
  };
  const { data, isLoading, error } = useQuery({
    queryKey: ["screen-access-grants", roleId, screenId, orgUnitId],
    queryFn: () => screenAccessApi.grants(filters),
  });

  const all = data ?? [];
  const needle = q.trim().toLowerCase();
  const rows = all.filter((g) =>
    (state === "all" || (state === "active" ? g.is_active : !g.is_active)) &&
    (!needle || `${g.role_name} ${g.screen_name} ${g.org_unit_name ?? ""}`.toLowerCase().includes(needle)));
  const groups = useMemo(() => {
    if (group === "none") return [["", rows]] as [string, ScreenGrantResponse[]][];
    const m = new Map<string, ScreenGrantResponse[]>();
    rows.forEach((g) => { const k = group === "screen" ? g.screen_name : g.role_name; m.set(k, [...(m.get(k) ?? []), g]); });
    return Array.from(m).sort((a, b) => a[0].localeCompare(b[0]));
  }, [rows, group]);
  const nActive = all.filter((g) => g.is_active).length;
  const anyFilter = !!(roleId || screenId || orgUnitId || q);

  function refresh() { qc.invalidateQueries({ queryKey: ["screen-access-grants"] }); }

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

  const flip = (k: string) => setShut((s) => { const n = new Set(s); if (n.has(k)) n.delete(k); else n.add(k); return n; });

  return (
    <div className="sa">
      <KitStyles />
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="ad-bar">
        <Seg<State> label="Grant state" value={state} onChange={setState} items={[{ id: "active", label: "Active", n: nActive }, { id: "revoked", label: "Revoked", n: all.length - nActive }, { id: "all", label: "All", n: all.length }]} />
        <span className="sp" />
        <Seg<Group> label="Group by" value={group} onChange={setGroup} items={[{ id: "screen", label: "By screen" }, { id: "role", label: "By role" }, { id: "none", label: "Flat" }]} />
        {canManage && <AdBtn kind="primary" onClick={() => setAssigning(true)}><Plus size={14} />Assign grant</AdBtn>}
      </div>

      <div className="ad-bar sa-filters">
        <Search value={q} onChange={setQ} placeholder="Search role, screen or unit…" />
        <select aria-label="Filter by role" value={roleId} onChange={(e) => setRoleId(e.target.value)}>
          <option value="">All roles</option>
          {(roles ?? []).map((r) => <option key={r.id} value={r.id}>{titleCase(r.name)}</option>)}
        </select>
        <select aria-label="Filter by screen" value={screenId} onChange={(e) => setScreenId(e.target.value)}>
          <option value="">All screens</option>
          {(screens ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select aria-label="Filter by org unit" value={orgUnitId} onChange={(e) => setOrgUnitId(e.target.value)}>
          <option value="">All org units</option>
          {orgUnitRows.map(({ unit: u, depth }) => <option key={u.id} value={u.id}>{"  ".repeat(depth)}{u.name}</option>)}
        </select>
        {anyFilter && <button type="button" className="sa-clear" onClick={() => { setQ(""); setRoleId(""); setScreenId(""); setOrgUnitId(""); }}>Clear</button>}
        <span className="sp" />
        <span className="sa-count">{rows.length} {rows.length === 1 ? "grant" : "grants"}</span>
      </div>

      {isLoading ? (
        <div className="ad-load"><span className="ad-spin" /></div>
      ) : error ? (
        <ErrorState error={error} />
      ) : rows.length === 0 ? (
        <Empty icon={<ShieldCheck size={22} />} title={anyFilter ? "No grants match" : "No screen-access grants"} desc={anyFilter ? "Try clearing a filter." : "Assign a role's action level on a screen to get started."}
          action={canManage && !anyFilter ? <AdBtn kind="primary" onClick={() => setAssigning(true)}><Plus size={14} />Assign grant</AdBtn> : undefined} />
      ) : (
        <div className="ad-t" role="table" aria-label="Screen-access grants">
          <div className="ad-th sa-head" role="row" style={{ ["--cols" as string]: COLS }}>
            <span>{group === "role" ? "Screen" : "Role"}</span><span>{group === "role" ? "Role" : "Screen"}</span><span>Org unit</span><span>What they can do</span><span />
          </div>
          {groups.map(([name, list]) => {
            const closed = shut.has(name);
            return (
              <Fragment key={name || "all"}>
                {group !== "none" && (
                  <button type="button" className="sa-grp" aria-expanded={!closed} onClick={() => flip(name)}>
                    <ChevronDown size={15} className="chev" />
                    <b>{group === "role" ? titleCase(name) : name}</b>
                    <span>{list.length}</span>
                    <span className="sa-mini" aria-hidden>{levels.map((l) => <i key={l} className={`lv-${l}`} style={{ opacity: list.some((g) => g.max_action_level === l) ? 1 : 0.2 }} />)}</span>
                  </button>
                )}
                {!closed && list.map((g, i) => {
                  const locked = g.is_locked;
                  const lockMsg = locked ? "The built-in admin role's access to this screen cannot be changed." : undefined;
                  return (
                    <div key={g.id} className={`ad-tr sa-row${g.is_active ? "" : " off"}`} role="row" style={{ ["--cols" as string]: COLS, ["--i" as string]: Math.min(i, 14) }}>
                      <span className="cell sa-first">{group === "role" ? g.screen_name : titleCase(g.role_name)}</span>
                      <span className="cell">{group === "role" ? titleCase(g.role_name) : g.screen_name}</span>
                      <span className="cell sa-unit">{g.org_unit_name ?? <em>Organization-wide</em>}</span>
                      <span className="sa-lv">
                        {g.is_active && canManage ? (
                          <LevelLadder levels={levels} value={g.max_action_level as ActionLevelCode} disabled={locked || levelBusyId === g.id} title={lockMsg} onChange={(l) => updateLevel(g.id, l)} />
                        ) : (
                          <Pill tone={g.is_active ? "blue" : "slate"}>{g.is_active ? g.max_action_level : `Revoked · ${g.max_action_level}`}</Pill>
                        )}
                        {locked && <span className="sa-lock" title={lockMsg}><Lock size={12} />Locked</span>}
                      </span>
                      <span className="end">
                        {g.is_active && canManage && !locked && (
                          <AdBtn kind="danger" className="sm" disabled={busyId === g.id} aria-label={`Revoke ${g.role_name} on ${g.screen_name}`} title="Revoke this grant" onClick={() => revoke(g.id, `${g.role_name} @ ${g.screen_name}`)}><Trash2 size={13} /></AdBtn>
                        )}
                      </span>
                    </div>
                  );
                })}
              </Fragment>
            );
          })}
        </div>
      )}

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

const CSS = `
.sa{display:flex;flex-direction:column;gap:10px;max-width:1360px;margin:0 auto;color:var(--ink)}
.sa-filters select{height:34px;padding:0 28px 0 12px;border-radius:10px;border:0;background:var(--surface);color:var(--ink-2);font:600 12.5px inherit;font-family:inherit;box-shadow:inset 0 0 0 1px var(--border-strong);cursor:pointer;max-width:200px;transition:box-shadow .15s}
.sa-filters select:hover,.sa-filters select:focus{outline:none;box-shadow:inset 0 0 0 1.5px var(--accent)}
.sa-clear{height:30px;padding:0 10px;border:0;background:transparent;color:var(--accent);font:600 12.5px inherit;font-family:inherit;cursor:pointer;border-radius:8px} .sa-clear:hover{background:var(--accent-soft,var(--surface-2))}
.sa-count{font-size:12.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.sa .ad-t{display:block}
.sa-grp{display:flex;align-items:center;gap:9px;width:100%;padding:7px 12px;border:0;border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--accent) 5%,var(--surface));color:var(--ink);font:inherit;font-family:inherit;text-align:left;cursor:pointer;transition:background .15s;position:sticky;top:0;z-index:1}
.sa-grp:hover{background:color-mix(in srgb,var(--accent) 9%,var(--surface))} .sa-grp b{font-size:13px;font-weight:700} .sa-grp>span:not(.sa-mini){font-size:11.5px;color:var(--ink-3);padding:1px 8px;border-radius:99px;background:var(--surface)}
.sa-grp .chev{color:var(--ink-3);transition:transform .22s} .sa-grp[aria-expanded="false"] .chev{transform:rotate(-90deg)}
.sa-mini{margin-left:auto;display:inline-flex;gap:3px} .sa-mini i{width:14px;height:5px;border-radius:99px;background:var(--border-strong)}
.sa-mini .lv-VIEW,.lad .lv-VIEW.on{background:#7a8499} .sa-mini .lv-ADD,.lad .lv-ADD.on{background:#3b4aa0} .sa-mini .lv-EDIT,.lad .lv-EDIT.on{background:#c98512} .sa-mini .lv-DELETE,.lad .lv-DELETE.on{background:#c0392b}
.sa-row{animation:adrise .28s both;animation-delay:calc(var(--i)*20ms)} .sa-row.off{opacity:.55}
.sa-first{font-weight:650;color:var(--ink)} .sa-unit em{font-style:normal;color:var(--ink-3)}
.sa-lv{display:flex;align-items:center;gap:10px;min-width:0} .sa-lock{display:inline-flex;align-items:center;gap:4px;font-size:11.5px;color:var(--ink-3)}
.sa-head{position:static}
@media (max-width:900px){.sa .ad-th,.sa-row{grid-template-columns:minmax(0,1fr) 44px!important}.sa .ad-th>:nth-child(2),.sa .ad-th>:nth-child(3),.sa .ad-th>:nth-child(4),.sa-row>:nth-child(2),.sa-row>:nth-child(3),.sa-row>:nth-child(4){display:none}}
`;
