"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Ban, ChevronRight, ClipboardList, FileCheck, FilePen, Lock, RefreshCw, XCircle, Inbox, Bell, Bot, Check, ChevronDown, ChevronUp, DoorOpen, FileText, Paperclip, PenLine, Plus, RotateCw, Search, ShieldCheck, Stamp, Trash2, Users, Mail, MessageSquare, X } from "lucide-react";
import { useRouter } from "next/navigation";
import type { LucideIcon } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Field, Input, Modal, Select, Table, TD, TH, THead,
  TR, Textarea,
} from "@/components/ui";
import { intakeApi, contractsApi, approvalsApi, approvalChainsApi, playbooksApi, workflowsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";

import { can } from "@/lib/intake";
import type { IntakeDraft } from "@/lib/types";

import { LegalIntakeBoard, MockupShell, counterpartyOf } from "./_legal-intake";
import { RequestOverview } from "./_request-overview";
import { OpenRequest } from "./_open-request";

import { AGREEMENT_FORMS, AgreementWizard, type AgreementFormDef } from "./_agreement-forms";
import { LegalQuestionForm } from "./_legal-question-form";
import { useScreenAccess } from "@/lib/screen-access";
import { isOpenRequest } from "@/lib/request-status";

// Worklists don't need background polling — refetch when the tab regains
// focus (covers "left it open, came back") and rely on each mutation's own
// invalidateQueries for immediate feedback on your own actions. Trade-off:
// sla_status is server-computed, so a ticket crossing at_risk -> overdue
// purely from elapsed time won't repaint until the tab is refocused or
// reloaded (the ticking clock below only re-renders relative-time text, not
// this posture).
const LIVE_POLL = { refetchOnWindowFocus: true } as const;

export default function IntakePage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  // FR-9: UI-layer control gating only — the API independently re-verifies
  // every ADD request (FR-10/FR-13); this is a usability aid, not the
  // security boundary.
  const { canAdd } = useScreenAccess("intake");
  const [section, setSection] = useState(isStaff ? "queue" : "new");
  const [detailId, setDetailIdRaw] = useState<string | null>(null);
  // Opening (or leaving) a request swaps the whole view; start it at the top, not wherever the queue was scrolled.
  const setDetailId = (id: string | null) => {
    setDetailIdRaw(id);
    requestAnimationFrame(() => document.querySelector(".li-shell .scrollarea")?.scrollTo({ top: 0 }));
  };
  // Deep-link: /intake?open=<id> opens that request's detail (used by My Work);
  // /intake?new=… reopens New request (so browser Back from Ask Aegis returns
  // to the form the user came from, not the queue).
  useEffect(() => {
    const qs = new URLSearchParams(window.location.search);
    const o = qs.get("open");
    if (o) setDetailId(o);
    else if (qs.get("new")) setSection("new");
  }, []);
  // Leaving New request (to another section or a request's detail) drops its
  // ?new= marker. Transition-based, so the initial render — before ?new= has
  // restored the section — never removes the marker it is about to use.
  const wasOnNew = useRef(false);
  useEffect(() => {
    const onNew = section === "new" && !detailId;
    if (wasOnNew.current && !onNew) setNewRequestUrl(null);
    wasOnNew.current = onNew;
  }, [section, detailId]);

  const { data: listData } = useQuery({
    queryKey: ["intake-list"], queryFn: () => intakeApi.list(), enabled: isStaff, ...LIVE_POLL,
  });
  // Count-pills on the tabs — the reference's at-a-glance queue signal.

  return (
    <MockupShell
      title="Legal Intake"
      icon={<Inbox />}
      hideHeading={!!detailId}
      actions={
        <>
          <button
            className="primary"
            disabled={!canAdd}
            title={canAdd ? undefined : "You don't have add access to this screen"}
            onClick={() => { setSection("new"); setDetailId(null); }}
          >
            <Plus className="h-4 w-4" />New request
          </button>
        </>
      }
    >
      {isStaff && section !== "queue" && !detailId ? (
        <button onClick={() => setSection("queue")} className="btn sm" style={{ marginTop: 14 }}>← Back to All requests</button>
      ) : null}

      {detailId ? (
        <RequestOverview key={detailId} id={detailId} canManage={isStaff} onBack={() => setDetailId(null)} onOpenRequest={setDetailId} />
      ) : (
        <>
          {section === "queue" && <LegalIntakeBoard onOpen={setDetailId} mode="pool" />}
          {section === "new" && <NewRequestTab onFiled={setDetailId} />}
        </>
      )}
    </MockupShell>
  );
}

/** Mirror the New request view in the URL (replace, not push: it's the same page). */
function setNewRequestUrl(view: "catalog" | "legal_question" | null) {
  const qs = new URLSearchParams(window.location.search);
  const want = view === "legal_question" ? "legal_question" : view ? "1" : null;
  if ((qs.get("new") ?? null) === want) return;
  if (want) qs.set("new", want);
  else qs.delete("new");
  const q = qs.toString();
  window.history.replaceState(null, "", q ? `/intake?${q}` : "/intake");
}

function NewRequestTab({ onFiled }: { onFiled: (id: string) => void }) {
  const [mode, setMode] = useState<"catalog" | "agreement" | "legal_question">("catalog");
  // Restore the legal-question form when coming back to /intake?new=legal_question
  // (after mount, so the server render and hydration agree).
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("new") === "legal_question") setMode("legal_question");
  }, []);
  useEffect(() => {
    setNewRequestUrl(mode === "legal_question" ? "legal_question" : "catalog");
  }, [mode]);
  const router = useRouter();
  const [agreementDef, setAgreementDef] = useState<AgreementFormDef | null>(null);
  const [draft, setDraft] = useState<IntakeDraft | null>(null);
  const [typeSearch, setTypeSearch] = useState("");
  const [ask, setAsk] = useState("");
  const { data: recentReqs } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list(), retry: false });
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: drafts } = useQuery({ queryKey: ["intake-drafts"], queryFn: () => intakeApi.listDrafts() });
  async function discardDraft(d: IntakeDraft) {
    try {
      await intakeApi.deleteDraft(d.id);
      qc.invalidateQueries({ queryKey: ["intake-drafts"] });
      notify("Draft deleted", "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Couldn't delete the draft", "error"); }
  }
  // The request forms own the full page width — they carry their
  // own two-column layout, so they must not sit inside the 940px `.nr` shell.
  if (mode === "agreement" && agreementDef) {
    return (
      <div className="pt-3">
        <AgreementWizard
          key={draft?.id ?? agreementDef.key}
          def={agreementDef}
          draft={draft}
          onFiled={onFiled}
          onBack={() => { setAgreementDef(null); setDraft(null); setMode("catalog"); }}
        />
      </div>
    );
  }

  if (mode === "legal_question") {
    return (
      <div className="pt-3">
        <LegalQuestionForm onBack={() => setMode("catalog")} onFiled={onFiled} />
      </div>
    );
  }

  const q = typeSearch.trim().toLowerCase();
  const shown = AGREEMENT_FORMS.filter((f) => !q || `${f.name} ${f.desc}`.toLowerCase().includes(q));
  const showTm = !q || "trademark file a new trademark".includes(q);
  const recent = (recentReqs ?? []).slice(0, 3);

  return (
    <div className="nrq">
      <style dangerouslySetInnerHTML={{ __html: NRQ_CSS }} />
      <header className="nrq-hd">
        <h1>Create a new legal request</h1>
        <p>Choose the type of request and provide the key details. Aegis will route it through the right workflow.</p>
      </header>
      <ol className="nrq-steps" aria-label="Steps">
        {[["Request type", "Choose the request"], ["Details", "Answer a few questions"], ["Files", "Add supporting documents"], ["Review", "Check and submit"]].map(([t, d], i) => (
          <li key={t} data-on={i === 0 ? "1" : undefined}><b>{i + 1}</b><span><em>{t}</em><small>{d}</small></span></li>
        ))}
      </ol>

      <div className="nrq-grid">
        <div className="nrq-main">
          {(drafts ?? []).length > 0 && (
            <section className="nrq-card">
              <div className="nrq-ch"><h2>Your drafts</h2><span>{drafts!.length} saved</span></div>
              <ul className="nrq-drafts">
                {drafts!.map((d) => {
                  const form = AGREEMENT_FORMS.find((f) => f.key === d.form_key);
                  return (
                    <li key={d.id}>
                      <PenLine className="h-4 w-4" aria-hidden />
                      <span className="nrq-dt"><b>{d.title || form?.name || d.form_key}</b>
                        <small>{form?.name ?? d.form_key} · step {d.page_index + 1} of {form ? form.steps.length + 1 : "?"} · saved {new Date(d.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</small>
                      </span>
                      <Button size="sm" disabled={!form} onClick={() => { if (form) { setDraft(d); setAgreementDef(form); setMode("agreement"); } }}>Continue</Button>
                      <Button size="sm" variant="ghost" onClick={() => discardDraft(d)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
                    </li>
                  );
                })}
              </ul>
            </section>
          )}

          <section className="nrq-card">
            <div className="nrq-ch">
              <div><h2>What do you need from Legal?</h2><p>Pick the closest match. Each option is a short form that only asks what that request needs.</p></div>
              <label className="nrq-search"><Search className="h-4 w-4" aria-hidden /><input aria-label="Search request types" placeholder="Search request types…" value={typeSearch} onChange={(e) => setTypeSearch(e.target.value)} /></label>
            </div>
            <div className="nrq-types">
              {shown.map((f) => {
                const [I, tone] = FORM_LOOK[f.key] ?? [FileText, "ind"];
                return (
                  <button key={f.key} type="button" data-tone={tone} onClick={() => { setDraft(null); setAgreementDef(f); setMode("agreement"); }}>
                    <span className="ti"><I aria-hidden /></span>
                    <span className="tx"><b>{f.name}</b><small>{f.desc}</small></span>
                    <ChevronRight className="go" aria-hidden />
                  </button>
                );
              })}
              {showTm && (
                <Link href="/trademarks/intake" data-tone="sky">
                  <span className="ti"><Stamp aria-hidden /></span>
                  <span className="tx"><b>Trademark</b><small>File a new trademark — opens the Trademark Suite&apos;s own intake wizard, with similarity search.</small></span>
                  <ChevronRight className="go" aria-hidden />
                </Link>
              )}
              {shown.length === 0 && !showTm ? <p className="nrq-none">No request type matches “{typeSearch}”.</p> : null}
            </div>
          </section>

          {recent.length > 0 && (
            <section className="nrq-card">
              <div className="nrq-ch"><h2>Recent requests</h2></div>
              <div className="nrq-recent">
                {recent.map((r) => (
                  <button key={r.id} type="button" onClick={() => onFiled(r.id)}>
                    <span className="ti"><FileText aria-hidden /></span>
                    <span className="tx"><small>{r.ref}</small><b>{r.subject || r.type_label}</b><small>{counterpartyOf(r) ?? r.requester_name ?? "—"}</small></span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>

        <aside className="nrq-side">
          <section className="nrq-card nrq-ask">
            <span className="ai"><Bot aria-hidden /></span>
            <h3>Not sure which type to choose?</h3>
            <p>Describe what you need and ask Aegis.</p>
            <textarea aria-label="Describe what you need" rows={3} value={ask} onChange={(e) => setAsk(e.target.value)} placeholder="e.g. I need a contract to engage a vendor for cloud services…" />
            <button type="button" className="nrq-cta" onClick={() => router.push("/")}>Ask Aegis</button>
          </section>
          <section className="nrq-card">
            <div className="nrq-ch"><h2>Other ways in</h2></div>
            <ul className="nrq-ways">
              <li><Mail aria-hidden /><span>Email requests arrive on their own — your inbox is connected.</span></li>
              <li><MessageSquare aria-hidden /><button type="button" onClick={() => setMode("legal_question")}>Ask Legal a general question</button></li>
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

// Each form's icon and tint, so the catalog scans by colour as well as by name.
const FORM_LOOK: Record<string, [LucideIcon, string]> = {
  new_agreement: [FileText, "ind"], sow: [ClipboardList, "ind"], dpa: [Lock, "vio"],
  amendment: [FilePen, "amb"], renewal: [RefreshCw, "emr"], termination: [XCircle, "red"],
  novation: [ArrowLeftRight, "vio"], regularize: [FileCheck, "sky"], cancellation: [Ban, "red"],
};

const NRQ_CSS = `
.nrq{--r:14px;max-width:1240px;margin:0 auto;padding-bottom:40px}
.nrq-hd h1{margin:6px 0 4px;font:700 24px/1.2 var(--sans);letter-spacing:-.02em;color:var(--ink)}
.nrq-hd p{margin:0;font-size:13.5px;color:var(--ink-3)}
.nrq-steps{list-style:none;margin:16px 0;padding:12px 16px;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;border-radius:var(--r);background:var(--surface);box-shadow:0 0 0 1px var(--border)}
.nrq-steps li{display:flex;align-items:center;gap:10px;position:relative}
.nrq-steps li+li::before{content:"";position:absolute;left:-8px;top:50%;width:6px;border-top:2px solid var(--border)}
.nrq-steps b{flex:none;display:grid;place-items:center;width:28px;height:28px;border-radius:50%;font:700 12px var(--sans);background:var(--surface-2);color:var(--ink-3);box-shadow:inset 0 0 0 1.5px var(--border-strong)}
.nrq-steps li[data-on] b{background:var(--accent);color:var(--accent-ink);box-shadow:none}
.nrq-steps em{display:block;font:650 13px var(--sans);font-style:normal;color:var(--ink)}
.nrq-steps small{display:block;font-size:11.5px;color:var(--ink-3)}
.nrq-steps li:not([data-on]) em{color:var(--ink-2);font-weight:600}
.nrq-grid{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start}
.nrq-main,.nrq-side{display:flex;flex-direction:column;gap:16px;min-width:0}
.nrq-card{padding:16px;border-radius:var(--r);background:var(--surface);box-shadow:0 0 0 1px var(--border)}
.nrq-ch{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:12px}
.nrq-ch h2{margin:0;font:700 15px var(--sans);color:var(--ink)}
.nrq-ch p{margin:3px 0 0;font-size:12.5px;color:var(--ink-3)}
.nrq-ch>span{font-size:12px;color:var(--ink-3)}
.nrq-search{display:flex;align-items:center;gap:8px;min-width:210px;padding:0 10px;height:34px;border-radius:10px;box-shadow:0 0 0 1px var(--border-strong);color:var(--ink-3)}
.nrq-search input{flex:1;min-width:0;border:0;outline:0;background:none;font:13px var(--sans);color:var(--ink)}
.nrq-search:focus-within{box-shadow:0 0 0 2px var(--accent)}
.nrq-types{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.nrq-types>button,.nrq-types>a{--t:var(--c-ind);display:flex;align-items:center;gap:12px;padding:14px;border-radius:12px;text-align:left;background:var(--surface);box-shadow:0 0 0 1px var(--border);transition:transform .18s,box-shadow .18s}
.nrq-types>button:hover,.nrq-types>a:hover{transform:translateY(-2px);box-shadow:0 0 0 1px var(--border-strong),0 10px 20px -12px rgba(20,26,40,.25)}
.nrq-types [data-tone=vio]{--t:var(--c-vio)}.nrq-types [data-tone=amb]{--t:var(--c-amb)}.nrq-types [data-tone=emr]{--t:var(--c-emr)}.nrq-types [data-tone=red]{--t:var(--c-red)}.nrq-types [data-tone=sky]{--t:var(--c-sky)}
.nrq .ti{flex:none;display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:color-mix(in srgb,var(--t,var(--c-ind)) 14%,transparent);color:var(--t,var(--c-ind))}
.nrq .ti svg{width:20px;height:20px}
.nrq .tx{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px}
.nrq .tx b{font:650 13.5px var(--sans);color:var(--ink)}
.nrq .tx small{font-size:12px;line-height:1.4;color:var(--ink-3);overflow-wrap:anywhere}
.nrq .go{flex:none;width:16px;height:16px;color:var(--ink-3);transition:transform .18s,color .18s}
.nrq-types>button:hover .go,.nrq-types>a:hover .go{transform:translateX(3px);color:var(--t)}
.nrq-none{grid-column:1/-1;margin:6px 0;font-size:13px;color:var(--ink-3)}
.nrq-recent{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.nrq-recent button{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:12px;text-align:left;box-shadow:0 0 0 1px var(--border);transition:background .15s}
.nrq-recent button:hover{background:var(--surface-2)}
.nrq-recent .ti{width:34px;height:34px;border-radius:10px}.nrq-recent .tx b{font-size:13px}
.nrq-drafts{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.nrq-drafts li{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:12px;background:var(--inset)}
.nrq-dt{flex:1;min-width:0;display:flex;flex-direction:column}.nrq-dt b{font:650 13px var(--sans);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.nrq-dt small{font-size:12px;color:var(--ink-3)}
.nrq-ask{display:flex;flex-direction:column;gap:6px;background:color-mix(in srgb,var(--accent) 5%,var(--surface))}
.nrq-ask .ai{display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:var(--accent);color:var(--accent-ink)}.nrq-ask .ai svg{width:22px;height:22px}
.nrq-ask h3{margin:6px 0 0;font:700 15px var(--sans)}.nrq-ask p{margin:0;font-size:12.5px;color:var(--ink-3)}
.nrq-ask textarea{margin-top:6px;padding:10px 12px;border-radius:10px;border:0;outline:0;resize:vertical;font:13px var(--sans);background:var(--surface);color:var(--ink);box-shadow:0 0 0 1px var(--border-strong)}
.nrq-ask textarea:focus{box-shadow:0 0 0 2px var(--accent)}
.nrq-cta{margin-top:6px;height:38px;border-radius:10px;background:var(--accent);color:var(--accent-ink);font:650 13px var(--sans);transition:filter .15s}.nrq-cta:hover{filter:brightness(1.1)}
.nrq-ways{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px;font-size:12.5px;color:var(--ink-2)}
.nrq-ways li{display:flex;align-items:flex-start;gap:9px}.nrq-ways svg{flex:none;width:16px;height:16px;margin-top:1px;color:var(--ink-3)}
.nrq-ways button{font-weight:600;color:var(--accent);text-align:left}.nrq-ways button:hover{text-decoration:underline}
@media (max-width:1000px){.nrq-grid{grid-template-columns:1fr}.nrq-steps{grid-template-columns:repeat(2,minmax(0,1fr))}}
`;
