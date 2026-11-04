"use client";

// Roles and Teams as working tools, not lists: edit a role's permissions in place, see and
// change who holds it, compare roles side by side, manage a team's people without leaving the
// card, and look at the same data by person. Data and saving stay in page.tsx (callbacks).

import { useMemo, useState, type ReactNode } from "react";
import { Check, ChevronDown, Copy, Lock, Minus, Pencil, Plus, Search, Trash2, TriangleAlert, Users, X } from "lucide-react";
import type { IntakeTeam, PermissionInfo, RoleResponse, UserResponse } from "@/lib/types";
import { titleCase } from "@/lib/utils";
import { Switch, Tile } from "./_atoms";

export { Tile };

const Seg = <T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; n?: number }[]; label: string }) => (
  <div className="rt-seg" role="tablist" aria-label={label}>
    {items.map((it) => (
      <button key={it.id} type="button" role="tab" aria-selected={value === it.id} className={value === it.id ? "on" : ""} onClick={() => onChange(it.id)}>
        {it.label}{it.n !== undefined && <span className="n">{it.n}</span>}
      </button>
    ))}
  </div>
);

const SearchBox = ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) => (
  <label className="rt-search"><Search size={15} /><input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} />{value && <button type="button" aria-label="Clear search" onClick={() => onChange("")}><X size={14} /></button>}</label>
);

// ================================================================ Roles

type RoleView = "roles" | "compare" | "people";

export function RolesView({ roles, catalog, users, busyId, adminRole, onNew, onEdit, onDelete, onSavePerms, onSetUserRoles, peopleView }: {
  roles: RoleResponse[]; catalog: PermissionInfo[]; users: UserResponse[]; busyId: string | null; adminRole: string;
  onNew: (seed?: RoleResponse) => void; onEdit: (r: RoleResponse) => void; onDelete: (r: RoleResponse) => void;
  onSavePerms: (r: RoleResponse, perms: string[], rollup?: boolean) => Promise<void>;
  onSetUserRoles: (userId: string, roleIds: string[], activeId: string | null) => Promise<void>;
  peopleView: ReactNode;
}) {
  const [view, setView] = useState<RoleView>("roles");
  const [selId, setSelId] = useState<string | null>(null);
  const [rq, setRq] = useState("");
  const sel = roles.find((r) => r.id === selId) ?? roles[0];
  const groups = useMemo(() => Array.from(catalog.reduce((m, p) => m.set(p.group, [...(m.get(p.group) ?? []), p]), new Map<string, PermissionInfo[]>())), [catalog]);
  const people = new Set(users.filter((u) => u.roles.length).map((u) => u.id)).size;
  const noRole = users.filter((u) => !u.roles.length).length;
  const list = roles.filter((r) => !rq.trim() || `${r.name} ${r.description ?? ""}`.toLowerCase().includes(rq.trim().toLowerCase()));

  return (
    <div className="rt">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <header className="rt-head">
        <div>
          <h2>Roles &amp; permissions</h2>
          <p>A role is a bundle of permissions: what a person is <b>allowed to do</b>. Who does the work is a <b>Team</b>; how much someone may approve is <b>Authority</b>.</p>
        </div>
        <button type="button" className="rt-primary" onClick={() => onNew()}><Plus size={16} />New role</button>
      </header>

      <div className="rt-bar2">
        <Seg<RoleView> label="Roles view" value={view} onChange={setView} items={[{ id: "roles", label: "Roles", n: roles.length }, { id: "compare", label: "Compare" }, { id: "people", label: "People", n: users.length }]} />
        {noRole > 0 && <button type="button" className="rt-alert" onClick={() => setView("people")}><TriangleAlert size={14} />{noRole} {noRole === 1 ? "person has" : "people have"} no role</button>}
        <span className="rt-bar2-sp" />
        <span className="rt-dim">{people} of {users.length} people hold a role</span>
      </div>

      {view === "people" && peopleView}

      {view === "compare" && (
        <div className="rt-matrix-wrap">
          <table className="rt-matrix">
            <thead>
              <tr>
                <th>Area</th>
                {roles.map((r) => (
                  <th key={r.id}>
                    <button type="button" onClick={() => { setSelId(r.id); setView("roles"); }} title={`Open ${titleCase(r.name)}`}>
                      <Tile name={titleCase(r.name)} size={30} />
                      <span>{titleCase(r.name)}</span>
                      <small>{r.user_count} {r.user_count === 1 ? "person" : "people"}</small>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map(([g, perms], i) => (
                <tr key={g} style={{ ["--i" as string]: Math.min(i, 14) }}>
                  <th scope="row">{titleCase(g.replace(/_/g, " "))}<small>{perms.length}</small></th>
                  {roles.map((r) => {
                    const have = perms.filter((p) => r.permissions.includes(p.value)).length;
                    const full = have === perms.length;
                    return (
                      <td key={r.id} title={`${titleCase(r.name)}: ${have} of ${perms.length}`}>
                        <span className={`cell${full ? " full" : have ? " part" : ""}`}>{full ? <Check size={13} /> : have ? have : <Minus size={12} />}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="rt-dim">A tick means the role has every permission in that area; a number means some of them. Click a role to open it.</p>
        </div>
      )}

      {view === "roles" && (
        <div className="rt-split">
          <div className="rt-lcol">
            {roles.length > 5 && <SearchBox value={rq} onChange={setRq} placeholder="Search roles…" />}
            <ul className="rt-list" aria-label="Roles">
              {list.map((r, i) => (
                <li key={r.id} style={{ ["--i" as string]: i }}>
                  <button type="button" className={`rt-role${sel?.id === r.id ? " on" : ""}`} aria-pressed={sel?.id === r.id} onClick={() => setSelId(r.id)}>
                    <Tile name={titleCase(r.name)} />
                    <span className="rt-rmain">
                      <span className="rt-rname">{titleCase(r.name)}{r.is_builtin && <Lock size={12} aria-label="Built-in" />}</span>
                      <span className="rt-rmeta"><Users size={12} />{r.user_count} {r.user_count === 1 ? "person" : "people"} · {r.permissions.length} permissions</span>
                    </span>
                  </button>
                </li>
              ))}
              {list.length === 0 && <li className="rt-dim">No role matches.</li>}
            </ul>
          </div>
          {sel && (
            <RoleDetail key={sel.id} role={sel} groups={groups} users={users} roles={roles} adminRole={adminRole} busy={busyId === sel.id}
              onNew={onNew} onEdit={onEdit} onDelete={onDelete} onSavePerms={onSavePerms} onSetUserRoles={onSetUserRoles} />
          )}
        </div>
      )}
    </div>
  );
}

function RoleDetail({ role, groups, users, roles, adminRole, busy, onNew, onEdit, onDelete, onSavePerms, onSetUserRoles }: {
  role: RoleResponse; groups: [string, PermissionInfo[]][]; users: UserResponse[]; roles: RoleResponse[]; adminRole: string; busy: boolean;
  onNew: (seed?: RoleResponse) => void; onEdit: (r: RoleResponse) => void; onDelete: (r: RoleResponse) => void;
  onSavePerms: (r: RoleResponse, perms: string[], rollup?: boolean) => Promise<void>;
  onSetUserRoles: (userId: string, roleIds: string[], activeId: string | null) => Promise<void>;
}) {
  const [tab, setTab] = useState<"perms" | "people">("perms");
  const members = users.filter((u) => u.roles.includes(role.name));
  const locked = role.name === adminRole;
  return (
    <section className="rt-detail" aria-live="polite">
      <div className="rt-dtop">
        <Tile name={titleCase(role.name)} size={52} />
        <div className="rt-dtitle">
          <h3>{titleCase(role.name)}{role.is_builtin && <span className="rt-badge"><Lock size={11} />Built-in</span>}</h3>
          <p>{role.description || "No description."}</p>
        </div>
        <div className="rt-dact">
          <button type="button" className="rt-ghost" onClick={() => onNew(role)} title="Start a new role from this one"><Copy size={14} />Duplicate</button>
          <button type="button" className="rt-ghost" onClick={() => onEdit(role)}><Pencil size={14} />{locked ? "View" : "Details"}</button>
          {!role.is_builtin && (
            <button type="button" className="rt-ghost danger" disabled={busy} onClick={() => onDelete(role)}><Trash2 size={14} />Delete</button>
          )}
        </div>
      </div>

      <Seg<"perms" | "people"> label="Role sections" value={tab} onChange={setTab} items={[{ id: "perms", label: "What it can do", n: role.permissions.length }, { id: "people", label: "Who has it", n: members.length }]} />

      {tab === "perms" ? (
        <PermEditor key={role.permissions.slice().sort().join("|") + role.allows_hierarchy_rollup} role={role} groups={groups} locked={locked} onSave={onSavePerms} />
      ) : (
        <RoleMembers role={role} members={members} users={users} roles={roles} onSetUserRoles={onSetUserRoles} />
      )}
    </section>
  );
}

/** Edit a role's permissions where you read them; a bar appears only when something changed. */
function PermEditor({ role, groups, locked, onSave }: { role: RoleResponse; groups: [string, PermissionInfo[]][]; locked: boolean; onSave: (r: RoleResponse, perms: string[], rollup?: boolean) => Promise<void> }) {
  const [draft, setDraft] = useState(() => new Set(role.permissions));
  const [rollup, setRollup] = useState(role.allows_hierarchy_rollup);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [saving, setSaving] = useState(false);
  const needle = q.trim().toLowerCase();
  const added = [...draft].filter((p) => !role.permissions.includes(p)).length;
  const removed = role.permissions.filter((p) => !draft.has(p)).length;
  const dirty = added + removed > 0 || rollup !== role.allows_hierarchy_rollup;
  const flip = (g: string) => setOpen((s) => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n; });
  const setMany = (perms: PermissionInfo[], on: boolean) => setDraft((d) => { const n = new Set(d); perms.forEach((p) => (on ? n.add(p.value) : n.delete(p.value))); return n; });
  const toggle = (v: string) => setDraft((d) => { const n = new Set(d); if (n.has(v)) n.delete(v); else n.add(v); return n; });

  return (
    <div className="rt-pe">
      <div className="rt-pe-top">
        <SearchBox value={q} onChange={setQ} placeholder="Search permissions…" />
        <div className="rt-pe-roll">
          <div><b>Sees the team below</b><span>Also sees what people under them in the org chart can see</span></div>
          <Switch on={rollup} onChange={setRollup} label="Rolls up the org-unit hierarchy" disabled={locked} />
        </div>
      </div>
      {locked && <div className="rt-note">The built-in admin role always has every permission. It is your break-glass access.</div>}
      <div className="rt-groups">
        {groups.map(([g, perms]) => {
          const shown = needle ? perms.filter((p) => p.value.toLowerCase().includes(needle) || (p.description ?? "").toLowerCase().includes(needle)) : perms;
          if (!shown.length) return null;
          const have = perms.filter((p) => draft.has(p.value)).length;
          const isOpen = open.has(g) || !!needle;
          return (
            <div key={g} className={`rt-g${isOpen ? " open" : ""}`}>
              <div className="rt-gh">
                <button type="button" className="rt-gt" aria-expanded={isOpen} onClick={() => flip(g)}>
                  <ChevronDown size={16} className="chev" />
                  <b>{titleCase(g.replace(/_/g, " "))}</b>
                  <span className="n">{have}/{perms.length}</span>
                  <span className="bar"><i style={{ width: `${(have / perms.length) * 100}%` }} /></span>
                </button>
                {!locked && <Switch on={have === perms.length} onChange={(v) => setMany(perms, v)} label={`All ${g} permissions`} />}
              </div>
              {isOpen && (
                <ul className="rt-perms">
                  {shown.map((p) => (
                    <li key={p.value}>
                      <button type="button" className={`rt-perm${draft.has(p.value) ? " on" : ""}`} aria-pressed={draft.has(p.value)} disabled={locked} onClick={() => toggle(p.value)}>
                        <span className="ck">{draft.has(p.value) && <Check size={12} />}</span>
                        <span><code>{p.value}</code>{p.description && <small>{p.description}</small>}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
      {dirty && (
        <div className="rt-savebar" role="status">
          <span><b>Unsaved changes</b>{added > 0 && ` · ${added} added`}{removed > 0 && ` · ${removed} removed`}</span>
          <button type="button" className="rt-ghost" onClick={() => { setDraft(new Set(role.permissions)); setRollup(role.allows_hierarchy_rollup); }}>Discard</button>
          <button type="button" className="rt-primary sm" disabled={saving} onClick={async () => { setSaving(true); try { await onSave(role, [...draft], rollup); } finally { setSaving(false); } }}>{saving ? "Saving…" : "Save changes"}</button>
        </div>
      )}
    </div>
  );
}

function RoleMembers({ role, members, users, roles, onSetUserRoles }: { role: RoleResponse; members: UserResponse[]; users: UserResponse[]; roles: RoleResponse[]; onSetUserRoles: (userId: string, roleIds: string[], activeId: string | null) => Promise<void> }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const idOf = (name: string) => roles.find((r) => r.name === name)?.id;
  const idsOf = (u: UserResponse) => u.roles.map(idOf).filter(Boolean) as string[];
  const run = async (u: UserResponse, ids: string[], active: string | null) => { setBusy(u.id); try { await onSetUserRoles(u.id, ids, active); } finally { setBusy(null); } };
  const addable = users.filter((u) => !u.roles.includes(role.name) && (!q.trim() || `${u.full_name} ${u.email}`.toLowerCase().includes(q.trim().toLowerCase())));

  return (
    <div className="rt-mem">
      <h4>{members.length} {members.length === 1 ? "person has" : "people have"} this role</h4>
      {members.length === 0 ? <p className="rt-dim">Nobody yet. Add people below.</p> : (
        <ul className="rt-people">
          {members.map((u) => {
            const only = u.roles.length <= 1;
            return (
              <li key={u.id} className={busy === u.id ? "busy" : ""}>
                <Tile name={u.full_name} size={34} />
                <span className="nm">{u.full_name}<small>{u.email}</small></span>
                {u.active_role_name === role.name && <span className="rt-badge main">Main role</span>}
                {u.roles.length > 1 && <span className="rt-dim">+{u.roles.length - 1} more</span>}
                <button type="button" className="rt-x" disabled={only || busy === u.id} title={only ? "This is their only role. Give them another first." : "Remove this role from them"} aria-label={`Remove ${role.name} from ${u.full_name}`}
                  onClick={() => { const ids = idsOf(u).filter((id) => id !== role.id); run(u, ids, u.active_role_id === role.id ? ids[0] ?? null : u.active_role_id); }}><X size={15} /></button>
              </li>
            );
          })}
        </ul>
      )}
      <h4>Add people</h4>
      <SearchBox value={q} onChange={setQ} placeholder="Find someone to give this role…" />
      <ul className="rt-pick">
        {addable.slice(0, 6).map((u) => (
          <li key={u.id}>
            <button type="button" disabled={busy === u.id} onClick={() => run(u, [...idsOf(u), role.id], u.active_role_id ?? role.id)}>
              <Tile name={u.full_name} size={28} /><span className="nm">{u.full_name}<small>{u.roles.length ? u.roles.map(titleCase).join(", ") : "No role yet"}</small></span><Plus size={15} />
            </button>
          </li>
        ))}
        {addable.length === 0 && <li className="rt-dim">{q ? "No one matches." : "Everyone already has this role."}</li>}
        {addable.length > 6 && <li className="rt-dim">Search to narrow {addable.length - 6} more.</li>}
      </ul>
    </div>
  );
}

// ================================================================ Teams

type TeamFilter = "all" | "empty" | "inactive";
type TeamMember = { user_id: string; capacity: number };

const TEAM_COLS = "minmax(0,1.8fr) 120px 120px minmax(0,2fr) 52px 92px";

function TeamRow({ t, i, users, onEdit, onDelete, onMembers, onActive }: {
  t: IntakeTeam; i: number; users: UserResponse[]; onEdit: () => void; onDelete: () => void;
  onMembers: (t: IntakeTeam, m: TeamMember[]) => Promise<void>; onActive: (t: IntakeTeam, on: boolean) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const showAuth = t.members.some((x) => x.approve_limit);
  const specs: TeamMember[] = t.members.map((m) => ({ user_id: m.user_id, capacity: m.capacity }));
  const ids = new Set(specs.map((m) => m.user_id));
  const addable = users.filter((u) => !ids.has(u.id) && q.trim() && `${u.full_name} ${u.email}`.toLowerCase().includes(q.trim().toLowerCase()));
  const save = async (m: TeamMember[]) => { setBusy(true); try { await onMembers(t, m); } finally { setBusy(false); } };
  const load = t.members.reduce((n, m) => n + (m.open_count ?? 0), 0);
  const cap = t.members.reduce((n, m) => n + m.capacity, 0);
  const pct = cap > 0 ? Math.min(100, Math.round((load / cap) * 100)) : 0;
  const tags = t.expertise;

  return (
    <div className={`rt-trow${open ? " open" : ""}${t.active ? "" : " off"}${busy ? " busy" : ""}`} style={{ ["--i" as string]: Math.min(i, 14) }}>
      <div className="rt-tline" style={{ ["--cols" as string]: TEAM_COLS }}>
        <button type="button" className="rt-tmain" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Tile name={t.name} size={28} />
          <span className="nm">{t.name}<small>{t.is_default_intake ? "Default intake · " : ""}{t.strategy === "round_robin" ? "Taking turns" : "Fewest open first"}{t.overflow_team_name ? ` · overflow → ${t.overflow_team_name}` : ""}</small></span>
        </button>
        <span className="rt-stack">
          {t.members.slice(0, 4).map((m) => <Tile key={m.user_id} name={m.name ?? m.user_id} size={22} />)}
          {t.members.length > 4 && <span className="rt-more sm">+{t.members.length - 4}</span>}
          {t.members.length === 0 && <span className="p warn">No members</span>}
        </span>
        <span className="rt-loadc" title={`${load} open of ${cap || "no limit"}`}>
          {cap > 0 ? <><span className="rt-pbar"><i className={pct >= 80 ? "hot" : ""} style={{ width: `${pct}%` }} /></span><small>{load}/{cap}</small></> : <small>{load} open</small>}
        </span>
        <span className="rt-tagsline">
          {tags.length ? <>{tags.slice(0, 3).map((e) => <span key={e} className="tag g">{e}</span>)}{tags.length > 3 && <span className="rt-dim-s">+{tags.length - 3}</span>}</> : <span className="rt-dim-s">Only what a workflow names</span>}
        </span>
        <span><Switch on={t.active} onChange={(v) => { setBusy(true); onActive(t, v).finally(() => setBusy(false)); }} label={t.active ? `Pause ${t.name}` : `Activate ${t.name}`} /></span>
        <span className="rt-tact">
          <button type="button" className="rt-icon" aria-label={`Edit ${t.name}`} onClick={onEdit}><Pencil size={14} /></button>
          <button type="button" className="rt-icon danger" aria-label={`Delete ${t.name}`} onClick={onDelete}><Trash2 size={14} /></button>
          <button type="button" className="rt-icon" aria-label={open ? "Collapse" : "Expand"} aria-expanded={open} onClick={() => setOpen((o) => !o)}><ChevronDown size={15} className="rt-chev" /></button>
        </span>
      </div>
      {open && (
        <div className="rt-xp">
          <div className="rt-manage">
            <h4>People & workload</h4>
            {t.members.length === 0 && <div className="rt-warn"><TriangleAlert size={14} />No members: a workflow step using this team would stall.</div>}
            <ul className="rt-load" aria-label={`${t.name} members and workload`}>
              {t.members.map((m, k) => {
                const o = m.open_count ?? 0;
                const pc = m.capacity > 0 ? Math.min(100, Math.round((o / m.capacity) * 100)) : 0;
                return (
                  <li key={m.user_id}>
                    <span className="n">{m.name ?? m.user_id}{showAuth && <small className={m.approve_limit === "no authority" ? "warn" : ""}>Approves {m.approve_limit ?? "—"}</small>}</span>
                    {m.capacity > 0 ? <span className="rt-pbar" title={`${o} of ${m.capacity} open`}><i className={pc >= 80 ? "hot" : ""} style={{ width: `${pc}%` }} /></span> : <span className="rt-dim">no limit</span>}
                    <span className="rt-stepper" role="group" aria-label={`Capacity for ${m.name ?? m.user_id}`}>
                      <button type="button" aria-label="Less capacity" disabled={busy} onClick={() => save(specs.map((x, j) => (j === k ? { ...x, capacity: Math.max(0, x.capacity - 1) } : x)))}><Minus size={13} /></button>
                      <output>{o}/{m.capacity || "∞"}</output>
                      <button type="button" aria-label="More capacity" disabled={busy} onClick={() => save(specs.map((x, j) => (j === k ? { ...x, capacity: x.capacity + 1 } : x)))}><Plus size={13} /></button>
                    </span>
                    <button type="button" className="rt-x" aria-label={`Remove ${m.name ?? m.user_id}`} disabled={busy} onClick={() => save(specs.filter((_, j) => j !== k))}><X size={14} /></button>
                  </li>
                );
              })}
            </ul>
            <SearchBox value={q} onChange={setQ} placeholder="Add a person…" />
            {q.trim() && (
              <ul className="rt-pick">
                {addable.slice(0, 5).map((u) => (
                  <li key={u.id}><button type="button" disabled={busy} onClick={() => { setQ(""); save([...specs, { user_id: u.id, capacity: 8 }]); }}><Tile name={u.full_name} size={24} /><span className="nm">{u.full_name}<small>{u.email}</small></span><Plus size={14} /></button></li>
                ))}
                {addable.length === 0 && <li className="rt-dim">No one else matches.</li>}
              </ul>
            )}
          </div>
          <div className="rt-aside">
            <h4>Handles</h4>
            <div className="rt-tagsline wrapall">{t.expertise.length ? t.expertise.map((e) => <span key={e} className="tag g">{e}</span>) : <span className="rt-dim-s">Nothing tagged</span>}</div>
            <h4>Serves</h4>
            <div className="rt-tagsline wrapall">{t.departments.length ? t.departments.map((d) => <span key={d} className="tag b">{d}</span>) : <span className="rt-dim-s">All business units</span>}</div>
            <h4>Where it works ({t.used_in.length})</h4>
            {t.used_in.length === 0 ? <span className="rt-dim-s">Not used by any workflow yet</span> : (
              <ul className="rt-usedlist">
                {t.used_in.map((u, k) => <li key={k}><span className="st">{u.stage ? titleCase(u.stage) : u.kind === "intake" ? "Intake" : "·"}</span><span>{u.where}</span></li>)}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function TeamsView({ teams, users, onNew, onEdit, onDelete, onMembers, onActive }: {
  teams: IntakeTeam[]; users: UserResponse[]; onNew: () => void; onEdit: (t: IntakeTeam) => void; onDelete: (t: IntakeTeam) => void;
  onMembers: (t: IntakeTeam, m: TeamMember[]) => Promise<void>; onActive: (t: IntakeTeam, on: boolean) => Promise<void>;
}) {
  const [view, setView] = useState<"teams" | "people">("teams");
  const [filter, setFilter] = useState<TeamFilter>("all");
  const [q, setQ] = useState("");
  const [pq, setPq] = useState("");
  const onTeam = new Set(teams.flatMap((t) => t.members.map((m) => m.user_id)));
  const empty = teams.filter((t) => !t.members.length).length;
  const idle = users.filter((u) => !onTeam.has(u.id));
  const def = teams.find((t) => t.is_default_intake);
  const needle = q.trim().toLowerCase();
  const shown = teams.filter((t) =>
    (filter === "all" || (filter === "empty" ? !t.members.length : !t.active)) &&
    (!needle || `${t.name} ${t.expertise.join(" ")} ${t.departments.join(" ")}`.toLowerCase().includes(needle)));
  const pneedle = pq.trim().toLowerCase();
  const rows = users.filter((u) => !pneedle || `${u.full_name} ${u.email}`.toLowerCase().includes(pneedle));

  return (
    <div className="rt">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <header className="rt-head">
        <div>
          <h2>Teams</h2>
          <p>Teams do the work. A new request goes to the team whose <b>expertise</b> covers it, else the <b>default intake team</b>; every workflow step names the team that does it. <b>Roles</b> decide what a person may do; <b>Authority</b> limits what they may approve.</p>
        </div>
        <button type="button" className="rt-primary" onClick={onNew}><Plus size={16} />New team</button>
      </header>

      <div className="rt-bar2">
        <Seg<"teams" | "people"> label="Teams view" value={view} onChange={setView} items={[{ id: "teams", label: "Teams", n: teams.length }, { id: "people", label: "People", n: users.length }]} />
        {empty > 0 && <button type="button" className="rt-alert" onClick={() => { setView("teams"); setFilter("empty"); }}><TriangleAlert size={14} />{empty} without members</button>}
        {idle.length > 0 && <button type="button" className="rt-alert soft" onClick={() => setView("people")}>{idle.length} not on a team</button>}
        <span className="rt-bar2-sp" />
        <span className="rt-dim">Default intake: <b>{def?.name ?? "none set"}</b></span>
      </div>

      {view === "teams" ? (
        <>
          <div className="rt-tools">
            <SearchBox value={q} onChange={setQ} placeholder="Search teams, expertise or departments…" />
            <div className="rt-fchips" role="group" aria-label="Filter teams">
              {([["all", "All"], ["empty", "No members"], ["inactive", "Paused"]] as const).map(([k, l]) => (
                <button key={k} type="button" aria-pressed={filter === k} className={filter === k ? "on" : ""} onClick={() => setFilter(k)}>{l}</button>
              ))}
            </div>
          </div>
          {shown.length === 0 ? (
            <div className="rt-empty"><Users size={28} /><b>{teams.length ? "No team matches" : "No teams yet"}</b><span>{teams.length ? "Try a different search or filter." : "Create a team and tag it with the matter types it handles and the business units it serves."}</span></div>
          ) : (
            <div className="rt-ttable" role="table" aria-label="Teams">
              <div className="rt-thead" style={{ ["--cols" as string]: TEAM_COLS }} role="row"><span>Team</span><span>People</span><span>Load</span><span>Handles</span><span>Active</span><span /></div>
              {shown.map((t, i) => <TeamRow key={t.id} t={t} i={i} users={users} onEdit={() => onEdit(t)} onDelete={() => onDelete(t)} onMembers={onMembers} onActive={onActive} />)}
            </div>
          )}
        </>
      ) : (
        <section className="rt-pview">
          <div className="rt-tools"><SearchBox value={pq} onChange={setPq} placeholder="Search people…" /></div>
          <ul className="rt-prows">
            {rows.map((u, i) => {
              const mine = teams.filter((t) => t.members.some((m) => m.user_id === u.id));
              const add = teams.filter((t) => !mine.includes(t));
              return (
                <li key={u.id} style={{ ["--i" as string]: Math.min(i, 12) }}>
                  <Tile name={u.full_name} size={36} />
                  <span className="nm">{u.full_name}<small>{u.email}</small></span>
                  <span className="tm">
                    {mine.length ? mine.map((t) => {
                      const m = t.members.find((x) => x.user_id === u.id)!;
                      return <span key={t.id} className="pill">{t.name}<em>{m.open_count ?? 0}/{m.capacity || "∞"}</em></span>;
                    }) : <span className="pill none">Not on a team</span>}
                  </span>
                  <label className="rt-add"><span className="sr-only">Add {u.full_name} to a team</span>
                    <select value="" onChange={(e) => { const t = teams.find((x) => x.id === e.target.value); if (t) onMembers(t, [...t.members.map((m) => ({ user_id: m.user_id, capacity: m.capacity })), { user_id: u.id, capacity: 8 }]); }}>
                      <option value="">+ Add to team</option>
                      {add.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                    </select>
                  </label>
                </li>
              );
            })}
            {rows.length === 0 && <li className="rt-dim">No one matches.</li>}
          </ul>
        </section>
      )}
    </div>
  );
}

const CSS = `
@keyframes rtrise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@keyframes rtgrow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes rtpop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
.rt{display:flex;flex-direction:column;gap:16px;color:var(--ink)}
.rt button,.rt select{cursor:pointer;font-family:inherit} .rt button:disabled{cursor:default}
.rt button:focus-visible,.rt select:focus-visible,.rt input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.rt-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.rt-head h2{font-size:20px;font-weight:700;margin:0}
.rt-head p{margin:4px 0 0;max-width:680px;font-size:13.5px;line-height:1.55;color:var(--ink-3)} .rt-head p b{color:var(--ink-2)}
.rt-primary{display:inline-flex;align-items:center;gap:7px;height:40px;padding:0 16px;border-radius:12px;background:var(--accent);color:var(--accent-ink,#fff);font-weight:650;font-size:13.5px;box-shadow:0 8px 18px -10px var(--accent);transition:transform .15s,box-shadow .2s,opacity .2s}
.rt-primary.sm{height:34px;padding:0 14px;font-size:13px}
.rt-primary:hover:not(:disabled){transform:translateY(-1px)} .rt-primary:active:not(:disabled){transform:scale(.97)} .rt-primary:disabled{opacity:.5}
.rt-ghost{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 12px;border-radius:10px;font-size:12.5px;font-weight:600;color:var(--ink-2);box-shadow:inset 0 0 0 1px var(--border-strong);background:transparent;transition:background .15s,color .15s,box-shadow .15s}
.rt-ghost:hover:not(:disabled){background:var(--surface-2);color:var(--ink)} .rt-ghost.danger:hover:not(:disabled){color:var(--crit,#c0392b);box-shadow:inset 0 0 0 1px var(--crit,#c0392b);background:transparent} .rt-ghost:disabled{opacity:.5}
.rt-bar2{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.rt-bar2-sp{flex:1}
.rt-seg{display:inline-flex;gap:2px;padding:3px;border-radius:12px;background:var(--surface-2)}
.rt-seg button{display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 14px;border-radius:9px;font-size:13px;font-weight:600;color:var(--ink-3);background:transparent;transition:background .2s,color .2s,box-shadow .2s}
.rt-seg button:hover{color:var(--ink)} .rt-seg button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 3px rgba(20,26,40,.16),0 0 0 1px var(--border)}
.rt-seg .n{padding:1px 7px;border-radius:99px;font-size:11px;background:var(--surface-2);color:var(--ink-3);font-variant-numeric:tabular-nums} .rt-seg .on .n{background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.rt-alert{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 12px;border-radius:99px;font-size:12.5px;font-weight:600;background:#fdecea;color:#a3271a;transition:transform .15s} .rt-alert:hover{transform:translateY(-1px)} .rt-alert.soft{background:#fdf0d5;color:#8a5a00}
.rt-dim{color:var(--ink-3);font-size:12.5px} .rt-dim b{color:var(--ink-2)}
.rt-tile{display:grid;place-items:center;flex:none;border-radius:30%;font-weight:700;letter-spacing:.02em;user-select:none}
.rt-badge{display:inline-flex;align-items:center;gap:4px;margin-left:8px;padding:2px 9px;border-radius:99px;font-size:11px;font-weight:600;background:var(--surface-2);color:var(--ink-3);vertical-align:middle} .rt-badge.main{margin:0;background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.rt-search{display:flex;align-items:center;gap:8px;padding:0 6px 0 12px;height:40px;border-radius:11px;box-shadow:inset 0 0 0 1px var(--border-strong);color:var(--ink-3);background:var(--surface);min-width:0;flex:1;transition:box-shadow .15s}
.rt-search:focus-within{box-shadow:inset 0 0 0 2px var(--accent)} .rt-search input{flex:1;min-width:0;height:100%;border:0;background:transparent;color:var(--ink);font:500 14px inherit;font-family:inherit;outline:none!important}
.rt-search button{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:transparent;color:var(--ink-3)} .rt-search button:hover{background:var(--surface-2)}
.rt-pbar{display:block;height:5px;border-radius:99px;background:var(--surface-2);overflow:hidden}
.rt-pbar i{display:block;height:100%;border-radius:99px;background:var(--accent);transform-origin:left;animation:rtgrow .6s cubic-bezier(.2,.8,.2,1) both} .rt-pbar i.hot{background:var(--warn,#d98a1b)}
.rt-x{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;color:var(--ink-3);background:transparent;transition:background .15s,color .15s;flex:none} .rt-x:hover:not(:disabled){background:#fdecea;color:#a3271a} .rt-x:disabled{opacity:.35}

/* roles */
.rt-split{display:grid;grid-template-columns:minmax(250px,320px) 1fr;gap:16px;align-items:start}
.rt-lcol{display:flex;flex-direction:column;gap:10px;position:sticky;top:12px}
.rt-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.rt-list li{animation:rtrise .35s both;animation-delay:calc(var(--i)*40ms)}
.rt-role{display:flex;gap:12px;align-items:center;width:100%;text-align:left;padding:12px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);transition:box-shadow .2s,transform .2s,background .2s}
.rt-role:hover{transform:translateX(3px);box-shadow:inset 0 0 0 1px var(--border-strong)} .rt-role.on{box-shadow:inset 0 0 0 2px var(--accent);background:var(--accent-soft,var(--surface))}
.rt-rmain{display:flex;flex-direction:column;gap:4px;min-width:0;flex:1}
.rt-rname{display:flex;align-items:center;gap:6px;font-weight:650;font-size:14px;color:var(--ink)} .rt-rname svg{color:var(--ink-3)}
.rt-rmeta{display:flex;align-items:center;gap:5px;font-size:12px;color:var(--ink-3)}
.rt-detail{padding:20px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:rtrise .3s both;display:flex;flex-direction:column;gap:16px;min-width:0}
.rt-dtop{display:flex;gap:14px;align-items:flex-start;flex-wrap:wrap}
.rt-dtitle{flex:1;min-width:200px} .rt-dtitle h3{margin:0;font-size:18px;font-weight:700} .rt-dtitle p{margin:3px 0 0;font-size:13px;color:var(--ink-3);line-height:1.5}
.rt-dact{display:flex;gap:6px;flex-wrap:wrap}
.rt-detail .rt-seg{align-self:flex-start}
.rt-pe{display:flex;flex-direction:column;gap:12px;position:relative}
.rt-pe-top{display:flex;gap:12px;align-items:stretch;flex-wrap:wrap}
.rt-pe-roll{display:flex;align-items:center;gap:12px;padding:6px 8px 6px 14px;border-radius:11px;background:var(--surface-2);font-size:13px;flex:1;min-width:260px;justify-content:space-between} .rt-pe-roll b{display:block;font-size:13px} .rt-pe-roll span{font-size:12px;color:var(--ink-3)}
.rt-note{padding:10px 14px;border-radius:12px;background:var(--accent-soft,var(--surface-2));color:var(--accent);font-size:13px}
.rt-groups{display:flex;flex-direction:column;gap:6px}
.rt-g{border-radius:14px;box-shadow:inset 0 0 0 1px var(--border);transition:box-shadow .2s} .rt-g.open{box-shadow:inset 0 0 0 1px var(--border-strong)}
.rt-gh{display:flex;align-items:center;gap:10px;padding:4px 12px 4px 4px}
.rt-gt{flex:1;display:flex;align-items:center;gap:10px;min-height:42px;padding:0 8px;background:transparent;text-align:left;min-width:0}
.rt-gt b{font-size:13.5px;font-weight:650;white-space:nowrap} .rt-gt .n{font-size:12px;color:var(--ink-3);font-variant-numeric:tabular-nums} .rt-gt .bar{flex:1;height:4px;border-radius:99px;background:var(--surface-2);overflow:hidden;min-width:30px} .rt-gt .bar i{display:block;height:100%;background:var(--accent);border-radius:99px;transition:width .35s cubic-bezier(.2,.8,.2,1)}
.rt-gt .chev,.rt-chev{color:var(--ink-3);transition:transform .25s} .rt-g.open .chev{transform:rotate(180deg)} [aria-expanded="true"]>.rt-chev{transform:rotate(180deg)}
.rt-perms{list-style:none;margin:0;padding:2px 8px 8px;display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:2px 8px;animation:rtrise .22s both}
.rt-perm{display:flex;gap:10px;align-items:flex-start;width:100%;padding:9px 10px;border-radius:10px;text-align:left;background:transparent;transition:background .15s}
.rt-perm:hover:not(:disabled){background:var(--surface-2)} .rt-perm code{font:600 12px ui-monospace,monospace;color:var(--ink)} .rt-perm small{display:block;margin-top:2px;font-size:12px;color:var(--ink-3);line-height:1.4}
.rt-perm .ck{display:grid;place-items:center;flex:none;width:18px;height:18px;margin-top:1px;border-radius:6px;box-shadow:inset 0 0 0 1.5px var(--border-strong);color:#fff;transition:background .15s,box-shadow .15s} .rt-perm.on .ck{background:var(--accent);box-shadow:none;animation:rtpop .2s both}
.rt-savebar{position:sticky;bottom:12px;display:flex;align-items:center;gap:10px;padding:10px 12px 10px 16px;border-radius:14px;background:var(--ink);color:var(--surface);box-shadow:0 14px 30px -12px rgba(0,0,0,.5);animation:rtrise .25s both}
.rt-savebar>span{flex:1;font-size:13px} .rt-savebar .rt-ghost{color:var(--surface);box-shadow:inset 0 0 0 1px rgba(255,255,255,.3)} .rt-savebar .rt-ghost:hover{background:rgba(255,255,255,.12);color:var(--surface)}
.rt-mem h4{margin:0 0 10px;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)} .rt-mem h4:not(:first-child){margin-top:18px}
.rt-mem{display:flex;flex-direction:column;gap:6px}
.rt-people,.rt-pick{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.rt-people li{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:12px;background:var(--surface-2);animation:rtrise .25s both;transition:opacity .2s} .rt-people li.busy{opacity:.5}
.nm{flex:1;min-width:0;display:flex;flex-direction:column;font-size:13.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .nm small{font-size:12px;font-weight:500;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis}
.rt-pick{margin-top:8px} .rt-pick button{display:flex;align-items:center;gap:10px;width:100%;padding:7px 10px;border-radius:12px;text-align:left;background:transparent;color:var(--ink-3);box-shadow:inset 0 0 0 1px var(--border);transition:background .15s,box-shadow .15s,color .15s}
.rt-pick button:hover:not(:disabled){background:var(--accent-soft,var(--surface-2));box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)} .rt-pick .nm{color:var(--ink)}
/* compare */
.rt-matrix-wrap{padding:16px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);overflow-x:auto;display:flex;flex-direction:column;gap:12px;animation:rtrise .3s both}
.rt-matrix{border-collapse:separate;border-spacing:0;width:100%;min-width:480px}
.rt-matrix th,.rt-matrix td{padding:8px 10px;text-align:center;border-bottom:1px solid var(--border)}
.rt-matrix thead th{vertical-align:bottom;position:sticky;top:0;background:var(--surface)} .rt-matrix thead th:first-child,.rt-matrix tbody th{text-align:left;font-size:13px;font-weight:600;color:var(--ink-2);position:sticky;left:0;background:var(--surface)}
.rt-matrix thead button{display:flex;flex-direction:column;align-items:center;gap:4px;width:100%;padding:6px;border-radius:12px;background:transparent;font-size:12.5px;font-weight:650;color:var(--ink);transition:background .15s} .rt-matrix thead button:hover{background:var(--surface-2)} .rt-matrix small{font-size:11px;font-weight:500;color:var(--ink-3);margin-left:6px}
.rt-matrix thead small{display:block;margin:0}
.rt-matrix tbody tr{animation:rtrise .3s both;animation-delay:calc(var(--i)*25ms)} .rt-matrix tbody tr:hover td,.rt-matrix tbody tr:hover th{background:var(--surface-2)}
.rt-matrix .cell{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;font-size:11.5px;font-weight:700;color:var(--ink-3);background:var(--surface-2)} .rt-matrix .cell.full{background:var(--accent);color:var(--accent-ink,#fff)} .rt-matrix .cell.part{background:var(--accent-soft,var(--surface-2));color:var(--accent)}

/* teams */
.rt-tools{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.rt-fchips{display:flex;gap:6px} .rt-fchips button{height:34px;padding:0 14px;border-radius:99px;font-size:12.5px;font-weight:600;color:var(--ink-2);background:var(--surface);box-shadow:inset 0 0 0 1px var(--border-strong);transition:background .18s,color .18s,box-shadow .18s} .rt-fchips button:hover{box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)} .rt-fchips button.on{background:var(--accent);color:var(--accent-ink,#fff);box-shadow:none}
.rt-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr));gap:14px;align-items:start}
.rt-team{display:flex;flex-direction:column;gap:12px;padding:16px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:rtrise .4s both;animation-delay:calc(var(--i)*50ms);transition:transform .2s,box-shadow .2s,opacity .2s}
.rt-team:hover{box-shadow:inset 0 0 0 1px var(--border-strong),0 14px 28px -20px rgba(20,26,40,.4)} .rt-team.off{opacity:.7} .rt-team.busy{opacity:.6;pointer-events:none}
.rt-ttop{display:flex;gap:12px;align-items:flex-start}
.rt-tname{flex:1;min-width:0} .rt-tname h3{margin:0 0 6px;font-size:15.5px;font-weight:700;overflow-wrap:anywhere}
.rt-pills{display:flex;flex-wrap:wrap;gap:5px}
.p{padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:var(--surface-2);color:var(--ink-3)} .p.blue{background:var(--accent-soft,var(--surface-2));color:var(--accent)} .p.warn{background:#fdf0d5;color:#8a5a00}
.rt-tact{display:flex;gap:4px;align-items:center}
.rt-icon{display:grid;place-items:center;width:32px;height:32px;border-radius:50%;color:var(--ink-2);background:transparent;transition:background .15s,color .15s} .rt-icon:hover{background:var(--surface-2)} .rt-icon.danger:hover{color:var(--crit,#c0392b)}
.rt-tstats{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;color:var(--ink-3)} .rt-tstats b{color:var(--ink);font-variant-numeric:tabular-nums}
.rt-warn{display:flex;align-items:center;gap:8px;padding:9px 12px;border-radius:10px;background:#fdecea;color:#a3271a;font-size:12.5px}
.rt-memb{display:flex;align-items:center;gap:12px;width:100%;padding:8px 10px;border-radius:12px;background:var(--surface-2);text-align:left;transition:background .15s}
.rt-memb:hover{background:color-mix(in srgb,var(--accent) 8%,var(--surface-2))}
.rt-stack{display:flex} .rt-stack .rt-tile{box-shadow:0 0 0 2px var(--surface-2);margin-left:-7px} .rt-stack .rt-tile:first-child{margin-left:0}
.rt-more{display:grid;place-items:center;min-width:26px;height:26px;margin-left:-7px;padding:0 6px;border-radius:99px;font-size:11px;font-weight:700;background:var(--surface);color:var(--ink-3);box-shadow:0 0 0 2px var(--surface-2)} .rt-stack .rt-more:first-child{margin-left:0}
.rt-mlabel{flex:1;font-size:12.5px;font-weight:600;color:var(--ink-2)}
.rt-manage{display:flex;flex-direction:column;gap:10px;animation:rtrise .25s both}
.rt-load,.rt-usedlist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.rt-load li{display:grid;grid-template-columns:minmax(0,1fr) 70px auto 30px;gap:10px;align-items:center;font-size:12.5px}
.rt-load .n{display:flex;flex-direction:column;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600} .rt-load .n small{font-weight:500;font-size:11.5px;color:var(--ink-3)} .rt-load .n small.warn{color:var(--warn,#b36b00)}
.rt-stepper{display:inline-flex;align-items:center;border-radius:99px;background:var(--surface-2)} .rt-stepper button{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:transparent;color:var(--ink-2)} .rt-stepper button:hover:not(:disabled){background:var(--border)} .rt-stepper output{min-width:42px;text-align:center;font-size:12px;font-weight:700;font-variant-numeric:tabular-nums}
.rt-tags{margin:0;display:grid;gap:8px} .rt-tags>div{display:grid;grid-template-columns:62px 1fr;gap:10px;align-items:start}
.rt-tags dt{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding-top:4px} .rt-tags dd{margin:0;display:flex;flex-wrap:wrap;gap:5px}
.rt-tags .tag{padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600} .rt-tags .tag.g{background:#e2f4ea;color:#17673a} .rt-tags .tag.b{background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.rt-uses{display:flex;align-items:center;justify-content:space-between;width:100%;padding:8px 2px 0;border-top:1px solid var(--border);font-size:12.5px;color:var(--ink-3);text-align:left;background:transparent}
.rt-uses:not(:disabled):hover{color:var(--accent)}
.rt-usedlist li{display:flex;gap:8px;align-items:center;font-size:12.5px;color:var(--ink-2)} .rt-usedlist .st{flex:none;padding:1px 8px;border-radius:6px;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.rt-pview{display:flex;flex-direction:column;gap:12px;padding:16px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)}
.rt-prows{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.rt-prows li{display:flex;align-items:center;gap:12px;padding:9px 12px;border-radius:14px;box-shadow:inset 0 0 0 1px var(--border);animation:rtrise .3s both;animation-delay:calc(var(--i)*30ms)}
.rt-prows .nm{flex:1} .rt-prows .tm{flex:1.6;display:flex;flex-wrap:wrap;gap:5px} .rt-prows .pill{padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:var(--accent-soft,var(--surface-2));color:var(--accent)} .rt-prows .pill em{font-style:normal;margin-left:6px;opacity:.7;font-variant-numeric:tabular-nums} .rt-prows .pill.none{background:#fdf0d5;color:#8a5a00}
.rt-add select{height:34px;padding:0 10px;border-radius:10px;border:0;background:var(--surface);color:var(--ink-2);font-size:12.5px;font-weight:600;box-shadow:inset 0 0 0 1px var(--border-strong);max-width:170px}
.rt-empty{display:flex;flex-direction:column;align-items:center;gap:6px;padding:40px 16px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);text-align:center;color:var(--ink-3)} .rt-empty b{color:var(--ink)}
.rtd-sw{position:relative;flex:none;width:40px;height:24px;border-radius:99px;background:var(--border-strong);transition:background .2s;cursor:pointer} .rtd-sw i{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .22s cubic-bezier(.3,1.4,.5,1)}
.rtd-sw.on{background:var(--accent)} .rtd-sw.on i{transform:translateX(16px)} .rtd-sw:disabled{opacity:.5;cursor:default}
:root[data-theme="dark"] .rt-tags .tag.g{background:#17372a;color:#7fd8a6}

/* ---- dense ---- */
.rt{gap:12px}
.rt-head h2{font-size:17px} .rt-head p{margin:2px 0 0;font-size:13px;line-height:1.45;max-width:760px}
.rt-primary{height:34px;padding:0 13px;font-size:12.5px;border-radius:10px;box-shadow:none}
.rt-seg button{height:28px;padding:0 11px;font-size:12.5px;border-radius:8px} .rt-seg{padding:3px;border-radius:11px}
.rt-search{height:34px;border-radius:10px} .rt-search input{font-size:13px}
.rt-ghost{height:30px;padding:0 10px;font-size:12px;border-radius:8px}
.rt-split{grid-template-columns:minmax(220px,260px) 1fr;gap:12px}
.rt-list{gap:2px;padding:4px;border-radius:12px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)}
.rt-role{padding:7px 9px;gap:10px;border-radius:9px;box-shadow:none;background:transparent} .rt-role:hover{transform:none;background:var(--surface-2);box-shadow:none} .rt-role.on{box-shadow:none;background:var(--accent-soft,var(--surface-2))}
.rt-rname{font-size:13.5px} .rt-rmeta{font-size:11.5px}
.rt-lcol{gap:8px;top:8px}
.rt-detail{padding:14px;border-radius:14px;gap:12px}
.rt-dtitle h3{font-size:16px} .rt-dtitle p{font-size:12.5px;margin:1px 0 0}
.rt-pe-top{gap:8px} .rt-pe-roll{padding:4px 6px 4px 12px;border-radius:10px;min-width:240px}
.rt-gt{min-height:34px} .rt-gt b{font-size:13px} .rt-g{border-radius:10px}
.rt-perm{padding:6px 8px} .rt-perm code{font-size:11.5px} .rt-perm small{font-size:11.5px}
.rt-perms{grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:0 8px;padding:0 6px 6px}
.rt-matrix th,.rt-matrix td{padding:5px 8px} .rt-matrix .cell{width:22px;height:22px;font-size:11px} .rt-matrix-wrap{padding:10px;border-radius:14px}
.rt-people li{padding:6px 8px;border-radius:10px}
.rt-ttable{border-radius:12px;box-shadow:inset 0 0 0 1px var(--border);background:var(--surface);overflow:hidden}
.rt-thead{display:grid;grid-template-columns:var(--cols);gap:12px;align-items:center;padding:7px 12px;background:var(--surface-2);font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.rt-trow{border-bottom:1px solid var(--border);animation:rtrise .3s both;animation-delay:calc(var(--i)*25ms);transition:background .15s,opacity .2s} .rt-trow:last-child{border-bottom:0} .rt-trow.off{opacity:.65} .rt-trow.busy{opacity:.55;pointer-events:none} .rt-trow.open{background:color-mix(in srgb,var(--accent) 4%,var(--surface))}
.rt-tline{display:grid;grid-template-columns:var(--cols);gap:12px;align-items:center;padding:6px 12px;min-height:48px} .rt-trow:not(.open) .rt-tline:hover{background:var(--surface-2)}
.rt-tmain{display:flex;align-items:center;gap:10px;min-width:0;background:transparent;border:0;text-align:left;padding:0}
.rt-tmain .nm{font-size:13.5px} .rt-tmain .nm small{font-size:11.5px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rt-stack .rt-tile{margin-left:-6px} .rt-more.sm{min-width:22px;height:22px;font-size:10px;margin-left:-6px}
.rt-loadc{display:flex;align-items:center;gap:8px} .rt-loadc .rt-pbar{flex:1} .rt-loadc small{font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums;flex:none}
.rt-tagsline{display:flex;gap:4px;align-items:center;overflow:hidden;flex-wrap:nowrap} .rt-tagsline.wrapall{flex-wrap:wrap}
.rt-tagsline .tag{padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;white-space:nowrap} .rt-tagsline .tag.g{background:#e2f4ea;color:#17673a} .rt-tagsline .tag.b{background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.rt-dim-s{font-size:12px;color:var(--ink-3);white-space:nowrap}
.rt-tact{display:flex;gap:0;justify-content:flex-end} .rt-icon{width:28px;height:28px}
.rt-xp{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:20px;padding:4px 16px 14px 50px;animation:rtrise .22s both}
.rt-xp h4{margin:10px 0 6px;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)} .rt-xp h4:first-child{margin-top:4px}
.rt-aside{display:flex;flex-direction:column}
.rt-manage{display:flex;flex-direction:column;gap:8px}
.rt-load li{grid-template-columns:minmax(0,1fr) 60px auto 28px}
.rt-manage .rt-search,.rt-mem .rt-search{flex:none}
.rt-usedlist{max-height:170px;overflow-y:auto;padding-right:4px}
@media (max-width:980px){.rt-xp{grid-template-columns:1fr;padding-left:16px}.rt-tline,.rt-thead{grid-template-columns:minmax(0,1fr) 52px 92px}.rt-tline>:nth-child(2),.rt-tline>:nth-child(3),.rt-tline>:nth-child(4),.rt-thead>:nth-child(2),.rt-thead>:nth-child(3),.rt-thead>:nth-child(4){display:none}}
@media (max-width:860px){.rt-split{grid-template-columns:1fr}.rt-lcol{position:static}.rt-prows li{flex-wrap:wrap}}
@media (prefers-reduced-motion:reduce){.rt *{animation:none!important;transition:none!important}}
`;
