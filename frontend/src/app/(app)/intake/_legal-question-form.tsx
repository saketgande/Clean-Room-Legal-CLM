"use client";

// "General legal question" — the short form behind the intake card.
//
// It asks the one thing the card promises ("what's your question?") plus the
// facts a lawyer needs to start: where, how urgent, which contract. Then the
// requester picks a route:
//   * Get an answer from Aegis — opens a NEW Ask Aegis chat with this question
//     sent as its first message (session type "legal_question"). Aegis answers
//     and offers to send it to Legal; nothing is filed unless they say yes.
//   * Send straight to Legal — files a "Legal Question — General" request now,
//     with no AI step (also the route when the assistant is unavailable).

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, MessageSquare, Send, Sparkles } from "lucide-react";
import { Field, Select, Textarea, Input } from "@/components/ui";
import { useToast } from "@/components/toast";
import { contractsApi, intakeApi } from "@/lib/endpoints";
import {
  composeRequestDescription, deriveSubject, LEGAL_QUESTION_TYPE, QUESTION_MAX_CHARS,
  stashHandoff, URGENCY_OPTIONS, validateLegalQuestion,
  type LegalQuestionErrors, type LegalQuestionInput, type LegalQuestionPriority,
} from "@/lib/legal-question";

export function LegalQuestionForm({ onBack, onFiled }: { onBack: () => void; onFiled: (id: string) => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();
  const [question, setQuestion] = useState("");
  const [jurisdiction, setJurisdiction] = useState("");
  const [priority, setPriority] = useState<LegalQuestionPriority>("Medium");
  const [contractId, setContractId] = useState("");
  const [errors, setErrors] = useState<LegalQuestionErrors>({});
  const [busy, setBusy] = useState<"ai" | "legal" | null>(null);
  // `busy` drives the UI; this ref is the actual lock. State updates are not
  // visible until the next render, so two clicks in the same tick both saw
  // busy === null and filed the question twice.
  const lock = useRef(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => contractsApi.list() });
  const picked = (contracts.data ?? []).find((c) => c.id === contractId) ?? null;

  function input(): LegalQuestionInput {
    return {
      question, jurisdiction, priority,
      contract: picked ? { id: picked.id, title: picked.title, counterparty: picked.counterparty_name ?? null } : null,
    };
  }

  /** Validate; on failure show the errors and focus the first bad field. */
  function checked(): LegalQuestionInput | null {
    const value = input();
    const found = validateLegalQuestion(value);
    setErrors(found);
    const first = (["question", "jurisdiction"] as const).find((k) => found[k]);
    if (first) {
      document.getElementById(`lq-${first}`)?.focus();
      return null;
    }
    return value;
  }

  function askAegis() {
    if (lock.current) return;
    const value = checked();
    if (!value) return;
    lock.current = true;
    setBusy("ai");
    try {
      const key = stashHandoff(value);
      router.push(`/assistant?legal_question=${encodeURIComponent(key)}`);
    } catch {
      // sessionStorage can be unavailable (private mode / blocked storage).
      lock.current = false;
      setBusy(null);
      notify("Couldn't open Ask Aegis from here — send it straight to Legal instead.", "error");
    }
  }

  async function sendToLegal() {
    if (lock.current) return;
    const value = checked();
    if (!value) return;
    lock.current = true;
    setBusy("legal");
    setFileError(null);
    try {
      const r = await intakeApi.create({
        type_label: LEGAL_QUESTION_TYPE,
        subject: deriveSubject(value.question),
        description: composeRequestDescription(value),
        priority: value.priority,
        source: "form",
      });
      ["intake-mine", "intake-list", "intake-mywork"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      notify(`Sent to Legal — ${r.ref}`, "success");
      onFiled(r.id);
    } catch (e) {
      setFileError(e instanceof Error ? e.message : "Couldn't send it to Legal. Try again.");
    } finally {
      lock.current = false;
      setBusy(null);
    }
  }

  const err = (k: keyof LegalQuestionErrors) => (errors[k] ? "border-danger focus:border-danger" : "");

  return (
    <div className="mx-auto max-w-[760px] pb-10">
      <button type="button" onClick={onBack} disabled={!!busy}
        className="mb-4 inline-flex items-center gap-1.5 text-[12.5px] font-medium text-brand-700 hover:underline disabled:opacity-50">
        <ArrowLeft className="h-3.5 w-3.5" />All request types
      </button>

      <header className="pb-5">
        <div className="text-[11px] font-semibold uppercase tracking-[0.12em] text-slate-500">General legal question</div>
        <h1 className="mt-1 text-[22px] font-semibold tracking-[-0.02em] text-slate-900">What&apos;s your legal question?</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          Ask it in plain words. Aegis can answer straight away and send it to a lawyer if you need one — or send it to Legal directly.
        </p>
      </header>

      <form className="flex flex-col gap-5 rounded-xl border border-slate-200 bg-slate-50 p-5"
        onSubmit={(e) => { e.preventDefault(); askAegis(); }} noValidate>
        <Field label="Your question *">
          <Textarea id="lq-question" rows={6} maxLength={QUESTION_MAX_CHARS} value={question} autoFocus
            aria-invalid={!!errors.question}
            placeholder="e.g. A supplier missed two deliveries this month. Can we terminate early, and do we have to give notice first?"
            className={err("question")}
            onChange={(e) => { setQuestion(e.target.value); if (errors.question) setErrors((p) => ({ ...p, question: undefined })); }} />
        </Field>
        {errors.question && <p role="alert" className="-mt-3.5 text-xs text-danger">{errors.question}</p>}

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <Field label="Country / jurisdiction" hint="Where the people or the deal are. If you leave it blank, Aegis assumes India.">
            <Input id="lq-jurisdiction" value={jurisdiction} maxLength={120} placeholder="e.g. India — Telangana"
              aria-invalid={!!errors.jurisdiction} className={`h-10 ${err("jurisdiction")}`}
              onChange={(e) => setJurisdiction(e.target.value)} />
          </Field>
          <Field label="How urgent is it?">
            <Select id="lq-priority" className="h-10" value={priority}
              onChange={(e) => setPriority(e.target.value as LegalQuestionPriority)}>
              {URGENCY_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </Field>
        </div>
        {errors.jurisdiction && <p role="alert" className="-mt-3.5 text-xs text-danger">{errors.jurisdiction}</p>}

        <Field label="Related contract (optional)"
          hint={contracts.isError ? "Couldn't load contracts — you can still ask without one." : "Aegis will read it when it answers."}>
          <Select id="lq-contract" className="h-10" value={contractId} disabled={contracts.isLoading || contracts.isError}
            onChange={(e) => setContractId(e.target.value)}>
            <option value="">{contracts.isLoading ? "Loading contracts…" : "None"}</option>
            {(contracts.data ?? []).filter((c) => !c.archived).map((c) => (
              <option key={c.id} value={c.id}>{[c.title, c.counterparty_name].filter(Boolean).join(" · ")}</option>
            ))}
          </Select>
        </Field>

        {fileError && (
          <p role="alert" className="rounded-lg border border-danger/40 bg-danger-subtle px-3 py-2 text-[12.5px] text-danger">{fileError}</p>
        )}

        <div className="flex flex-wrap items-center gap-3 border-t border-slate-200 pt-4">
          {/* Shell button classes, not <Button>: the intake shell resets
              `.li-board button`, which overrides Tailwind's fill/border utilities. */}
          <button type="submit" className="primary disabled:opacity-60" disabled={!!busy}>
            {busy === "ai" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
            Get an answer from Aegis
          </button>
          <button type="button" className="ddbtn disabled:opacity-60" disabled={!!busy} onClick={sendToLegal}>
            {busy === "legal" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            {busy === "legal" ? "Sending…" : "Send straight to Legal"}
          </button>
          <span className="inline-flex items-center gap-1.5 text-[12px] text-slate-500">
            <MessageSquare className="h-3.5 w-3.5" />Aegis only files a request with Legal if you say yes.
          </span>
        </div>
      </form>
    </div>
  );
}
