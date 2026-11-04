"use client";

// Workflows — every routing workflow, grouped by the agreement type it serves, each drawn as the
// pipeline it runs (start → AI → drafting → approval → signature → …). Editing happens on the
// full-screen designer (/workflow-builder/[id]). Admin-only: workflow definitions are a privileged
// configuration surface.

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight, Bell, CheckCircle2, CirclePause, FilePen, Flag, GitBranch, Handshake, Layers, PenLine, Play, Plus, Search, ShieldCheck, Sparkles,
  UserRound, Wand2, Zap, type LucideIcon,
} from "lucide-react";
import { workflowsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import type { Workflow } from "@/lib/types";
import { useRequestForms } from "../intake/_agreement-forms";
import { MockupShell } from "../intake/_legal-intake";
import { questionsFor, typeKey, typeOptions, whenText } from "@/lib/workflow-types";

/** How each kind of step looks on the pipeline: a word, an icon, and a tone. */
const STEP: Record<string, { label: string; Icon: LucideIcon; tone: string }> = {
  start: { label: "Start", Icon: Play, tone: "mute" },
  ai_task: { label: "AI", Icon: Sparkles, tone: "vio" },
  human_task: { label: "Human", Icon: UserRound, tone: "sky" },
  clm_draft: { label: "Draft", Icon: FilePen, tone: "ind" },
  counterparty: { label: "Counterparty", Icon: Handshake, tone: "amb" },
  approval: { label: "Approval", Icon: ShieldCheck, tone: "emr" },
  signature: { label: "Signature", Icon: PenLine, tone: "emr" },
  notify: { label: "Notify", Icon: Bell, tone: "mute" },
  end: { label: "End", Icon: Flag, tone: "mute" },
};
// The last step of every workflow ("Activate") is stored as an agent-less AI task, but nothing AI
// runs in it: it hands the signed contract over to the Active stage. Show it for what it is.
const stepOf = (s: { type: string; stage?: string | null; name?: string | null }) =>
  s.stage === "active" || s.name === "Activate"
    ? { label: "Activate", Icon: Zap, tone: "emr" }
    : STEP[s.type] ?? { label: s.type, Icon: GitBranch, tone: "mute" };

type Filter = "all" | "on" | "off" | "builtin";

/** A number that counts up to its value, so the cards feel live. */
function CountUp({ n }: { n: number }) {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) { setV(n); return; }
    let raf = 0;
    const t0 = performance.now();
    const tick = (t: number) => {
      const k = Math.min(1, (t - t0) / 650);
      setV(Math.round(n * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [n]);
  return <>{v}</>;
}

/** The pipeline a workflow runs, as connected nodes that draw in one after another. */
function Pipeline({ steps, base }: { steps: Workflow["steps"]; base: number }) {
  if (!steps.length) return <span className="wfnone">No steps yet</span>;
  return (
    <ol className="wfpipe" aria-label="Steps">
      {steps.map((s, i) => {
        const d = stepOf(s);
        return (
          <li key={s.id ?? i} style={{ animationDelay: `${base + i * 70}ms` }}>
            {i > 0 && <span className="wfline" style={{ animationDelay: `${base + i * 70 - 40}ms` }} />}
            <span className={`wfnode t-${d.tone}`} title={s.name ? `${d.label}: ${s.name}` : d.label}><d.Icon size={14} /></span>
            <em>{d.label}</em>
          </li>
        );
      })}
    </ol>
  );
}

export default function WorkflowBuilderPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  const isAdmin = can(user, "admin_panel:access");
  const [seeding, setSeeding] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");

  const { data, isLoading, error } = useQuery({ queryKey: ["flows"], queryFn: workflowsApi.listFlows, enabled: isAdmin });
  const flows = useMemo(() => data ?? [], [data]);
  const { data: forms } = useRequestForms();

  const q = search.trim().toLowerCase();
  const keep = (f: Workflow) =>
    (filter === "all" || (filter === "on" && f.enabled) || (filter === "off" && !f.enabled) || (filter === "builtin" && f.is_builtin)) &&
    (!q || `${f.name} ${f.description ?? ""}`.toLowerCase().includes(q));
  const narrowed = filter !== "all" || !!q;

  // Workflows sit under the agreement type they are for; untyped ones only serve
  // email and chat requests.
  const groups = typeOptions(forms).map((t) => {
    const labels = Object.fromEntries(questionsFor(forms, t.form, t.agreement_type).map((qq) => [qq.field, qq.label]));
    const all = flows.filter((f) => typeKey(f) === t.key);
    return { ...t, labels, all, flows: all.filter(keep) };
  });
  const untyped = flows.filter((f) => !typeKey(f) || !groups.some((g) => g.key === typeKey(f)));
  const untypedShown = untyped.filter(keep);

  const covered = groups.filter((g) => g.all.length > 0).length;
  const enabledN = flows.filter((f) => f.enabled).length;
  const stepsN = flows.reduce((s, f) => s + f.steps.length, 0);
  const kcards = [
    { l: "Workflows", n: flows.length, sub: `${enabledN} enabled`, I: GitBranch, c: "ind", f: "all" as Filter },
    { l: "Types covered", n: covered, sub: `of ${groups.length} agreement types`, I: Layers, c: "vio", f: "all" as Filter },
    { l: "Enabled", n: enabledN, sub: "routing requests now", I: CheckCircle2, c: "emr", f: "on" as Filter },
    { l: "Disabled", n: flows.length - enabledN, sub: "paused", I: CirclePause, c: "amb", f: "off" as Filter },
    { l: "Steps in all", n: stepsN, sub: "across every workflow", I: Wand2, c: "sky", f: "all" as Filter },
  ];

  const renderCard = (f: Workflow, when: string | null, i: number) => (
    <button key={f.id} className={`wfcard${f.enabled ? "" : " off"}`} style={{ animationDelay: `${Math.min(i, 8) * 60}ms` }} onClick={() => router.push(`/workflow-builder/${f.id}`)}>
      <span className="wfhd">
        <span className="wfname">{f.name}</span>
        {f.is_builtin && <span className="wftag">Built-in</span>}
        <span className={`wfen ${f.enabled ? "on" : "off"}`}><i />{f.enabled ? "Enabled" : "Disabled"}</span>
      </span>
      {when ? <span className="wfwhen"><b>Chosen when</b>{when}</span> : f.description ? <span className="wfdesc">{f.description}</span> : null}
      <Pipeline steps={f.steps} base={140 + Math.min(i, 8) * 60} />
      <span className="wffoot">
        <span>{f.steps.length} step{f.steps.length === 1 ? "" : "s"} · v{f.version}</span>
        <span className="wfopen">Open designer<ArrowRight size={14} /></span>
      </span>
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
    <MockupShell
      icon={<GitBranch />}
      title="Workflows"
      subtitle="how legal requests are routed through AI tasks, drafting, approvals and signature"
      actions={isAdmin ? (
        <>
          <button className="btn" disabled={seeding} onClick={seed}><Wand2 size={14} />{seeding ? "Seeding…" : "Seed defaults"}</button>
          <button className="btn pri" onClick={() => router.push("/workflow-builder/new")}><Plus size={14} />New workflow</button>
        </>
      ) : undefined}
    >
      <div className="wfl">
        <style dangerouslySetInnerHTML={{ __html: WFL_CSS }} />
        {!isAdmin ? (
          <div className="wfempty"><div className="eic"><ShieldCheck size={22} /></div><p className="et">Administrators only</p><p className="ed">Workflow definitions can only be viewed and edited by administrators.</p></div>
        ) : isLoading ? (
          <div className="wfgrid">{[0, 1, 2, 3].map((i) => <div key={i} className="wfcard skel" />)}</div>
        ) : error ? (
          <div className="wfempty err">{error instanceof Error ? error.message : "Couldn't load workflows."}</div>
        ) : flows.length === 0 ? (
          <div className="wfempty wfin">
            <div className="eic"><GitBranch size={22} /></div>
            <p className="et">No workflows yet</p>
            <p className="ed">Seed the built-in defaults, or create one to automate how requests flow through your team.</p>
            <div className="wfctas"><button className="btn pri" disabled={seeding} onClick={seed}><Wand2 size={14} />Seed defaults</button><button className="btn" onClick={() => router.push("/workflow-builder/new")}><Plus size={14} />New workflow</button></div>
          </div>
        ) : (
          <>
            <div className="kcards wfkpis">
              {kcards.map((k, i) => (
                <button key={k.l} type="button" className={`kcard kc-${k.c}${filter === k.f && k.f !== "all" ? " on" : ""}`} onClick={() => setFilter(k.f === filter ? "all" : k.f)} tabIndex={-1} aria-hidden="true" style={{ animationDelay: `${i * 50}ms` }}>
                  <span className="kic"><k.I className="ic" aria-hidden /></span>
                  <span className="ktx"><span className="kl">{k.l}</span><b className="kn num"><CountUp n={k.n} /></b><span className="ks">{k.sub}</span></span>
                </button>
              ))}
            </div>

            <div className="wftools">
              <div className="stabs" role="group" aria-label="Filter workflows" style={{ marginTop: 0 }}>
                {([["all", "All", flows.length, "ind"], ["on", "Enabled", enabledN, "emr"], ["off", "Disabled", flows.length - enabledN, "amb"], ["builtin", "Built-in", flows.filter((f) => f.is_builtin).length, "vio"]] as const).map(([k, l, n, c]) => (
                  <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)} className={`stab${filter === k ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${c})` }}>
                    <i aria-hidden /><span className="tl">{l}</span><span className="tn num">{n}</span>
                  </button>
                ))}
              </div>
              <div className="search" style={{ marginLeft: "auto" }}>
                <Search className="ic" />
                <input aria-label="Search workflows" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search workflows…" />
              </div>
            </div>

            <div className="wfgroups" key={`${filter}-${q}`}>
              {groups.filter((g) => !narrowed || g.flows.length).map((g, gi) => (
                <section key={g.key} className="wfgrp" style={{ animationDelay: `${Math.min(gi, 6) * 70}ms` }}>
                  <div className="wfgh">
                    <span className={`wfgic${g.all.length ? "" : " empty"}`}><GitBranch size={15} /></span>
                    <h2>{g.label}</h2>
                    <span className="wfgc">{g.all.length ? `${g.all.length} workflow${g.all.length === 1 ? "" : "s"}` : "none yet"}</span>
                    <span className="wfgl" />
                    <button className="btn sm" onClick={() => router.push(`/workflow-builder/new?type=${encodeURIComponent(g.key)}`)}><Plus size={13} />New {g.short} workflow</button>
                  </div>
                  {g.flows.length > 0 ? (
                    <div className="wfgrid">{g.flows.map((f, i) => renderCard(f, whenText(f.criteria.conditions, g.labels), i))}</div>
                  ) : (
                    <button className="wfadd" onClick={() => router.push(`/workflow-builder/new?type=${encodeURIComponent(g.key)}`)}>
                      <span className="wfaic"><Plus size={18} /></span>
                      <span><b>No workflow for this type yet</b><em>Requests of this type wait for a person to pick one. Create a workflow to route them automatically.</em></span>
                      <ArrowRight size={16} />
                    </button>
                  )}
                </section>
              ))}
              {untypedShown.length > 0 && (
                <section className="wfgrp">
                  <div className="wfgh">
                    <span className="wfgic"><Layers size={15} /></span>
                    <h2>No agreement type</h2>
                    <span className="wfgc">{untyped.length}</span>
                    <span className="wfgl" />
                  </div>
                  <p className="wfnote">Email and chat requests only: the AI suggests one and a person confirms. Give one a type to use it for form requests.</p>
                  <div className="wfgrid">{untypedShown.map((f, i) => renderCard(f, null, i))}</div>
                </section>
              )}
              {narrowed && groups.every((g) => !g.flows.length) && untypedShown.length === 0 && (
                <div className="wfempty wfin"><div className="eic"><Search size={22} /></div><p className="et">No workflows match</p><p className="ed">Try another filter or search word.</p></div>
              )}
            </div>
          </>
        )}
      </div>
    </MockupShell>
  );
}

const WFL_CSS = `
.wfl{color:var(--ink);font:400 13px/1.5 var(--font-sans)}
.wfl *{box-sizing:border-box}
@keyframes wfrise{from{opacity:0;transform:translateY(16px) scale(.99)}to{opacity:1;transform:none}}
.wfl .wfin{animation:wfrise .5s cubic-bezier(.2,.8,.2,1) both}
.wfl .wfkpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:4px 0 0}
.wfl .wfkpis .kcard{padding:14px;gap:12px;cursor:pointer;opacity:0;animation:wfrise .55s cubic-bezier(.2,.8,.2,1) forwards}
.wfl .wfkpis .kcard.on{box-shadow:0 0 0 2px var(--t)}
.wfl .wfkpis .ks{font-size:11px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wfl .wftools{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:16px 0 14px}
.wfl .wfgroups{display:flex;flex-direction:column;gap:22px}
.wfl .wfgrp{opacity:0;animation:wfrise .55s cubic-bezier(.2,.8,.2,1) forwards}
.wfl .wfgh{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.wfl .wfgh h2{margin:0;font-size:15px;font-weight:680;letter-spacing:-.01em}
.wfl .wfgic{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent)}
.wfl .wfgic.empty{background:var(--surface-2);color:var(--ink-3)}
.wfl .wfgc{font-size:11.5px;font-weight:600;color:var(--ink-2);background:var(--surface-2);padding:2px 10px;border-radius:99px}
.wfl .wfgl{flex:1;height:1px;background:var(--border)}
.wfl .btn.sm{padding:6px 12px;font-size:12px;display:inline-flex;align-items:center;gap:6px}
.wfl .wfnote{margin:-4px 0 12px;font-size:12.5px;color:var(--ink-3)}
.wfl .wfgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:14px}
.wfl .wfcard{position:relative;display:flex;flex-direction:column;gap:12px;text-align:left;padding:16px 18px;border-radius:20px;border:0;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);font:inherit;color:inherit;cursor:pointer;opacity:0;animation:wfrise .55s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .22s cubic-bezier(.2,.8,.2,1),box-shadow .25s;overflow:hidden}
.wfl .wfcard::after{content:"";position:absolute;inset:0;pointer-events:none;opacity:0;transition:opacity .3s;background:radial-gradient(240px circle at 85% 0%,color-mix(in srgb,var(--accent) 12%,transparent),transparent 65%)}
.wfl .wfcard:hover{transform:translateY(-4px);box-shadow:0 20px 38px -24px rgba(20,26,40,.5),0 0 0 1px var(--accent)}
.wfl .wfcard:hover::after{opacity:1}
.wfl .wfcard.off{background:var(--inset)}
.wfl .wfcard.off .wfnode{filter:grayscale(.7);opacity:.7}
.wfl .wfcard.skel{pointer-events:none;height:150px;background:var(--surface-2);box-shadow:none;animation:wfpulse 1.4s ease-in-out infinite;opacity:1}
@keyframes wfpulse{50%{opacity:.55}}
.wfl .wfhd{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.wfl .wfname{font-size:15px;font-weight:680;letter-spacing:-.01em}
.wfl .wftag{font:700 9px var(--font-sans);letter-spacing:.06em;text-transform:uppercase;background:var(--accent-soft);color:var(--accent);padding:2px 8px;border-radius:99px}
.wfl .wfen{margin-left:auto;display:inline-flex;align-items:center;gap:6px;font:600 10.5px var(--font-sans);padding:3px 10px;border-radius:99px}
.wfl .wfen i{width:7px;height:7px;border-radius:50%;background:currentColor}
.wfl .wfen.on{background:var(--good-soft);color:var(--good)}.wfl .wfen.on i{animation:wfdot 2s ease-in-out infinite}
.wfl .wfen.off{background:var(--surface-2);color:var(--ink-3)}
@keyframes wfdot{50%{opacity:.35;transform:scale(.8)}}
.wfl .wfdesc{font-size:12.5px;color:var(--ink-2);line-height:1.5}
.wfl .wfwhen{display:flex;flex-direction:column;gap:2px;padding:8px 12px;border-radius:12px;background:var(--accent-soft);color:var(--accent);font-size:12px;line-height:1.4}
.wfl .wfwhen b{font:700 9.5px var(--font-sans);letter-spacing:.07em;text-transform:uppercase;opacity:.8}
.wfl .wfpipe{list-style:none;margin:2px 0 0;padding:6px 0 0;display:flex;align-items:flex-start;flex-wrap:nowrap}
.wfl .wfpipe li{display:flex;flex-direction:column;align-items:center;gap:4px;position:relative;opacity:0;animation:wfpop .5s cubic-bezier(.3,1.4,.4,1) forwards;flex:1 1 0;min-width:0}
@keyframes wfpop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
.wfl .wfnode{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;position:relative;z-index:1;transition:transform .2s}
.wfl .wfcard:hover .wfnode{transform:translateY(-2px)}
.wfl .wfnode.t-mute{background:var(--surface-2);color:var(--ink-3)}
.wfl .wfnode.t-vio{background:color-mix(in srgb,var(--c-vio) 16%,transparent);color:var(--c-vio)}
.wfl .wfnode.t-sky{background:color-mix(in srgb,var(--c-sky) 16%,transparent);color:var(--c-sky)}
.wfl .wfnode.t-ind{background:color-mix(in srgb,var(--c-ind) 16%,transparent);color:var(--c-ind)}
.wfl .wfnode.t-amb{background:color-mix(in srgb,var(--c-amb) 18%,transparent);color:var(--c-amb)}
.wfl .wfnode.t-emr{background:color-mix(in srgb,var(--c-emr) 16%,transparent);color:var(--c-emr)}
.wfl .wfpipe em{font-style:normal;font-size:9.5px;color:var(--ink-3);white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.wfl .wfline{position:absolute;top:15px;right:50%;width:100%;height:2px;margin-right:15px;max-width:calc(100% - 4px);background:var(--border-strong);transform-origin:right;animation:wfdraw .45s ease both}
@keyframes wfdraw{from{transform:scaleX(0)}to{transform:none}}
.wfl .wfnone{font-size:12px;color:var(--ink-3)}
.wfl .wffoot{display:flex;align-items:center;justify-content:space-between;padding-top:10px;margin-top:auto;border-top:1px dashed var(--border-strong);font-size:12px;color:var(--ink-3)}
.wfl .wfopen{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 14px;border-radius:99px;background:var(--accent-soft);color:var(--accent);font-weight:650;transition:background .2s,color .2s,box-shadow .2s,gap .2s}
.wfl .wfcard:hover .wfopen{background:var(--accent);color:var(--accent-ink);gap:10px;box-shadow:0 10px 20px -10px var(--accent)}
.wfl .wfadd{display:flex;align-items:center;gap:14px;width:100%;text-align:left;padding:14px 18px;border-radius:18px;border:1.5px dashed var(--border-strong);background:transparent;color:var(--ink-3);font:inherit;cursor:pointer;transition:border-color .2s,background .2s,transform .2s}
.wfl .wfadd:hover{border-color:var(--accent);background:var(--accent-soft);transform:translateY(-2px)}
.wfl .wfaic{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;background:var(--surface-2);color:var(--accent);flex:none;transition:transform .25s}
.wfl .wfadd:hover .wfaic{transform:rotate(90deg)}
.wfl .wfadd span:nth-child(2){display:flex;flex-direction:column;flex:1;min-width:0}
.wfl .wfadd b{color:var(--ink);font-size:13px}.wfl .wfadd em{font-style:normal;font-size:12px}
.wfl .wfempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px}
.wfl .wfempty.err{color:var(--crit)}
.wfl .wfempty .eic{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:10px}
.wfl .wfempty .et{margin:0;font-weight:700;font-size:15px}
.wfl .wfempty .ed{margin:3px 0 0;font-size:12.5px;color:var(--ink-2);max-width:420px}
.wfl .wfctas{display:flex;gap:8px;margin-top:14px}
@media (max-width:1400px){.wfl .wfkpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:900px){.wfl .wfgrid{grid-template-columns:1fr}}
@media (prefers-reduced-motion:reduce){.wfl *{animation:none!important;transition:none!important}.wfl .wfgrp,.wfl .wfcard,.wfl .wfkpis .kcard,.wfl .wfpipe li{opacity:1!important}}
`;
