"use client";

import { Fragment, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { FileText, PenLine, Plus, Trash2, Mail } from "lucide-react";

import { Button } from "@/components/ui";
import { intakeApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";

import { can } from "@/lib/intake";
import type { IntakeDraft } from "@/lib/types";

import { LegalIntakeBoard, MockupShell } from "./_legal-intake";
import { RequestOverview } from "./_request-overview";

import { AGREEMENT_FORMS, AgreementWizard, type AgreementFormDef } from "./_agreement-forms";
import { LegalQuestionForm } from "./_legal-question-form";
import { useScreenAccess } from "@/lib/screen-access";

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
  const { notify } = useToast();
  const qc = useQueryClient();
  // FR-9: UI-layer control gating only — the API independently re-verifies
  // every ADD request (FR-10/FR-13); this is a usability aid, not the
  // security boundary.
  const { canAdd } = useScreenAccess("intake");
  const [gmailSyncing, setGmailSyncing] = useState(false);

  async function syncGmail() {
    setGmailSyncing(true);
    try {
      const res = await intakeApi.gmailSync();
      if (res.status === "disabled") {
        notify(res.note || "Gmail sync isn't configured yet", "error");
      } else if (res.status === "error") {
        notify(res.note || "Gmail sync failed", "error");
      } else {
        type Filed = { deduped?: boolean; thread_followup?: boolean; documents_added?: string[] };
        const filed = (res.filed ?? []) as Filed[];
        const newRequests = filed.filter((f) => !f.deduped).length;
        const docsAddedToThreads = filed
          .filter((f) => f.thread_followup)
          .reduce((n, f) => n + (f.documents_added?.length ?? 0), 0);
        const parts = [];
        if (newRequests > 0) parts.push(`${newRequests} new request(s)`);
        if (docsAddedToThreads > 0) parts.push(`${docsAddedToThreads} document(s) added to existing threads`);
        notify(parts.length > 0 ? `Synced: ${parts.join(", ")}` : "No new emails to sync", "success");
        qc.invalidateQueries({ queryKey: ["intake-list"] });
      }
    } catch (e) {
      notify(e instanceof Error ? e.message : "Gmail sync failed", "error");
    } finally {
      setGmailSyncing(false);
    }
  }

  const [section, setSection] = useState(isStaff ? "queue" : "new");
  const [detailId, setDetailId] = useState<string | null>(null);
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
  const openCount = (listData ?? []).filter((r) => r.status !== "closed" && r.status !== "approved").length;
  const atRiskCount = (listData ?? []).filter((r) => r.sla_status === "at_risk").length;
  const overdueCount = (listData ?? []).filter((r) => r.sla_status === "overdue").length;

  return (
    <MockupShell
      title="Legal Intake"
      subtitle="one front door — every request lands here"
      stats={isStaff ? (
        <span className="stat">
          <span><b>{openCount}</b> open</span>
          {atRiskCount > 0 ? <span className="warn"><b>{atRiskCount}</b> at risk</span> : null}
          {overdueCount > 0 ? <span className="crit"><b>{overdueCount}</b> overdue</span> : null}
        </span>
      ) : null}
      actions={
        <>
          {isStaff ? (
            <button className="iconbtn" title={gmailSyncing ? "Syncing…" : "Sync email"} disabled={gmailSyncing} onClick={syncGmail}>
              <Mail className="h-4 w-4" />
            </button>
          ) : null}
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
        <RequestOverview id={detailId} canManage={isStaff} onBack={() => setDetailId(null)} />
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

  return (
    <div className="mx-auto max-w-[1120px] pb-10">
      {/* One question, one set of answers. Everything that is not a request
          type is demoted to a single line beneath them. */}
      <header className="pb-6 pt-1">
        <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-slate-900">What do you need from Legal?</h1>
        <p className="mt-1.5 max-w-[70ch] text-[13.5px] text-slate-500">
          Pick the closest match. Each one is a short form that only asks what that request needs.
        </p>
      </header>

      {(drafts ?? []).length > 0 && (
        <section className="mb-7">
          <div className="mb-3 flex items-center gap-3">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">Your drafts</h2>
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-[11px] text-slate-400">{drafts!.length} saved</span>
          </div>
          <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-slate-50">
            {drafts!.map((d) => {
              const form = AGREEMENT_FORMS.find((f) => f.key === d.form_key);
              return (
                <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
                  <PenLine className="h-4 w-4 shrink-0 text-slate-400" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-semibold text-slate-900">{d.title || form?.name || d.form_key}</span>
                    <span className="block text-[12px] text-slate-500">
                      {form?.name ?? d.form_key} · step {d.page_index + 1} of {form ? form.steps.length + 1 : "?"} · saved {new Date(d.updated_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}
                    </span>
                  </span>
                  <Button size="sm" disabled={!form} onClick={() => { if (form) { setDraft(d); setAgreementDef(form); setMode("agreement"); } }}>Continue</Button>
                  <Button size="sm" variant="ghost" onClick={() => discardDraft(d)}><Trash2 className="h-3.5 w-3.5" />Delete</Button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      <div className="flex flex-col gap-7">
        {(["New paper", "Change an agreement", "Records"] as const).map((group) => (
          <section key={group}>
            <div className="mb-3 flex items-center gap-3">
              <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">{group}</h2>
              <span className="h-px flex-1 bg-slate-200" />
              <span className="text-[11px] text-slate-400">
                {AGREEMENT_FORMS.filter((f) => f.group === group).length} types
              </span>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {AGREEMENT_FORMS.filter((f) => f.group === group).map((f) => (
                <button
                  key={f.key}
                  onClick={() => { setDraft(null); setAgreementDef(f); setMode("agreement"); }}
                  className="group flex items-start gap-3 rounded-xl border border-slate-200 bg-slate-100 p-4 text-left shadow-card transition-all hover:-translate-y-px hover:border-brand-300 hover:shadow-pop focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35"
                >
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-brand-100 bg-brand-50 text-brand-700">
                    <FileText className="h-[18px] w-[18px]" />
                  </span>
                  <span className="min-w-0">
                    <span className="block text-[13.5px] font-semibold text-slate-900">{f.name}</span>
                    <span className="mt-1 block text-[12.2px] leading-snug text-slate-500">{f.desc}</span>
                  </span>
                </button>
              ))}
            </div>
          </section>
        ))}
        <section>
          <div className="mb-3 flex items-center gap-3">
            <h2 className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">Not an agreement</h2>
            <span className="h-px flex-1 bg-slate-200" />
            <span className="text-[11px] text-slate-400">Ask Aegis answers first and sends it to Legal if you need a lawyer</span>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {([["General legal question", "Not sure what you need? Ask Legal in plain words.", "legal_question"],
              ["Trademark", "Clear, file or defend a brand name or logo.", null]] as const).map(([name, desc, form]) => (
              // General legal question opens its own short form (question, jurisdiction,
              // urgency, contract) which then hands off to Ask Aegis or files with Legal.
              // Trademark still goes straight to Ask Aegis.
              <button key={name} onClick={() => (form ? setMode(form) : router.push("/"))}
                className="flex items-start gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50 p-4 text-left transition-colors hover:border-brand-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/35">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-slate-200 bg-slate-100 text-slate-500">
                  <Mail className="h-[18px] w-[18px]" />
                </span>
                <span className="min-w-0">
                  <span className="block text-[13.5px] font-semibold text-slate-900">{name}</span>
                  <span className="mt-1 block text-[12.2px] leading-snug text-slate-500">{desc}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      </div>

      {/* The other ways in, as one quiet strip rather than three more panels. */}
      <div className="mt-9 rounded-xl border border-slate-200 bg-slate-50 px-5 py-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-[12.5px] text-slate-600">
          <span className="inline-flex items-center gap-2">
            <Mail className="h-4 w-4 text-slate-400" />
            Email requests arrive on their own — your inbox is connected.
          </span>
          <span className="h-4 w-px bg-slate-200" />
          <button onClick={() => router.push("/")} className="font-medium text-brand-700 underline-offset-2 hover:underline">
            Not sure what you need? Ask Aegis
          </button>

        </div>

      </div>
    </div>
  );
}
