"use client";

// Projects — matter/deal-room groupings that contracts and requests can belong to.
// Reskinned to the new mockup style (scoped `.proj`); see workflow-builder/page.tsx for the pattern.

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { projectsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { titleCase } from "@/lib/utils";
import type { ProjectType } from "@/lib/types";

const PROJECT_TYPES: ProjectType[] = [
  "general",
  "contract_review",
  "due_diligence",
  "regulatory",
];

function projectTypeTone(type: ProjectType): string {
  switch (type) {
    case "contract_review":
      return "blue";
    case "due_diligence":
      return "violet";
    case "regulatory":
      return "amber";
    default:
      return "slate";
  }
}

export default function ProjectsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  });
  const projects = data ?? [];

  return (
    <div className="proj">
      <style dangerouslySetInnerHTML={{ __html: PROJ_CSS }} />
      <div className="hd">
        <div>
          <h1>Projects</h1>
          <p className="sub">Organize contracts into matters, deal rooms and review workspaces.</p>
        </div>
        <div className="acts">
          <button className="btn pri" onClick={() => setCreateOpen(true)}>+ New project</button>
        </div>
      </div>

      {isLoading ? (
        <div className="grid">{[0, 1, 2].map((i) => <div key={i} className="pcard skel" />)}</div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load projects."}</div>
      ) : projects.length === 0 ? (
        <div className="empty">
          No projects yet. Create one to group related contracts, add folders, members and shares.
          <div style={{ marginTop: 12 }}><button className="btn pri" onClick={() => setCreateOpen(true)}>New project</button></div>
        </div>
      ) : (
        <div className="grid">
          {projects.map((p) => (
            <Link key={p.id} href={`/projects/${p.id}`} className="pcard">
              <div className="pch">
                <span className="pnm">{p.name}</span>
                <span className={`tag ${projectTypeTone(p.project_type)}`}>{titleCase(p.project_type)}</span>
              </div>
              <div className="pd">{p.description?.trim() || "No description."}</div>
              <div className="pmeta"><span /><span className="open">Open →</span></div>
            </Link>
          ))}
        </div>
      )}

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["projects"] });
          notify("Project created", "success");
          setCreateOpen(false);
        }}
      />
    </div>
  );
}

function CreateProjectModal({
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
  const [description, setDescription] = useState("");
  const [projectType, setProjectType] = useState<ProjectType>("general");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  // Focus management: move focus into the dialog on open, trap Tab inside it,
  // close on Escape, and return focus to the trigger on close.
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
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const els = focusables();
      if (els.length === 0) return;
      const first = els[0];
      const last = els[els.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previouslyFocused?.focus?.();
    };
  }, [open, onClose]);

  if (!open) return null;

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await projectsApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        project_type: projectType,
      });
      setName("");
      setDescription("");
      setProjectType("general");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create project", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="proj">
      <div className="ov" onClick={onClose}>
        <div
          className="dlg"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          ref={dialogRef}
          tabIndex={-1}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="dhd"><h2 id={titleId}>New project</h2></div>
          <div className="dbody">
            <label className="fld">
              <span className="lbl">Name</span>
              <input
                className="inp"
                placeholder="e.g. Acme acquisition — due diligence"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label className="fld">
              <span className="lbl">Description <span className="hint">Optional</span></span>
              <textarea
                className="ta"
                rows={3}
                placeholder="What is this project for?"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </label>
            <label className="fld">
              <span className="lbl">Project type</span>
              <select
                className="sel"
                value={projectType}
                onChange={(e) => setProjectType(e.target.value as ProjectType)}
              >
                {PROJECT_TYPES.map((t) => (
                  <option key={t} value={t}>{titleCase(t)}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="dft">
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn pri" disabled={busy || !name.trim()} onClick={submit}>
              {busy ? "Creating…" : "Create project"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const PROJ_CSS = `
.proj{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--blue:#2c4a9e;--blue-soft:#dde5fb;--violet:#6b3fa0;--violet-soft:#ece3fb;--amber:#92600b;--amber-soft:#faecd3;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .proj{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--blue:#a8b9f4;--blue-soft:#202944;--violet:#c9a8ef;--violet-soft:#28203c;--amber:#e3b567;--amber-soft:#332616;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.proj .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.proj .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .proj .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.proj .acts{margin-left:auto;display:flex;gap:8px}
.proj .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer} .proj .btn:hover{background:var(--surface-2)} .proj .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .proj .btn.pri:hover{filter:brightness(1.06)} .proj .btn[disabled]{opacity:.6;pointer-events:none}
.proj .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.proj .pcard{text-align:left;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:15px 16px;cursor:pointer;transition:.12s;display:flex;flex-direction:column;gap:10px;font:inherit;color:inherit;min-height:120px;text-decoration:none}
.proj .pcard:hover{border-color:var(--accent);transform:translateY(-1px)}
.proj .pcard.skel{pointer-events:none;background:var(--surface-2);border-style:dashed;animation:projpulse 1.4s ease-in-out infinite}
@keyframes projpulse{50%{opacity:.55}}
.proj .pch{display:flex;align-items:flex-start;gap:8px;flex-wrap:wrap;justify-content:space-between}
.proj .pnm{font-weight:660;font-size:14px}
.proj .tag{font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);white-space:nowrap}
.proj .tag.blue{background:var(--blue-soft);color:var(--blue)} .proj .tag.violet{background:var(--violet-soft);color:var(--violet)} .proj .tag.amber{background:var(--amber-soft);color:var(--amber)}
.proj .pd{font-size:12.5px;color:var(--ink-2);line-height:1.5;flex:1}
.proj .pmeta{display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--border);padding-top:9px;font-size:12px;color:var(--ink-3)} .proj .pmeta .open{color:var(--accent);font-weight:600}
.proj .empty{border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:26px;text-align:center;font-size:13px;color:var(--ink-2)} .proj .empty.err{border-color:color-mix(in srgb,#bb4835 40%,var(--border));color:#bb4835}
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
