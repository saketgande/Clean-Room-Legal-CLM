"use client";

// Workflow-engine builder — list org flows in the new mockup style (scoped `.wfl`).
// Editing happens on the full-screen designer (/workflow-builder/[id]).
// Admin-only: workflow definitions are a privileged configuration surface.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { workflowsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import type { Workflow } from "@/lib/types";
import { useRequestForms } from "../intake/_agreement-forms";
import { questionsFor, typeKey, typeOptions, whenText } from "@/lib/workflow-types";

const STEP_SHORT: Record<string, string> = {
  start: "Start", ai_task: "AI", human_task: "Human", clm_draft: "Draft",
  counterparty: "Counterparty", approval: "Approval", signature: "Signature", notify: "Notify", end: "End",
};
const isRung = (t: string) => t === "approval" || t === "signature";

function Ladder({ steps }: { steps: Workflow["steps"] }) {
  if (!steps.length) return <span className="dim">No steps yet</span>;
  return (
    <div className="ladder">
      {steps.map((s, i) => (
        <span key={s.id ?? i} className="lstep">
          {i > 0 && <span className="arr">→</span>}
          <span className={`lchip${isRung(s.type) ? " rung" : ""}`}>{STEP_SHORT[s.type] ?? s.type}</span>
        </span>
      ))}
    </div>
  );
}

export default function WorkflowBuilderPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  const isAdmin = can(user, "admin_panel:access");
  const [seeding, setSeeding] = useState(false);

  const { data, isLoading, error } = useQuery({ queryKey: ["flows"], queryFn: workflowsApi.listFlows, enabled: isAdmin });
  const flows = data ?? [];
  const { data: forms } = useRequestForms();
  // Workflows sit under the agreement type they are for; untyped ones only serve
  // email and chat requests.
  const groups = typeOptions(forms).map((t) => {
    const labels = Object.fromEntries(questionsFor(forms, t.form, t.agreement_type).map((q) => [q.field, q.label]));
    return { ...t, labels, flows: flows.filter((f) => typeKey(f) === t.key) };
  });
  const untyped = flows.filter((f) => !typeKey(f) || !groups.some((g) => g.key === typeKey(f)));

  const renderCard = (f: Workflow, when: string | null) => (
            <button key={f.id} className="wcard" onClick={() => router.push(`/workflow-builder/${f.id}`)}>
              <div className="wch">
                <span className="wnm">{f.name}</span>
                {f.is_builtin && <span className="tag">Built-in</span>}
                <span className={`en ${f.enabled ? "on" : "off"}`}>{f.enabled ? "Enabled" : "Disabled"}</span>
              </div>
              {when ? <div className="when"><b>Chosen when</b> {when}</div> : f.description && <div className="wd">{f.description}</div>}
              <Ladder steps={f.steps} />
              <div className="wmeta"><span>{f.steps.length} step{f.steps.length === 1 ? "" : "s"}</span><span className="edit">Open →</span></div>
            </button>
          );

  async function seed() {
    setSeeding(true);
    try {
      const res = await workflowsApi.seedFlows();
      qc.invalidateQueries({ queryKey: ["flows"] });
      notify(`Seeded ${res.added} default workflow${res.added === 1 ? "" : "s"}`, "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Seed failed", "error"); }
    finally { setSeeding(false); }
  }

  return (
    <div className="wfl">
      <style dangerouslySetInnerHTML={{ __html: WFL_CSS }} />
      <div className="hd">
        <div><h1>Workflows</h1><p className="sub">Automated routing of legal requests through AI tasks, drafting, approvals, and signature.</p></div>
        {isAdmin && (
          <div className="acts">
            <button className="btn" disabled={seeding} onClick={seed}>{seeding ? "Seeding…" : "Seed defaults"}</button>
            <button className="btn pri" onClick={() => router.push("/workflow-builder/new")}>+ New workflow</button>
          </div>
        )}
      </div>

      {!isAdmin ? (
        <div className="empty">Workflow definitions can only be viewed and edited by administrators.</div>
      ) : isLoading ? (
        <div className="grid">{[0, 1, 2, 3].map((i) => <div key={i} className="wcard skel" />)}</div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load workflows."}</div>
      ) : flows.length === 0 ? (
        <div className="empty">
          No workflows yet. Seed the built-in defaults, or create one to automate how requests flow through your team.
          <div style={{ marginTop: 12 }}><button className="btn pri" disabled={seeding} onClick={seed}>Seed defaults</button></div>
        </div>
      ) : (
        <div className="groups">
          {groups.map((g) => (
            <section key={g.key} className={`grp${g.flows.length ? "" : " nowf"}`}>
              <div className="gh">
                <h2>{g.label}</h2>
                <span className="dim">{g.flows.length ? `${g.flows.length} workflow${g.flows.length === 1 ? "" : "s"}` : "None yet: requests of this type wait for a person to pick"}</span>
                <span className="gl" />
                <button className="btn" onClick={() => router.push(`/workflow-builder/new?type=${encodeURIComponent(g.key)}`)}>+ New {g.short} workflow</button>
              </div>
              {g.flows.length > 0 && <div className="grid">{g.flows.map((f) => renderCard(f, whenText(f.criteria.conditions, g.labels)))}</div>}
            </section>
          ))}
          {untyped.length > 0 && (
            <section className="grp">
              <div className="gh">
                <h2>No agreement type</h2>
                <span className="dim">Email and chat requests only: the AI suggests one and a person confirms. Give one a type to use it for form requests.</span>
                <span className="gl" />
              </div>
              <div className="grid">{untyped.map((f) => renderCard(f, null))}</div>
            </section>
          )}
        </div>
      )}
    </div>
  );
}

const WFL_CSS = `
.wfl{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .wfl{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.wfl .dim{color:var(--ink-3)}
.wfl .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.wfl .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .wfl .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.wfl .acts{margin-left:auto;display:flex;gap:8px}
    
.wfl .groups{display:flex;flex-direction:column;gap:22px}
.wfl .grp .gh{display:flex;align-items:center;gap:10px;margin-bottom:10px} .wfl .grp h2{margin:0;font-size:14px;font-weight:650}
.wfl .grp .gl{flex:1;height:1px;background:var(--border)} .wfl .grp.nowf .gh{margin-bottom:0}
.wfl .when{padding:7px 10px;border-radius:8px;background:var(--accent-soft);color:var(--accent);font-size:12px;line-height:1.45} .wfl .when b{font-weight:600}
.wfl .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.wfl .wcard{text-align:left;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:15px 16px;cursor:pointer;transition:.12s;display:flex;flex-direction:column;gap:10px;font:inherit;color:inherit;min-height:120px}
.wfl .wcard:hover{border-color:var(--accent);transform:translateY(-1px)}
.wfl .wcard.skel{pointer-events:none;background:var(--surface-2);border-style:dashed;animation:wflpulse 1.4s ease-in-out infinite}
@keyframes wflpulse{50%{opacity:.55}}
.wfl .wch{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.wfl .wnm{font-weight:660;font-size:14px} .wfl .tag{font:600 9px var(--sans);letter-spacing:.05em;text-transform:uppercase;background:var(--accent-soft);color:var(--accent);padding:2px 7px;border-radius:99px}
.wfl .en{margin-left:auto;font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px} .wfl .en.on{background:var(--good-soft);color:var(--good)} .wfl .en.off{background:var(--surface-2);color:var(--ink-3)}
.wfl .wd{font-size:12.5px;color:var(--ink-2);line-height:1.5}
.wfl .ladder{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:auto}
.wfl .lstep{display:inline-flex;align-items:center;gap:5px} .wfl .arr{color:var(--ink-3);font-size:11px}
.wfl .lchip{font:600 10.5px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:6px;padding:2px 7px} .wfl .lchip.rung{background:var(--accent-soft);color:var(--accent)}
.wfl .wmeta{display:flex;align-items:center;justify-content:space-between;border-top:1px solid var(--border);padding-top:9px;font-size:12px;color:var(--ink-3)} .wfl .wmeta .edit{color:var(--accent);font-weight:600}
 
`;
