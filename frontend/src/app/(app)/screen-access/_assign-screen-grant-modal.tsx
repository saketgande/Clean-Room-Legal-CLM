"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Search } from "lucide-react";
import { Drawer } from "../admin/_roles-teams-forms";
import { titleCase } from "@/lib/utils";
import { menuApi, orgUnitsApi, rolesApi, screenAccessApi, screensApi } from "@/lib/endpoints";
import { flattenWithIndent } from "@/lib/org-tree";
import { useToast } from "@/components/toast";
import type { ActionLevelCode } from "@/lib/types";

const LEVEL_TEXT: Record<string, { t: string; d: string }> = {
  VIEW: { t: "View", d: "Open and read" },
  ADD: { t: "Add", d: "Read and add new" },
  EDIT: { t: "Edit", d: "Add and change" },
  DELETE: { t: "Delete", d: "Everything" },
};

// FR-22/FR-23: assign role -> screen action-level grants. One role and one level can go to
// many screens at once; screens the role already has (at that org unit) are shown, not offered.
export function AssignScreenGrantModal({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: screens } = useQuery({ queryKey: ["screens"], queryFn: () => screensApi.list() });
  const { data: orgUnits } = useQuery({ queryKey: ["org-units"], queryFn: () => orgUnitsApi.list() });
  const { data: actionLevels } = useQuery({ queryKey: ["action-levels"], queryFn: () => menuApi.actionLevels() });

  const [roleId, setRoleId] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [orgUnitId, setOrgUnitId] = useState("");
  const [level, setLevel] = useState<ActionLevelCode>("VIEW");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: have } = useQuery({
    queryKey: ["screen-access-grants", roleId, "", ""],
    queryFn: () => screenAccessApi.grants({ role_id: roleId }),
    enabled: !!roleId,
  });
  const existing = useMemo(() => {
    const m = new Map<string, string>();
    (have ?? []).filter((g) => g.is_active && (g.org_unit_id ?? "") === orgUnitId).forEach((g) => m.set(g.screen_id, g.max_action_level));
    return m;
  }, [have, orgUnitId]);

  const orgUnitRows = flattenWithIndent(orgUnits ?? []);
  const levels = (actionLevels ?? []).slice().sort((a, b) => a.rank - b.rank).map((l) => l.code as ActionLevelCode);
  const needle = q.trim().toLowerCase();
  const byModule = useMemo(() => {
    const m = new Map<string, NonNullable<typeof screens>>();
    (screens ?? []).filter((s) => !needle || s.name.toLowerCase().includes(needle) || s.module.toLowerCase().includes(needle))
      .forEach((s) => m.set(s.module, [...(m.get(s.module) ?? []), s]));
    return Array.from(m).sort((a, b) => a[0].localeCompare(b[0]));
  }, [screens, needle]);

  const role = (roles ?? []).find((r) => r.id === roleId);
  const chosen = [...picked].filter((id) => !existing.has(id));
  const canSave = Boolean(roleId && chosen.length && level);
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const setMany = (ids: string[], on: boolean) => setPicked((p) => { const n = new Set(p); ids.forEach((i) => (on ? n.add(i) : n.delete(i))); return n; });

  async function save() {
    if (!canSave) return;
    setBusy(true);
    const res = await Promise.allSettled(chosen.map((screen_id) => screenAccessApi.createGrant({ role_id: roleId, screen_id, org_unit_id: orgUnitId || null, action_level: level })));
    setBusy(false);
    const bad = res.filter((r) => r.status === "rejected");
    if (bad.length === 0) {
      notify(chosen.length === 1 ? "Screen-access grant created" : `${chosen.length} grants created`, "success");
      onSaved();
    } else {
      const first = (bad[0] as PromiseRejectedResult).reason;
      notify(`${chosen.length - bad.length} created, ${bad.length} failed: ${first instanceof Error ? first.message : "Grant failed"}`, "error");
      if (bad.length < chosen.length) onSaved();
    }
  }

  const names = chosen.map((id) => (screens ?? []).find((s) => s.id === id)?.name).filter(Boolean) as string[];
  const summary = canSave
    ? `${titleCase(role?.name ?? "")} can ${LEVEL_TEXT[level]?.t.toLowerCase()} on ${names.length > 2 ? `${names.length} screens` : names.join(" and ")}`
    : !roleId ? "Start by choosing a role" : "Tick the screens it should reach";

  return (
    <Drawer title="Assign screen access" subtitle="One role, one level, as many screens as you like." onClose={onClose}
      footer={
        <>
          <span className="rtd-note">{summary}</span>
          <button type="button" className="rtd-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="rtd-primary" onClick={save} disabled={!canSave || busy}>{busy ? "Assigning…" : chosen.length > 1 ? `Assign to ${chosen.length}` : "Assign"}</button>
        </>
      }>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />

      <section className="rtd-sec">
        <h3>Role</h3>
        <div className="rtd-chips">
          {(roles ?? []).map((r) => <button key={r.id} type="button" className={`rtd-chip${roleId === r.id ? " on" : ""}`} aria-pressed={roleId === r.id} onClick={() => { setRoleId(r.id); setPicked(new Set()); }}>{roleId === r.id && <Check size={12} />}{titleCase(r.name)}</button>)}
        </div>
      </section>

      <section className="rtd-sec">
        <h3>How far</h3>
        <div className="sg-levels" role="radiogroup" aria-label="Action level">
          {levels.map((l) => (
            <button key={l} type="button" role="radio" aria-checked={level === l} className={`lv-${l}${level === l ? " on" : ""}`} onClick={() => setLevel(l)}>
              <b>{LEVEL_TEXT[l]?.t ?? l}</b><span>{LEVEL_TEXT[l]?.d}</span>
            </button>
          ))}
        </div>
      </section>

      <section className="rtd-sec sg-fill">
        <div className="sg-sh">
          <h3>Screens <em>{chosen.length} picked</em></h3>
          <span className="sg-links">
            <button type="button" onClick={() => setMany((screens ?? []).filter((s) => !existing.has(s.id)).map((s) => s.id), true)} disabled={!roleId}>All</button>
            <button type="button" onClick={() => setPicked(new Set())} disabled={!picked.size}>None</button>
          </span>
        </div>
        <label className="sg-search"><Search size={14} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search screens…" aria-label="Search screens" /></label>
        <div className={`sg-list${roleId ? "" : " idle"}`}>
          {!roleId && <p className="sg-hint">Choose a role first. Screens it already has will be marked.</p>}
          {byModule.map(([mod, list]) => {
            const free = list.filter((s) => !existing.has(s.id));
            const all = free.length > 0 && free.every((s) => picked.has(s.id));
            return (
              <div key={mod} className="sg-mod">
                <button type="button" className="sg-mh" disabled={!roleId || free.length === 0} onClick={() => setMany(free.map((s) => s.id), !all)}>
                  <span className={`ck${all ? " on" : ""}`}>{all && <Check size={11} />}</span>{titleCase(mod)}<small>{list.length}</small>
                </button>
                {list.map((s) => {
                  const has = existing.get(s.id);
                  const on = picked.has(s.id);
                  return (
                    <button key={s.id} type="button" className={`sg-row${on ? " on" : ""}`} disabled={!roleId || !!has} aria-pressed={on} onClick={() => toggle(s.id)}>
                      <span className={`ck${on ? " on" : ""}`}>{on && <Check size={11} />}</span>
                      <span className="nm">{s.name}</span>
                      {has && <span className="has">Already {has.toLowerCase()}</span>}
                    </button>
                  );
                })}
              </div>
            );
          })}
          {roleId && byModule.length === 0 && <p className="sg-hint">No screen matches.</p>}
        </div>
      </section>

      <section className="rtd-sec">
        <h3>Where <em>optional</em></h3>
        <label className="rtd-f"><span className="sr-only">Org unit</span>
          <select value={orgUnitId} onChange={(e) => setOrgUnitId(e.target.value)}>
            <option value="">Organization-wide</option>
            {orgUnitRows.map(({ unit: u, depth }) => <option key={u.id} value={u.id}>{"  ".repeat(depth)}{u.name}</option>)}
          </select>
        </label>
      </section>
    </Drawer>
  );
}

const CSS = `
.sg-levels{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.sg-levels button{display:flex;flex-direction:column;gap:2px;align-items:flex-start;padding:10px 12px;border:0;border-radius:12px;background:var(--surface);color:var(--ink-2);text-align:left;cursor:pointer;font-family:inherit;box-shadow:inset 0 0 0 1px var(--border-strong);transition:background .18s,color .18s,box-shadow .18s,transform .12s}
.sg-levels button b{font-size:13.5px} .sg-levels button span{font-size:11.5px;color:var(--ink-3)}
.sg-levels button:hover{box-shadow:inset 0 0 0 1px var(--accent)} .sg-levels button:active{transform:scale(.97)}
.sg-levels button.on{color:#fff;box-shadow:0 8px 16px -10px rgba(0,0,0,.5)} .sg-levels button.on span{color:rgba(255,255,255,.85)}
.sg-levels .lv-VIEW.on{background:#7a8499} .sg-levels .lv-ADD.on{background:#3b4aa0} .sg-levels .lv-EDIT.on{background:#c98512} .sg-levels .lv-DELETE.on{background:#c0392b}
.sg-sh{display:flex;justify-content:space-between;align-items:center} .sg-sh h3 em{font-style:normal;font-weight:600;color:var(--accent);margin-left:8px;text-transform:none;letter-spacing:0}
.sg-links{display:flex;gap:4px} .sg-links button{height:26px;padding:0 10px;border:0;border-radius:8px;background:transparent;color:var(--accent);font:600 12.5px inherit;font-family:inherit;cursor:pointer} .sg-links button:hover:not(:disabled){background:var(--accent-soft,var(--surface-2))} .sg-links button:disabled{opacity:.4;cursor:default}
.sg-search{display:flex;align-items:center;gap:8px;height:38px;padding:0 12px;border-radius:11px;color:var(--ink-3);box-shadow:inset 0 0 0 1px var(--border-strong);background:var(--surface)} .sg-search:focus-within{box-shadow:inset 0 0 0 2px var(--accent)}
.sg-search input{flex:1;min-width:0;border:0;background:transparent;outline:none;color:var(--ink);font:500 14px inherit;font-family:inherit}
.sg-list{max-height:min(340px,38vh);overflow-y:auto;border-radius:12px;box-shadow:inset 0 0 0 1px var(--border);background:var(--surface)} .sg-list.idle{opacity:.65}
.sg-hint{margin:0;padding:14px;font-size:12.5px;color:var(--ink-3)}
.sg-mod+.sg-mod{border-top:1px solid var(--border)}
.sg-mh{display:flex;align-items:center;gap:9px;width:100%;padding:7px 12px;border:0;background:var(--surface-2);color:var(--ink-3);font:700 11px inherit;font-family:inherit;letter-spacing:.07em;text-transform:uppercase;cursor:pointer;position:sticky;top:0;z-index:1;text-align:left} .sg-mh small{margin-left:auto;font-weight:600;letter-spacing:0}
.sg-mh:hover:not(:disabled){color:var(--accent)} .sg-mh:disabled{cursor:default}
.sg-row{display:flex;align-items:center;gap:10px;width:100%;min-height:36px;padding:0 12px;border:0;background:transparent;color:var(--ink);font:500 13.5px inherit;font-family:inherit;text-align:left;cursor:pointer;transition:background .12s}
.sg-row:hover:not(:disabled){background:var(--surface-2)} .sg-row.on{background:var(--accent-soft,var(--surface-2))} .sg-row:disabled{cursor:default;color:var(--ink-3)}
.sg-row .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .sg-row .has{font-size:11px;font-weight:650;padding:1px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-3)}
.sg-list .ck{display:grid;place-items:center;flex:none;width:16px;height:16px;border-radius:5px;box-shadow:inset 0 0 0 1.5px var(--border-strong);color:#fff;transition:background .15s,box-shadow .15s} .sg-list .ck.on{background:var(--accent);box-shadow:none}
.sg-list button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
@media (max-width:560px){.sg-levels{grid-template-columns:1fr 1fr}}
`;
