"use client";

// Matters board — every client engagement, grouped/filterable, plus the
// "awaiting a matter" tray that files unfiled contracts/requests into exactly
// one matter (one matter -> many). Scoped `.proj`.

import { useEffect, useId, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { mattersApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { titleCase } from "@/lib/utils";
import type { MatterResponse, MatterStatus, MatterType, UnfiledItem } from "@/lib/types";

const MATTER_TYPES: MatterType[] = [
  "general",
  "transactional",
  "litigation",
  "m_and_a",
  "employment",
  "privacy",
  "procurement",
  "advisory",
  "contract_review",
  "due_diligence",
  "regulatory",
];

const STATUS_FILTERS: { key: "all" | MatterStatus; label: string }[] = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "intake", label: "Intake" },
  { key: "on_hold", label: "On hold" },
  { key: "closed", label: "Closed" },
];

function statusTone(s: string): string {
  if (s === "active") return "blue";
  if (s === "on_hold") return "amber";
  if (s === "closed") return "slate";
  return "violet"; // intake
}

export default function MattersPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [createOpen, setCreateOpen] = useState(false);
  const [filter, setFilter] = useState<"all" | MatterStatus>("all");
  // One-time "Projects are now Matters" notice; dismissed state in localStorage.
  const [showMigration, setShowMigration] = useState(false);
  useEffect(() => {
    setShowMigration(localStorage.getItem("matters.migrationDismissed") !== "1");
  }, []);
  function dismissMigration() {
    localStorage.setItem("matters.migrationDismissed", "1");
    setShowMigration(false);
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ["matters"],
    queryFn: mattersApi.list,
  });
  const { data: unfiledData } = useQuery({
    queryKey: ["matters-unfiled"],
    queryFn: () => mattersApi.unfiled(),
  });
  const matters = data ?? [];
  const unfiled = unfiledData ?? [];
  const shown = useMemo(
    () => (filter === "all" ? matters : matters.filter((m) => m.status === filter)),
    [matters, filter],
  );

  async function assign(item: UnfiledItem, matterId: string) {
    if (!matterId) return;
    try {
      await mattersApi.assign(matterId, item.kind, item.id);
      qc.invalidateQueries({ queryKey: ["matters-unfiled"] });
      qc.invalidateQueries({ queryKey: ["matters"] });
      notify("Filed under the matter", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't assign", "error");
    }
  }

  return (
    <div className="proj">
      <style dangerouslySetInnerHTML={{ __html: PROJ_CSS }} />
      <div className="hd">
        <div>
          <h1>Matters</h1>
          <p className="sub">Every client engagement — its contracts, requests, obligations and people in one place.</p>
        </div>
        <div className="acts">
          <button className="btn pri" onClick={() => setCreateOpen(true)}>+ New matter</button>
        </div>
      </div>

      {showMigration && (
        <div className="mbanner">
          <span>Your <b>projects are now Matters</b> — the same workspaces, promoted into a client-engagement hub. Contracts, requests, obligations and notices roll up per matter.</span>
          <button className="x" aria-label="Dismiss" onClick={dismissMigration}>×</button>
        </div>
      )}

      <div className="filters">
        {STATUS_FILTERS.map((f) => {
          const count = f.key === "all" ? matters.length : matters.filter((m) => m.status === f.key).length;
          return (
            <button
              key={f.key}
              className={`chip${filter === f.key ? " sel" : ""}`}
              onClick={() => setFilter(f.key)}
            >
              {f.label} <span className="ct">{count}</span>
            </button>
          );
        })}
      </div>

      {unfiled.length > 0 && (
        <div className="tray">
          <div className="trh">
            <b>{unfiled.length} item{unfiled.length === 1 ? "" : "s"} awaiting a matter</b>
            <span className="trsub">Each contract or request files into exactly one matter.</span>
          </div>
          {unfiled.slice(0, 6).map((it) => (
            <div key={`${it.kind}-${it.id}`} className="trrow">
              <span className={`kind ${it.kind}`}>{it.kind}</span>
              <span className="nm">{it.title}</span>
              {it.subtitle && <span className="sub2">{it.subtitle}</span>}
              {it.suggested_matter_id && (
                <button
                  className="sugbtn"
                  title={`File under ${it.suggested_matter_label}`}
                  onClick={() => assign(it, it.suggested_matter_id as string)}
                >
                  Aegis suggests <b>{it.suggested_matter_label}</b>
                </button>
              )}
              <select
                className="asg"
                defaultValue=""
                onChange={(e) => assign(it, e.target.value)}
              >
                <option value="" disabled>Assign to matter…</option>
                {matters.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.matter_number ? `${m.matter_number} · ` : ""}{m.name}
                  </option>
                ))}
              </select>
            </div>
          ))}
          {unfiled.length > 6 && <div className="trmore">+ {unfiled.length - 6} more unfiled</div>}
        </div>
      )}

      {isLoading ? (
        <div className="tablewrap"><div className="empty">Loading matters…</div></div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load matters."}</div>
      ) : matters.length === 0 ? (
        <div className="empty">
          No matters yet. Create one to group a client engagement — its contracts, requests, deadlines and team.
          <div style={{ marginTop: 12 }}><button className="btn pri" onClick={() => setCreateOpen(true)}>New matter</button></div>
        </div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr><th>Matter</th><th>Client</th><th>Type</th><th>Status</th></tr>
            </thead>
            <tbody>
              {shown.map((m) => (
                <tr key={m.id} onClick={() => { window.location.href = `/matters/${m.id}`; }}>
                  <td>
                    <Link href={`/matters/${m.id}`} className="mname" onClick={(e) => e.stopPropagation()}>{m.name}</Link>
                    <div className="mnum">{m.matter_number ?? "—"}</div>
                  </td>
                  <td className="muted">{m.client_name ?? "—"}</td>
                  <td className="muted">{titleCase(m.matter_type)}</td>
                  <td><span className={`tag ${statusTone(m.status)}`}>{titleCase(m.status)}</span></td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr><td colSpan={4} className="muted" style={{ textAlign: "center", padding: 20 }}>No matters with this status.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      <CreateMatterModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["matters"] });
          notify("Matter created", "success");
          setCreateOpen(false);
        }}
      />
    </div>
  );
}

function CreateMatterModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [client, setClient] = useState("");
  const [description, setDescription] = useState("");
  const [matterType, setMatterType] = useState<MatterType>("transactional");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const focusables = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      );
    (focusables()[0] ?? dialog)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.key !== "Tab") return;
      const els = focusables();
      if (els.length === 0) return;
      const first = els[0];
      const last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); previouslyFocused?.focus?.(); };
  }, [open, onClose]);

  if (!open) return null;

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await mattersApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        matter_type: matterType,
        client_name: client.trim() || undefined,
      });
      setName(""); setClient(""); setDescription(""); setMatterType("transactional");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create matter", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="proj">
      <div className="ov" onClick={onClose}>
        <div className="dlg" role="dialog" aria-modal="true" aria-labelledby={titleId} ref={dialogRef} tabIndex={-1} onClick={(e) => e.stopPropagation()}>
          <div className="dhd"><h2 id={titleId}>New matter</h2></div>
          <div className="dbody">
            <label className="fld">
              <span className="lbl">Matter name</span>
              <input className="inp" placeholder="e.g. Globex MSA — Renewal & Expansion" value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="fld">
              <span className="lbl">Client <span className="hint">Optional</span></span>
              <input className="inp" placeholder="e.g. Globex Inc." value={client} onChange={(e) => setClient(e.target.value)} />
            </label>
            <label className="fld">
              <span className="lbl">Matter type</span>
              <select className="sel" value={matterType} onChange={(e) => setMatterType(e.target.value as MatterType)}>
                {MATTER_TYPES.map((t) => <option key={t} value={t}>{titleCase(t)}</option>)}
              </select>
            </label>
            <label className="fld">
              <span className="lbl">Description <span className="hint">Optional</span></span>
              <textarea className="ta" rows={3} placeholder="What is this matter about?" value={description} onChange={(e) => setDescription(e.target.value)} />
            </label>
          </div>
          <div className="dft">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn pri" disabled={busy || !name.trim()} onClick={submit}>{busy ? "Creating…" : "Create matter"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

const PROJ_CSS = `
.proj{--blue:#2c4a9e;--blue-soft:#dde5fb;--violet:#6b3fa0;--violet-soft:#ece3fb;--amber:#92600b;--amber-soft:#faecd3;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .proj{--blue:#a8b9f4;--blue-soft:#202944;--violet:#c9a8ef;--violet-soft:#28203c;--amber:#e3b567;--amber-soft:#332616;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.proj .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.proj .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .proj .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.proj .acts{margin-left:auto;display:flex;gap:8px}
    
.proj .filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.proj .chip{font:600 12px var(--sans);color:var(--ink-2);background:var(--surface);border:1px solid var(--border-strong);border-radius:8px;padding:6px 11px;cursor:pointer;display:inline-flex;gap:7px;align-items:center}
.proj .chip:hover{background:var(--surface-2)} .proj .chip.sel{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}
.proj .chip .ct{font:600 10px var(--mono);color:var(--ink-3)} .proj .chip.sel .ct{color:var(--accent)}
.proj .tray{border:1px dashed var(--border-strong);background:var(--inset);border-radius:12px;padding:13px 15px;margin-bottom:16px}
.proj .trh{display:flex;align-items:baseline;gap:9px;flex-wrap:wrap} .proj .trh b{font-size:13px} .proj .trsub{font-size:12px;color:var(--ink-3)}
.proj .trrow{display:flex;align-items:center;gap:10px;margin-top:9px;background:var(--surface);border:1px solid var(--border);border-radius:9px;padding:8px 11px;font-size:12.5px}
.proj .trrow .kind{font:600 9.5px var(--sans);text-transform:uppercase;letter-spacing:.04em;padding:2px 7px;border-radius:99px;background:var(--blue-soft);color:var(--blue)} .proj .trrow .kind.intake{background:var(--violet-soft);color:var(--violet)}
.proj .trrow .nm{font-weight:560;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:340px} .proj .trrow .sub2{color:var(--ink-3);font-size:11.5px}
.proj .trrow .sugbtn{margin-left:auto;font:500 11px var(--sans);color:var(--ink-2);background:var(--surface-2);border:1px solid var(--border);border-radius:7px;padding:5px 9px;cursor:pointer;white-space:nowrap} .proj .trrow .sugbtn:hover{border-color:var(--accent)} .proj .trrow .sugbtn b{color:var(--accent);font-weight:640}
.proj .trrow .sugbtn + .asg{margin-left:8px}
.proj .trrow .asg{margin-left:auto;font:600 11.5px var(--sans);color:var(--accent);background:var(--accent-soft);border:1px solid var(--accent);border-radius:7px;padding:5px 9px;cursor:pointer}
.proj .trmore{margin-top:8px;font-size:11.5px;color:var(--ink-3)}
.proj .mbanner{display:flex;align-items:center;gap:10px;background:var(--accent-soft);border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);border-radius:11px;padding:10px 14px;margin-bottom:14px;font-size:12.5px;color:var(--ink)}
.proj .mbanner .x{margin-left:auto;background:none;border:0;color:var(--ink-3);font-size:16px;cursor:pointer;line-height:1;padding:2px 6px}
.proj .tablewrap{border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);overflow-x:auto}
.proj table{border-collapse:collapse;width:100%;min-width:640px;font-size:13px}
.proj th{text-align:left;font:600 10px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:11px 15px;border-bottom:1px solid var(--border);white-space:nowrap}
.proj td{padding:12px 15px;border-bottom:1px solid var(--border);vertical-align:middle} .proj tbody tr:last-child td{border-bottom:0}
.proj tbody tr{cursor:pointer} .proj tbody tr:hover{background:var(--surface-2)}
.proj .mname{font-weight:640;color:var(--ink);text-decoration:none} .proj .mname:hover{color:var(--accent)}
.proj .mnum{font:500 10.5px var(--mono);color:var(--ink-3);margin-top:2px} .proj .muted{color:var(--ink-2)}
.proj .tag{font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);white-space:nowrap}
.proj .tag.blue{background:var(--blue-soft);color:var(--blue)} .proj .tag.violet{background:var(--violet-soft);color:var(--violet)} .proj .tag.amber{background:var(--amber-soft);color:var(--amber)}
 
.proj .ov{position:fixed;inset:0;background:rgba(12,15,22,.5);display:flex;align-items:center;justify-content:center;padding:20px;z-index:50}
.proj .dlg{width:100%;max-width:460px;max-height:calc(100vh - 40px);overflow:auto;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--shadow);display:flex;flex-direction:column}
.proj .dhd{padding:16px 18px;border-bottom:1px solid var(--border)} .proj .dhd h2{margin:0;font-size:15px;font-weight:660}
.proj .dbody{padding:16px 18px;display:flex;flex-direction:column;gap:14px}
.proj .fld{display:flex;flex-direction:column;gap:6px}
.proj .lbl{font-size:12px;font-weight:600;color:var(--ink-2)} .proj .lbl .hint{font-weight:400;color:var(--ink-3)}
.proj .inp,.proj .ta,.proj .sel{font:inherit;font-size:13px;color:var(--ink);background:var(--surface);border:1px solid var(--border-strong);border-radius:9px;padding:8px 10px;width:100%}
.proj .inp:focus,.proj .ta:focus,.proj .sel:focus{outline:2px solid var(--accent);outline-offset:1px}
.proj .ta{resize:vertical}
.proj .dft{padding:14px 18px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:8px}
`;
