"use client";

// The editors behind Roles & Teams: one slide-in drawer, switches instead of tiny checkboxes,
// a searchable permission picker, a people picker for teams, and the assign-roles flow.
// Presentation only: state and saving stay in page.tsx.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Check, ChevronDown, Minus, Plus, Search, X } from "lucide-react";
import type { IntakeTeam, PermissionInfo, RoleResponse, UserResponse } from "@/lib/types";
import { titleCase } from "@/lib/utils";
import { Switch, Tile } from "./_atoms";
export { Switch };

export function Drawer({ title, subtitle, onClose, footer, children }: { title: string; subtitle?: string; onClose: () => void; footer?: ReactNode; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("keydown", key); prev?.focus?.(); };
  }, [onClose]);
  return (
    <div className="rtd-root">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="rtd-back" onClick={onClose} aria-hidden />
      <div className="rtd-panel" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>
        <header className="rtd-top">
          <div><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
          <button type="button" className="rtd-x" aria-label="Close" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="rtd-body">{children}</div>
        {footer && <footer className="rtd-foot">{footer}</footer>}
      </div>
    </div>
  );
}

function Section({ title, hint, children }: { title: string; hint?: string; children: ReactNode }) {
  return <section className="rtd-sec"><h3>{title}</h3>{hint && <p className="hint">{hint}</p>}{children}</section>;
}

const Footer = ({ onClose, onSave, busy, canSave, label, readOnly, note }: { onClose: () => void; onSave: () => void; busy: boolean; canSave: boolean; label: string; readOnly?: boolean; note?: string }) => (
  <>
    <span className="rtd-note">{note}</span>
    <button type="button" className="rtd-ghost" onClick={onClose}>{readOnly ? "Close" : "Cancel"}</button>
    {!readOnly && <button type="button" className="rtd-primary" onClick={onSave} disabled={!canSave || busy}>{busy ? "Saving…" : label}</button>}
  </>
);

// ------------------------------------------------------------ role editor

export function RoleEditorUI({ mode, title, readOnly, nameLocked, name, setName, description, setDescription, rollup, setRollup, selected, onToggle, onToggleGroup, onReplace, groups, roles, busy, canSave, onSave, onClose }: {
  mode: "create" | "edit"; title: string; readOnly: boolean; nameLocked: boolean;
  name: string; setName: (v: string) => void; description: string; setDescription: (v: string) => void;
  rollup: boolean; setRollup: (v: boolean) => void; selected: Set<string>;
  onToggle: (v: string) => void; onToggleGroup: (p: PermissionInfo[], on: boolean) => void; onReplace: (perms: string[]) => void;
  groups: [string, PermissionInfo[]][]; roles: RoleResponse[]; busy: boolean; canSave: boolean; onSave: () => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const needle = q.trim().toLowerCase();
  const hit = (p: PermissionInfo) => !needle || p.value.toLowerCase().includes(needle) || (p.description ?? "").toLowerCase().includes(needle);
  const flip = (g: string) => setOpen((s) => { const n = new Set(s); if (n.has(g)) n.delete(g); else n.add(g); return n; });
  const total = groups.reduce((n, [, p]) => n + p.length, 0);

  return (
    <Drawer title={title} subtitle={mode === "create" ? "Name it, then choose what it may do." : readOnly ? "Always has everything. Can’t be changed." : undefined} onClose={onClose}
      footer={<Footer onClose={onClose} onSave={onSave} busy={busy} canSave={canSave} readOnly={readOnly} label={mode === "create" ? "Create role" : "Save changes"} note={`${selected.size} of ${total} permissions`} />}>
      {readOnly && <div className="rtd-info">The built-in <b>admin</b> role has every permission and is your break-glass access.</div>}

      <Section title="About this role">
        <label className="rtd-f"><span>Name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} disabled={nameLocked || readOnly} placeholder="e.g. Paralegal, Outside Counsel" autoFocus={!nameLocked} />
        </label>
        <label className="rtd-f"><span>Description <em>optional</em></span>
          <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} disabled={readOnly} placeholder="Who is this for?" />
        </label>
        <div className="rtd-row">
          <div><b>Sees the team below</b><p>People with this role also see what the people under them in the org chart can see.</p></div>
          <Switch on={rollup} onChange={setRollup} label="Rolls up the org-unit hierarchy" disabled={readOnly} />
        </div>
      </Section>

      <Section title="What it can do">
        {!readOnly && mode === "create" && (
          <div className="rtd-start">
            <span>Start from</span>
            {roles.filter((r) => r.name !== "admin").map((r) => <button key={r.id} type="button" className="rtd-chip" onClick={() => onReplace(r.permissions)}>{titleCase(r.name)}</button>)}
            <button type="button" className="rtd-chip quiet" onClick={() => onReplace([])}>Nothing</button>
          </div>
        )}
        <label className="rtd-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search permissions…" aria-label="Search permissions" /></label>
        <div className="rtd-groups">
          {groups.map(([g, perms]) => {
            const shown = perms.filter(hit);
            if (!shown.length) return null;
            const have = perms.filter((p) => selected.has(p.value)).length;
            const isOpen = open.has(g) || !!needle;
            return (
              <div key={g} className={`rtd-g${isOpen ? " open" : ""}`}>
                <div className="rtd-gh">
                  <button type="button" className="rtd-gt" aria-expanded={isOpen} onClick={() => flip(g)}>
                    <ChevronDown size={16} className="chev" />
                    <b>{titleCase(g.replace(/_/g, " "))}</b>
                    <span className="n">{have}/{perms.length}</span>
                    <span className="bar"><i style={{ width: `${(have / perms.length) * 100}%` }} /></span>
                  </button>
                  {!readOnly && <Switch on={have === perms.length} onChange={(v) => onToggleGroup(perms, v)} label={`All ${g} permissions`} />}
                </div>
                {isOpen && (
                  <ul className="rtd-perms">
                    {shown.map((p) => (
                      <li key={p.value}>
                        <button type="button" className={`rtd-perm${selected.has(p.value) ? " on" : ""}`} aria-pressed={selected.has(p.value)} disabled={readOnly} onClick={() => onToggle(p.value)}>
                          <span className="ck">{selected.has(p.value) && <Check size={12} />}</span>
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
      </Section>
    </Drawer>
  );
}

// ------------------------------------------------------------ team editor

export function TeamEditorUI({ team, name, setName, keyVal, setKey, strategy, setStrategy, overflow, setOverflow, teams, defaultIntake, setDefaultIntake, expertise, departments, onExpertise, onDepartments, categories, units, members, setMembers, users, busy, canSave, onSave, onClose }: {
  team: IntakeTeam | null; name: string; setName: (v: string) => void; keyVal: string; setKey: (v: string) => void;
  strategy: string; setStrategy: (v: IntakeTeam["strategy"]) => void; overflow: string; setOverflow: (v: string) => void; teams: IntakeTeam[];
  defaultIntake: boolean; setDefaultIntake: (v: boolean) => void; expertise: string[]; departments: string[];
  onExpertise: (v: string) => void; onDepartments: (v: string) => void; categories: string[]; units: string[];
  members: { user_id: string; capacity: number }[]; setMembers: (m: { user_id: string; capacity: number }[]) => void; users: UserResponse[];
  busy: boolean; canSave: boolean; onSave: () => void; onClose: () => void;
}) {
  const [q, setQ] = useState("");
  const ids = new Set(members.map((m) => m.user_id));
  const addable = users.filter((u) => !ids.has(u.id) && (!q.trim() || `${u.full_name} ${u.email}`.toLowerCase().includes(q.trim().toLowerCase())));
  const setCap = (i: number, c: number) => setMembers(members.map((x, j) => (j === i ? { ...x, capacity: Math.max(0, c) } : x)));
  const ChipRow = ({ opts, sel, on }: { opts: string[]; sel: string[]; on: (v: string) => void }) => (
    <div className="rtd-chips">{opts.map((o) => <button key={o} type="button" className={`rtd-chip${sel.includes(o) ? " on" : ""}`} aria-pressed={sel.includes(o)} onClick={() => on(o)}>{sel.includes(o) && <Check size={12} />}{o}</button>)}</div>
  );
  return (
    <Drawer title={team ? `Edit ${team.name}` : "New team"} subtitle="A team is a group of people that does the work." onClose={onClose}
      footer={<Footer onClose={onClose} onSave={onSave} busy={busy} canSave={canSave} label={team ? "Save team" : "Create team"} note={`${members.length} ${members.length === 1 ? "member" : "members"}`} />}>
      <Section title="Basics">
        <div className="rtd-two">
          <label className="rtd-f"><span>Name</span><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Commercial Legal" autoFocus /></label>
          <label className="rtd-f"><span>Key {!team && <em>lowercase id</em>}</span><input value={team ? team.key : keyVal} disabled={!!team} onChange={(e) => setKey(e.target.value)} placeholder="commercial" /></label>
        </div>
        <div className="rtd-row">
          <div><b>Default intake team</b><p>Owns new requests that no team’s expertise matches.</p></div>
          <Switch on={defaultIntake} onChange={setDefaultIntake} label="Default intake team" disabled={!!team?.is_default_intake} />
        </div>
      </Section>

      <Section title="Who gets the next request" hint="How work is shared inside the team.">
        <div className="rtd-opts">
          {([["least_loaded", "Fewest open first", "The person with the lightest load gets it."], ["round_robin", "Taking turns", "Everyone in turn, whatever their load."]] as const).map(([v, t, d]) => (
            <button key={v} type="button" className={`rtd-opt${strategy === v ? " on" : ""}`} aria-pressed={strategy === v} onClick={() => setStrategy(v)}><b>{t}</b><span>{d}</span></button>
          ))}
        </div>
        <label className="rtd-f"><span>When everyone is full</span>
          <select value={overflow} onChange={(e) => setOverflow(e.target.value)}>
            <option value="">No overflow team</option>
            {teams.filter((t) => t.id !== team?.id).map((t) => <option key={t.id} value={t.id}>{t.name} takes over</option>)}
          </select>
        </label>
      </Section>

      <Section title="What it handles" hint="Requests whose type matches go to this team. Leave blank to match none.">
        <ChipRow opts={categories} sel={expertise} on={onExpertise} />
      </Section>
      <Section title="Who it serves" hint="Requesting departments. Blank serves all.">
        <ChipRow opts={units} sel={departments} on={onDepartments} />
      </Section>

      <Section title={`Members (${members.length})`} hint="Capacity is how many open items a person can hold. 0 means no limit.">
        {members.length === 0 && <div className="rtd-info warn">No members yet. A workflow step using this team would stall.</div>}
        <ul className="rtd-members">
          {members.map((m, i) => {
            const u = users.find((x) => x.id === m.user_id);
            const n = u?.full_name ?? m.user_id;
            return (
              <li key={m.user_id}>
                <Tile name={n} size={32} />
                <span className="nm">{n}<small>{u?.email}</small></span>
                <span className="step" role="group" aria-label={`Capacity for ${n}`}>
                  <button type="button" aria-label="Less capacity" onClick={() => setCap(i, m.capacity - 1)}><Minus size={14} /></button>
                  <output>{m.capacity || "∞"}</output>
                  <button type="button" aria-label="More capacity" onClick={() => setCap(i, m.capacity + 1)}><Plus size={14} /></button>
                </span>
                <button type="button" className="rtd-rm" aria-label={`Remove ${n}`} onClick={() => setMembers(members.filter((_, j) => j !== i))}><X size={15} /></button>
              </li>
            );
          })}
        </ul>
        <label className="rtd-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find people to add…" aria-label="Find people to add" /></label>
        <ul className="rtd-pick">
          {addable.slice(0, 6).map((u) => (
            <li key={u.id}>
              <button type="button" onClick={() => setMembers([...members, { user_id: u.id, capacity: 8 }])}>
                <Tile name={u.full_name} size={28} /><span className="nm">{u.full_name}<small>{u.email}</small></span><Plus size={15} />
              </button>
            </li>
          ))}
          {addable.length === 0 && <li className="none">{q ? "No one matches." : "Everyone is already on this team."}</li>}
          {addable.length > 6 && <li className="none">Type to narrow {addable.length - 6} more.</li>}
        </ul>
      </Section>
    </Drawer>
  );
}

// ------------------------------------------------------------ assign roles

export function PeopleList({ users, onManage }: { users: UserResponse[]; onManage: (u: UserResponse) => void }) {
  const [q, setQ] = useState("");
  const list = users.filter((u) => !q.trim() || `${u.full_name} ${u.email} ${u.roles.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <section className="rtd-people">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="rtd-ph">
        <div><h3>People &amp; their roles</h3><p>Pick a person to change their roles or how confidential a contract they may read.</p></div>
        <label className="rtd-search"><Search size={15} /><input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people or roles…" aria-label="Search people" /></label>
      </div>
      <ul>
        {list.map((u, i) => (
          <li key={u.id} style={{ ["--i" as string]: Math.min(i, 12) }}>
            <button type="button" onClick={() => onManage(u)}>
              <Tile name={u.full_name} size={38} />
              <span className="nm">{u.full_name}<small>{u.email}</small></span>
              <span className="rl">{u.roles.length ? u.roles.map((r) => <span key={r} className={`pill${r === u.active_role_name ? " main" : ""}`}>{titleCase(r)}</span>) : <span className="pill none">No role</span>}</span>
              <span className="cl">{titleCase(u.clearance)}</span>
              <span className="go">Manage</span>
            </button>
          </li>
        ))}
        {list.length === 0 && <li className="none">No one matches “{q}”.</li>}
      </ul>
    </section>
  );
}

const LEVEL_HINT: Record<string, string> = {
  public: "Only public contracts.",
  internal: "Public and internal.",
  confidential: "Everything but restricted.",
  restricted: "Every contract, including restricted.",
};

export function AssignUI({ user, roles, picked, onToggle, activeId, setActiveId, clearance, setClearance, levels, busy, onSave, onClose }: {
  user: UserResponse; roles: RoleResponse[]; picked: Set<string>; onToggle: (id: string) => void; activeId: string; setActiveId: (id: string) => void;
  clearance: string; setClearance: (v: string) => void; levels: string[]; busy: boolean; onSave: () => void; onClose: () => void;
}) {
  return (
    <Drawer title={user.full_name} subtitle={user.email} onClose={onClose}
      footer={<Footer onClose={onClose} onSave={onSave} busy={busy} canSave={picked.size > 0} label="Save changes" note={picked.size ? "" : "Pick at least one role"} />}>
      <Section title="Roles" hint="They get every permission of every role you pick.">
        <div className="rtd-rolepick">
          {roles.map((r) => {
            const on = picked.has(r.id);
            return (
              <button key={r.id} type="button" className={`rtd-opt${on ? " on" : ""}`} aria-pressed={on} onClick={() => onToggle(r.id)}>
                <b>{on && <Check size={14} />}{titleCase(r.name)}</b>
                <span>{r.description || `${r.permissions.length} permissions`}</span>
              </button>
            );
          })}
        </div>
      </Section>
      {picked.size > 1 && (
        <Section title="Main role" hint="Shown as their title and used first when a screen needs just one.">
          <div className="rtd-chips">
            {[...picked].map((id) => { const r = roles.find((x) => x.id === id); return <button key={id} type="button" className={`rtd-chip${activeId === id ? " on" : ""}`} aria-pressed={activeId === id} onClick={() => setActiveId(id)}>{r ? titleCase(r.name) : id}</button>; })}
          </div>
        </Section>
      )}
      <Section title="Confidentiality clearance" hint="The most confidential contracts they can read. Anything above is hidden, including from the assistant.">
        <div className="rtd-levels" role="radiogroup" aria-label="Clearance">
          {levels.map((l, i) => (
            <button key={l} type="button" role="radio" aria-checked={clearance === l} className={`${clearance === l ? "on" : ""}${levels.indexOf(clearance) >= i ? " lit" : ""}`} onClick={() => setClearance(l)}>
              <i />{titleCase(l)}
            </button>
          ))}
        </div>
        <p className="rtd-lvh">{LEVEL_HINT[clearance] ?? ""}</p>
      </Section>
    </Drawer>
  );
}

const CSS = `
@keyframes rtdfade{from{opacity:0}to{opacity:1}}
@keyframes rtdslide{from{transform:translateX(40px);opacity:0}to{transform:none;opacity:1}}
@keyframes rtdrise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.rtd-root{position:fixed;inset:0;z-index:60;display:flex;justify-content:flex-end}
.rtd-back{position:absolute;inset:0;background:rgba(15,20,35,.45);backdrop-filter:blur(2px);animation:rtdfade .2s both}
.rtd-panel{position:relative;display:flex;flex-direction:column;width:min(580px,100vw);height:100%;background:var(--surface);box-shadow:-24px 0 48px -24px rgba(0,0,0,.35);animation:rtdslide .28s cubic-bezier(.2,.8,.2,1) both;outline:none;color:var(--ink)}
.rtd-top{display:flex;justify-content:space-between;gap:12px;padding:20px 24px 16px;border-bottom:1px solid var(--border)}
.rtd-top h2{margin:0;font-size:19px;font-weight:700} .rtd-top p{margin:3px 0 0;font-size:13px;color:var(--ink-3)}
.rtd-x{display:grid;place-items:center;width:36px;height:36px;border-radius:50%;color:var(--ink-2);transition:background .15s} .rtd-x:hover{background:var(--surface-2)}
.rtd-body{flex:1;overflow-y:auto;padding:8px 24px 24px;display:flex;flex-direction:column}
.rtd-foot{display:flex;align-items:center;gap:10px;padding:14px 24px;border-top:1px solid var(--border);background:var(--surface)}
.rtd-note{flex:1;font-size:12.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.rtd-panel button,.rtd-people button{cursor:pointer;font-family:inherit} .rtd-panel button:disabled{cursor:default}
.rtd-panel button:focus-visible,.rtd-people button:focus-visible,.rtd-panel input:focus-visible,.rtd-panel textarea:focus-visible,.rtd-panel select:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.rtd-primary{height:40px;padding:0 18px;border-radius:12px;background:var(--accent);color:var(--accent-ink,#fff);font-weight:650;font-size:13.5px;transition:transform .15s,opacity .2s,box-shadow .2s;box-shadow:0 8px 18px -10px var(--accent)}
.rtd-primary:hover:not(:disabled){transform:translateY(-1px)} .rtd-primary:active:not(:disabled){transform:scale(.97)} .rtd-primary:disabled{opacity:.45;box-shadow:none}
.rtd-ghost{height:40px;padding:0 16px;border-radius:12px;font-weight:600;font-size:13.5px;color:var(--ink-2);box-shadow:inset 0 0 0 1px var(--border-strong);background:transparent;transition:background .15s} .rtd-ghost:hover{background:var(--surface-2)}
.rtd-sec{padding:18px 0;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:12px;animation:rtdrise .3s both} .rtd-sec:last-child{border-bottom:0}
.rtd-sec h3{margin:0;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.rtd-sec .hint{margin:-6px 0 0;font-size:12.5px;color:var(--ink-3);line-height:1.5}
.rtd-info{margin-top:14px;padding:11px 14px;border-radius:12px;background:var(--accent-soft,var(--surface-2));color:var(--accent);font-size:13px;line-height:1.5} .rtd-info.warn{margin:0;background:#fdf0d5;color:#8a5a00}
.rtd-f{display:flex;flex-direction:column;gap:6px;font-size:12.5px;font-weight:600;color:var(--ink-2)} .rtd-f em{font-style:normal;font-weight:500;color:var(--ink-3);margin-left:4px}
.rtd-f input,.rtd-f textarea,.rtd-f select,.rtd-search input{width:100%;border-radius:11px;background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1px var(--border-strong);padding:0 12px;height:42px;font:500 14px inherit;font-family:inherit;border:0;transition:box-shadow .15s}
.rtd-f textarea{height:auto;padding:10px 12px;resize:vertical} .rtd-f input:focus,.rtd-f textarea:focus,.rtd-f select:focus,.rtd-search input:focus{box-shadow:inset 0 0 0 2px var(--accent);outline:none}
.rtd-f input:disabled,.rtd-f textarea:disabled{background:var(--surface-2);color:var(--ink-3)}
.rtd-two{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.rtd-row{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:12px 14px;border-radius:12px;background:var(--surface-2)} .rtd-row b{font-size:13.5px} .rtd-row p{margin:2px 0 0;font-size:12.5px;color:var(--ink-3);line-height:1.45}
.rtd-sw{position:relative;flex:none;width:40px;height:24px;border-radius:99px;background:var(--border-strong);transition:background .2s} .rtd-sw i{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .22s cubic-bezier(.3,1.4,.5,1)}
.rtd-sw.on{background:var(--accent)} .rtd-sw.on i{transform:translateX(16px)} .rtd-sw:disabled{opacity:.5}
.rtd-search{display:flex;align-items:center;gap:8px;padding-left:12px;border-radius:11px;box-shadow:inset 0 0 0 1px var(--border-strong);color:var(--ink-3);background:var(--surface);min-width:0;flex:1} .rtd-search input{box-shadow:none!important;padding-left:0;height:40px} .rtd-search:focus-within{box-shadow:inset 0 0 0 2px var(--accent)}
.rtd-chips,.rtd-start{display:flex;flex-wrap:wrap;gap:7px;align-items:center} .rtd-start>span{font-size:12.5px;color:var(--ink-3);margin-right:2px}
.rtd-chip{display:inline-flex;align-items:center;gap:5px;min-height:32px;padding:0 13px;border-radius:99px;font-size:12.5px;font-weight:600;color:var(--ink-2);background:var(--surface);box-shadow:inset 0 0 0 1px var(--border-strong);transition:background .18s,color .18s,box-shadow .18s,transform .12s}
.rtd-chip:hover{box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)} .rtd-chip:active{transform:scale(.95)} .rtd-chip.on{background:var(--accent);color:var(--accent-ink,#fff);box-shadow:none} .rtd-chip.quiet{border-style:dashed}
.rtd-opts,.rtd-rolepick{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.rtd-opt{display:flex;flex-direction:column;gap:3px;text-align:left;padding:12px 14px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border-strong);transition:box-shadow .18s,background .18s,transform .15s}
.rtd-opt:hover{transform:translateY(-1px);box-shadow:inset 0 0 0 1px var(--accent)} .rtd-opt.on{box-shadow:inset 0 0 0 2px var(--accent);background:var(--accent-soft,var(--surface))}
.rtd-opt b{display:flex;align-items:center;gap:6px;font-size:13.5px;color:var(--ink)} .rtd-opt span{font-size:12px;color:var(--ink-3);line-height:1.4}
.rtd-groups{display:flex;flex-direction:column;gap:6px}
.rtd-g{border-radius:14px;box-shadow:inset 0 0 0 1px var(--border);transition:box-shadow .2s} .rtd-g.open{box-shadow:inset 0 0 0 1px var(--border-strong)}
.rtd-gh{display:flex;align-items:center;gap:10px;padding:4px 12px 4px 4px}
.rtd-gt{flex:1;display:flex;align-items:center;gap:10px;min-height:42px;padding:0 8px;background:transparent;text-align:left;min-width:0}
.rtd-gt b{font-size:13.5px;font-weight:650;white-space:nowrap} .rtd-gt .n{font-size:12px;color:var(--ink-3);font-variant-numeric:tabular-nums} .rtd-gt .bar{flex:1;height:4px;border-radius:99px;background:var(--surface-2);overflow:hidden;min-width:30px} .rtd-gt .bar i{display:block;height:100%;background:var(--accent);border-radius:99px;transition:width .35s cubic-bezier(.2,.8,.2,1)}
.rtd-gt .chev{color:var(--ink-3);transition:transform .25s} .rtd-g.open .chev{transform:rotate(180deg)}
.rtd-perms{list-style:none;margin:0;padding:2px 8px 8px;display:flex;flex-direction:column;gap:2px;animation:rtdrise .22s both}
.rtd-perm{display:flex;gap:10px;align-items:flex-start;width:100%;padding:9px 10px;border-radius:10px;text-align:left;background:transparent;transition:background .15s}
.rtd-perm:hover:not(:disabled){background:var(--surface-2)} .rtd-perm code{font:600 12px ui-monospace,monospace;color:var(--ink)} .rtd-perm small{display:block;margin-top:2px;font-size:12px;color:var(--ink-3);line-height:1.4}
.rtd-perm .ck{display:grid;place-items:center;flex:none;width:18px;height:18px;margin-top:1px;border-radius:6px;box-shadow:inset 0 0 0 1.5px var(--border-strong);color:#fff;transition:background .15s,box-shadow .15s} .rtd-perm.on .ck{background:var(--accent);box-shadow:none}
.rtd-members,.rtd-pick{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.rtd-members li{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:12px;background:var(--surface-2);animation:rtdrise .25s both}
.rtd-members .nm,.rtd-pick .nm,.rtd-people .nm{flex:1;min-width:0;display:flex;flex-direction:column;font-size:13.5px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .rtd-members small,.rtd-pick small,.rtd-people small{font-size:12px;font-weight:500;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis}
.rtd-members .step{display:inline-flex;align-items:center;border-radius:99px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)} .rtd-members .step button{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;color:var(--ink-2);background:transparent} .rtd-members .step button:hover{background:var(--surface-2)} .rtd-members .step output{min-width:26px;text-align:center;font-size:13px;font-weight:700;font-variant-numeric:tabular-nums}
.rtd-rm{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;color:var(--ink-3);background:transparent;transition:background .15s,color .15s} .rtd-rm:hover{background:#fdecea;color:#a3271a}
.rtd-pick button{display:flex;align-items:center;gap:10px;width:100%;padding:7px 10px;border-radius:12px;text-align:left;background:transparent;color:var(--ink-3);box-shadow:inset 0 0 0 1px var(--border);transition:background .15s,box-shadow .15s,color .15s} .rtd-pick button:hover{background:var(--accent-soft,var(--surface-2));box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)} .rtd-pick button .nm{color:var(--ink)}
.rtd-pick .none{font-size:12.5px;color:var(--ink-3);padding:4px 2px}
.rtd-levels{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.rtd-levels button{display:flex;flex-direction:column;align-items:center;gap:8px;padding:12px 4px 10px;border-radius:12px;font-size:12.5px;font-weight:600;color:var(--ink-3);background:var(--surface);box-shadow:inset 0 0 0 1px var(--border-strong);transition:box-shadow .18s,color .18s,background .18s}
.rtd-levels button i{width:100%;max-width:44px;height:5px;border-radius:99px;background:var(--border-strong);transition:background .25s} .rtd-levels button.lit i{background:var(--accent)} .rtd-levels button.on{color:var(--ink);box-shadow:inset 0 0 0 2px var(--accent)}
.rtd-lvh{margin:0;font-size:12.5px;color:var(--ink-3)}
/* people list */
.rtd-people{display:flex;flex-direction:column;gap:12px;padding:20px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);color:var(--ink)}
.rtd-ph{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap} .rtd-ph h3{margin:0;font-size:16px;font-weight:700} .rtd-ph p{margin:3px 0 0;font-size:13px;color:var(--ink-3)} .rtd-ph .rtd-search{max-width:300px}
.rtd-people ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.rtd-people li{animation:rtdrise .3s both;animation-delay:calc(var(--i)*30ms)} .rtd-people li.none{padding:12px;font-size:13px;color:var(--ink-3)}
.rtd-people li button{display:flex;align-items:center;gap:12px;width:100%;padding:9px 12px;border-radius:14px;text-align:left;background:transparent;box-shadow:inset 0 0 0 1px var(--border);transition:box-shadow .18s,background .18s,transform .18s}
.rtd-people li button:hover{background:var(--surface-2);box-shadow:inset 0 0 0 1px var(--border-strong);transform:translateX(3px)}
.rtd-people .nm{flex:1.2} .rtd-people .rl{flex:1.4;display:flex;flex-wrap:wrap;gap:5px} .rtd-people .pill{padding:2px 9px;border-radius:99px;font-size:11.5px;font-weight:600;background:var(--surface-2);color:var(--ink-3)} .rtd-people .pill.main{background:var(--accent-soft,var(--surface-2));color:var(--accent)} .rtd-people .pill.none{background:#fdf0d5;color:#8a5a00}
.rtd-people .cl{width:96px;font-size:12.5px;color:var(--ink-3)} .rtd-people .go{font-size:12.5px;font-weight:650;color:var(--accent);opacity:0;transition:opacity .15s} .rtd-people li button:hover .go,.rtd-people li button:focus-visible .go{opacity:1}
@media (max-width:700px){.rtd-two,.rtd-opts,.rtd-rolepick{grid-template-columns:1fr}.rtd-people .cl,.rtd-people .go{display:none}.rtd-body,.rtd-top,.rtd-foot{padding-left:16px;padding-right:16px}}
@media (prefers-reduced-motion:reduce){.rtd-root *,.rtd-people *{animation:none!important;transition:none!important}}
`;
