"use client";

// Signatures — the e-signature envelopes in the same layout as Approvals: counts on top, view pills,
// and one compact row per envelope, headed by the request it belongs to. A row opens that request.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2, CheckCircle2, ChevronDown, Clock, FileText, Inbox, PenLine, Plus, Search, Send, Trash2, XCircle, AlertTriangle, Mail } from "lucide-react";
import { contractsApi, intakeApi, signaturesApi } from "@/lib/endpoints";
import { Button, Field, Input, Modal, Select } from "@/components/ui";
import { contractDisplayName, fmtDateTime, fmtRelative, initials, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ContractResponse, SignatureRequest } from "@/lib/types";

/** One line of the list: an envelope that exists, or a contract that is ready to be sent. */
type Item = { kind: "env"; req: SignatureRequest } | { kind: "ready"; contract: ContractResponse };
import { MockupShell, pageList } from "../intake/_legal-intake";

const TERMINAL = new Set(["completed", "declined", "voided"]);
const STATUS_TONE: Record<string, string> = { completed: "ok", sent: "warn", delivered: "warn", draft: "neutral", declined: "crit", voided: "crit" };
type Tab = "ready" | "awaiting" | "done" | "declined" | "all";
// An envelope is in the view for its status; a contract still to be sent only shows under "To send" and "All".
const inTab = (r: SignatureRequest, t: Tab) =>
  t === "all" ? true : t === "ready" ? false : t === "awaiting" ? !TERMINAL.has(r.status) : t === "done" ? r.status === "completed" : r.status === "declined" || r.status === "voided";
const daysSince = (iso: string | null) => (iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000) : 0);

export default function SignaturesPage() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const [sendOpen, setSendOpen] = useState(false);
  const [presetId, setPresetId] = useState<string | undefined>();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [tabSel, setTabSel] = useState<Tab | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);

  const { data, isLoading, error } = useQuery({ queryKey: ["signatures"], queryFn: signaturesApi.list });
  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: contractsApi.list });
  // The request each contract came from; someone who cannot see requests gets rows headed by the contract.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), retry: false, staleTime: 60_000 });

  const contractById = useMemo(() => new Map((contracts ?? []).map((c) => [c.id, c])), [contracts]);
  const reqByContract = useMemo(() => {
    const m = new Map<string, NonNullable<typeof requests>[number]>();
    (requests ?? []).forEach((r) => { if (r.contract_id && !m.has(r.contract_id)) m.set(r.contract_id, r); });
    return m;
  }, [requests]);
  const contractName = (id: string) => {
    const c = contractById.get(id);
    return c ? contractDisplayName(c) : "Contract";
  };

  async function sync(req: SignatureRequest, completed: boolean) {
    setBusyId(req.id);
    try {
      await signaturesApi.sync(req.id, completed, !completed);
      notify(completed ? "Marked as completed" : "Marked as declined", "success");
      // Let the row fade before the list re-sorts under it.
      setLeaving(req.id);
      setTimeout(() => { setLeaving(null); qc.invalidateQueries({ queryKey: ["signatures"] }); }, 380);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Sync failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  const rows = useMemo(() => data ?? [], [data]);
  const awaiting = rows.filter((r) => inTab(r, "awaiting"));
  const done = rows.filter((r) => inTab(r, "done"));
  const declined = rows.filter((r) => inTab(r, "declined"));
  // Contracts that cleared approval and have no live envelope yet: still to be sent.
  const live = new Set(rows.filter((r) => !TERMINAL.has(r.status) || r.status === "completed").map((r) => r.contract_id));
  const ready = (contracts ?? []).filter((c) => c.lifecycle_stage === "signature" && !live.has(c.id));
  const tab: Tab = tabSel ?? (awaiting.length ? "awaiting" : ready.length ? "ready" : "all");
  const pickTab = (t: Tab) => { setTabSel(t); setPage(0); };
  const q = search.trim().toLowerCase();
  const shown = useMemo<Item[]>(() => {
    const envs = rows
      .filter((r) => inTab(r, tab) && (!q || `${contractName(r.contract_id)} ${reqByContract.get(r.contract_id)?.subject ?? ""} ${reqByContract.get(r.contract_id)?.ref ?? ""} ${(r.recipients ?? []).map((x) => x.name).join(" ")}`.toLowerCase().includes(q)))
      .map<Item>((req) => ({ kind: "env", req }));
    const toSend = (tab === "ready" || tab === "all" ? ready : [])
      .filter((c) => !q || `${contractDisplayName(c)} ${reqByContract.get(c.id)?.subject ?? ""} ${reqByContract.get(c.id)?.ref ?? ""}`.toLowerCase().includes(q))
      .map<Item>((contract) => ({ kind: "ready", contract }));
    // Still to send comes first; then the oldest waiting; finished ones newest first.
    const t = (r: SignatureRequest) => new Date(r.sent_at ?? r.created_at).getTime();
    envs.sort((a, b) => (a.kind === "env" && b.kind === "env" ? (tab === "awaiting" ? t(a.req) - t(b.req) : t(b.req) - t(a.req)) : 0));
    return [...toSend, ...envs];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, ready.length, tab, q, contractById, reqByContract]);
  const pageCount = Math.max(1, Math.ceil(shown.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visible = shown.slice(cur * pageSize, (cur + 1) * pageSize);

  const kcards: { t: Tab; n: number; l: string; I: typeof Inbox; c: string }[] = [
    { t: "ready", n: ready.length, l: "To send", I: Send, c: "ind" },
    { t: "awaiting", n: awaiting.length, l: "Awaiting signers", I: Clock, c: "amb" },
    { t: "done", n: done.length, l: "Completed", I: CheckCircle2, c: "emr" },
    { t: "declined", n: declined.length, l: "Declined / voided", I: AlertTriangle, c: "red" },
  ];
  const EMPTY: Record<Tab, { t: string; d: string }> = {
    ready: { t: "Nothing waiting to be sent", d: "Contracts that finish approval will appear here, ready to send." },
    awaiting: { t: "Nothing waiting on signers", d: "Every envelope has been signed, declined or voided." },
    done: { t: "No completed signatures yet", d: "Fully signed envelopes will appear here." },
    declined: { t: "Nothing declined", d: "Declined or voided envelopes will appear here." },
    all: { t: "No signature requests", d: "Send a contract for e-signature to track its envelope here." },
  };

  return (
    <MockupShell
      icon={<PenLine />}
      title="Signatures"
      subtitle="send contracts for e-signature and track each envelope"
      actions={<button className="btn pri" onClick={() => { setPresetId(undefined); setSendOpen(true); }}><Send className="ic" />Send for signature</button>}
    >
      <div className="sigl">
        <style dangerouslySetInnerHTML={{ __html: SIGL_CSS }} />
        <div className="kcards" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))", marginTop: 4 }}>
          {kcards.map((k) => (
            <button key={k.t} type="button" className={`kcard kc-${k.c}`} onClick={() => pickTab(k.t)} tabIndex={-1} aria-hidden="true">
              <span className="kic"><k.I className="ic" aria-hidden /></span>
              <span className="ktx"><span className="kl">{k.l}</span><b className="kn num"><CountUp n={k.n} /></b></span>
            </button>
          ))}
        </div>

        <div className="sgtoolbar">
          <div className="stabs" role="group" aria-label="Filter signatures" style={{ marginTop: 0 }}>
            {([["ready", "To send", ready.length, "ind"], ["awaiting", "Awaiting", awaiting.length, "amb"], ["done", "Completed", done.length, "emr"], ["declined", "Declined", declined.length, "red"], ["all", "All", rows.length + ready.length, "vio"]] as const).map(([t, l, n, c]) => (
              <button key={t} type="button" aria-pressed={tab === t} onClick={() => pickTab(t)} className={`stab${tab === t ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${c})` }}>
                <i aria-hidden /><span className="tl">{l}</span><span className="tn num">{n}</span>
              </button>
            ))}
          </div>
          <div className="search" style={{ marginLeft: "auto" }}>
            <Search className="ic" />
            <input aria-label="Search signatures" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search request, contract or signer…" />
          </div>
        </div>

        {isLoading ? (
          <div className="sglist">{[0, 1, 2, 3].map((i) => <div key={i} className="skel" />)}</div>
        ) : error ? (
          <div className="sgempty err">{error instanceof Error ? error.message : "Couldn't load signatures."}</div>
        ) : shown.length === 0 ? (
          <div className="sgempty aa-in">
            <div className="eic"><PenLine className="ic" /></div>
            <div className="et">{q ? "No signatures match your search" : EMPTY[tab].t}</div>
            <div className="ed">{q ? "Try a different word." : EMPTY[tab].d}</div>
            {!q && tab === "all" && <button className="btn pri" onClick={() => setSendOpen(true)}><Send className="ic" />Send for signature</button>}
          </div>
        ) : (
          <div key={`${tab}-${cur}`}>
            <ul className="sglist">
              {visible.map((item, i) => {
                if (item.kind === "ready") {
                  const c = item.contract;
                  const rq = reqByContract.get(c.id);
                  const href = rq ? `/intake?open=${rq.id}` : `/contracts/${c.id}`;
                  return (
                    <li key={`ready-${c.id}`} className="sgrow st-ready" style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
                      <div className="sggrid click" onClick={(e) => { if (!(e.target as HTMLElement).closest("a,button")) router.push(href); }}>
                        <span className={`sgic ${rq ? "r" : "c"}`}>{rq ? <Inbox className="ic" /> : <FileText className="ic" />}</span>
                        <div className="sgnm">
                          <div className="sgtitle">
                            <Link href={href} className="lnk">{rq ? rq.subject || rq.type_label : contractDisplayName(c)}</Link>
                            {rq && <b className="sgref">{rq.ref}</b>}
                          </div>
                          <div className="sgmeta">
                            <Link href={`/contracts/${c.id}`} className="sgchip ct" title="Open the contract"><FileText className="ic" /><span className="trunc">{contractDisplayName(c)}</span></Link>
                            {c.counterparty_name && <span className="sgchip"><Building2 className="ic" /><span className="trunc">{c.counterparty_name}</span></span>}
                          </div>
                        </div>
                        <div className="sgcol"><span className="pill accent live"><i />To send</span><span className="sgsm">Approved · ready to send</span></div>
                        <div className="sgcol"><span className="dim">—</span><span className="sgsm">No signers yet</span></div>
                        <div className="sgact">
                          <button className="abtn pri" onClick={() => { setPresetId(c.id); setSendOpen(true); }}><Send className="ic" />Send for signature</button>
                        </div>
                      </div>
                    </li>
                  );
                }
                const req = item.req;
                const rq = reqByContract.get(req.contract_id);
                const ct = contractById.get(req.contract_id);
                const href = rq ? `/intake?open=${rq.id}` : `/contracts/${req.contract_id}`;
                const heading = rq ? rq.subject || rq.type_label : contractName(req.contract_id);
                const terminal = TERMINAL.has(req.status);
                const waitDays = daysSince(req.sent_at);
                const stale = !terminal && waitDays >= 5;
                const state = req.status === "completed" ? "ok" : req.status === "declined" || req.status === "voided" ? "bad" : stale ? "late" : "wait";
                const signers = req.recipients ?? [];
                const busy = busyId === req.id;
                return (
                  <li key={req.id} className={`sgrow st-${state}${leaving === req.id ? " leaving" : ""}`} style={{ animationDelay: `${Math.min(i, 8) * 50}ms` }}>
                    <div className="sggrid click" onClick={(e) => { if (!(e.target as HTMLElement).closest("a,button")) router.push(href); }}>
                      <span className={`sgic ${rq ? "r" : "c"}`}>{rq ? <Inbox className="ic" /> : <FileText className="ic" />}</span>
                      <div className="sgnm">
                        <div className="sgtitle">
                          <Link href={href} className="lnk">{heading}</Link>
                          {rq && <b className="sgref">{rq.ref}</b>}
                        </div>
                        <div className="sgmeta">
                          <Link href={`/contracts/${req.contract_id}`} className="sgchip ct" title="Open the contract"><FileText className="ic" /><span className="trunc">{contractName(req.contract_id)}</span></Link>
                          {ct?.counterparty_name && <span className="sgchip"><Building2 className="ic" /><span className="trunc">{ct.counterparty_name}</span></span>}
                          <span className="sgchip"><Mail className="ic" />{titleCase(req.provider)}</span>
                        </div>
                      </div>
                      <div className="sgcol">
                        <span className={`pill ${STATUS_TONE[req.status] ?? "neutral"}${!terminal ? " live" : ""}`}>{!terminal && <i />}{titleCase(req.status)}</span>
                        <span className={`sgsm${stale ? " warnt" : ""}`}>{req.completed_at ? `Completed ${fmtDateTime(req.completed_at)}` : terminal ? `${titleCase(req.status)}${req.sent_at ? ` · sent ${fmtRelative(req.sent_at)}` : ""}` : stale ? `Waiting ${waitDays}d · sent ${fmtRelative(req.sent_at)}` : req.sent_at ? `Sent ${fmtRelative(req.sent_at)}` : "Draft"}</span>
                      </div>
                      <div className="sgcol">
                        {signers.length ? (
                          <div className="sgsigners">
                            {signers.slice(0, 4).map((r, k) => (
                              <span key={k} className={`sgav ${r.status === "completed" || r.status === "signed" ? "ok" : r.status === "declined" ? "bad" : ""}`} title={`${r.name}${r.role ? ` (${r.role})` : ""}${r.status ? ` — ${titleCase(r.status)}` : ""}`}>{initials(r.name)}</span>
                            ))}
                            {signers.length > 4 && <span className="sgav more">+{signers.length - 4}</span>}
                          </div>
                        ) : <span className="dim">—</span>}
                        <span className="sgsm">{signers.length ? `${signers.length} ${signers.length === 1 ? "signer" : "signers"}` : "No signers listed"}</span>
                      </div>
                      <div className="sgact">
                        {terminal ? (
                          <span className={`sgdone ${req.status}`}>{req.status === "completed" ? <CheckCircle2 className="ic" /> : <XCircle className="ic" />}{titleCase(req.status)}</span>
                        ) : (
                          <>
                            <button className="abtn ok" disabled={busy} onClick={() => sync(req, true)} aria-label="Mark this signature request completed">{busy ? <span className="spin" /> : <CheckCircle2 className="ic" />}{busy ? "" : "Mark completed"}</button>
                            <button className="abtn no" disabled={busy} onClick={() => sync(req, false)} aria-label="Mark this signature request declined">Declined</button>
                          </>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="sgpager">
              <span className="num">{`${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, shown.length)} of ${shown.length}`}</span>
              <nav className="pager" aria-label="Pagination">
                <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
                {pageList(pageCount, cur).map((n, i) => n === -1
                  ? <span key={`e${i}`} className="pge">…</span>
                  : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
                <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
              </nav>
              <label className="rpp">Rows <select aria-label="Rows per page" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>{[5, 10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
            </div>
          </div>
        )}

        <SendModal
          open={sendOpen}
          onClose={() => setSendOpen(false)}
          presetId={presetId}
          // Only contracts that have cleared approval (reached the signature stage)
          // can be sent — filtering here stops the pick-then-409 dead end.
          contracts={(contracts ?? [])
            .filter((c) => c.lifecycle_stage === "signature")
            .map((c) => ({ id: c.id, title: contractDisplayName(c) }))}
          onSent={() => {
            qc.invalidateQueries({ queryKey: ["signatures"] });
            notify("Sent for signature", "success");
            setSendOpen(false);
          }}
        />
      </div>
    </MockupShell>
  );
}

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

interface RecipientRow {
  name: string;
  email: string;
  role: string;
}

function SendModal({
  open,
  onClose,
  contracts,
  presetId,
  onSent,
}: {
  open: boolean;
  onClose: () => void;
  contracts: { id: string; title: string }[];
  presetId?: string;
  onSent: () => void;
}) {
  const { notify } = useToast();
  const [contractId, setContractId] = useState("");
  const [overrideLifecycle, setOverrideLifecycle] = useState(false);
  const [recipients, setRecipients] = useState<RecipientRow[]>([
    { name: "", email: "", role: "" },
  ]);
  const [busy, setBusy] = useState(false);
  // "Send" on a ready contract opens the box with that contract already chosen.
  useEffect(() => { if (open && presetId) setContractId(presetId); }, [open, presetId]);

  function setRow(i: number, patch: Partial<RecipientRow>) {
    setRecipients((rows) =>
      rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)),
    );
  }
  function addRow() {
    setRecipients((rows) => [...rows, { name: "", email: "", role: "" }]);
  }
  function removeRow(i: number) {
    setRecipients((rows) =>
      rows.length > 1 ? rows.filter((_, idx) => idx !== i) : rows,
    );
  }

  const valid =
    !!contractId &&
    recipients.every((r) => r.name.trim() && r.email.trim());

  async function submit() {
    if (!valid) return;
    setBusy(true);
    try {
      await signaturesApi.send({
        contract_id: contractId,
        override_lifecycle: overrideLifecycle,
        recipients: recipients.map((r) => ({
          name: r.name.trim(),
          email: r.email.trim(),
          role: r.role.trim() || null,
        })),
      });
      setContractId("");
      setOverrideLifecycle(false);
      setRecipients([{ name: "", email: "", role: "" }]);
      onSent();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Send for signature"
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!valid}>
            Send
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract" hint="Only contracts that have completed approval appear here.">
          <Select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
          >
            <option value="">
              {contracts.length ? "Select a contract…" : "No contracts are ready for signature yet"}
            </option>
            {contracts.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <label className="block text-xs font-medium text-slate-700">
              Recipients
            </label>
            <Button size="sm" variant="outline" onClick={addRow}>
              <Plus className="h-3.5 w-3.5" />
              Add recipient
            </Button>
          </div>
          <div className="space-y-2">
            {recipients.map((r, i) => (
              <div key={i} className="flex items-end gap-2">
                <Field label={i === 0 ? "Name" : undefined} className="flex-1">
                  <Input
                    placeholder="Full name"
                    value={r.name}
                    onChange={(e) => setRow(i, { name: e.target.value })}
                  />
                </Field>
                <Field label={i === 0 ? "Email" : undefined} className="flex-1">
                  <Input
                    type="email"
                    placeholder="email@example.com"
                    value={r.email}
                    onChange={(e) => setRow(i, { email: e.target.value })}
                  />
                </Field>
                <Field label={i === 0 ? "Role" : undefined} className="w-36">
                  <Input
                    placeholder="signer"
                    value={r.role}
                    onChange={(e) => setRow(i, { role: e.target.value })}
                  />
                </Field>
                <Button
                  size="icon"
                  variant="ghost"
                  disabled={recipients.length === 1}
                  onClick={() => removeRow(i)}
                  aria-label="Remove recipient"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
          </div>
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            checked={overrideLifecycle}
            onChange={(e) => setOverrideLifecycle(e.target.checked)}
          />
          Override lifecycle stage requirement
        </label>
      </div>
    </Modal>
  );
}

const SIGL_CSS = `
.sigl{--sgcols:36px minmax(0,3fr) minmax(110px,.9fr) minmax(130px,1fr) minmax(0,auto)}
.sigl *{box-sizing:border-box}
.sigl .dim{color:var(--ink-3)}
.sigl .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}
.sigl .kcard{cursor:pointer;padding:14px;gap:12px}
.sigl .btn.sm{padding:6px 11px;font-size:12px}
.sigl .sgtoolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0}
@keyframes sgin{from{opacity:0;transform:translateY(14px) scale(.99)}to{opacity:1;transform:none}}
.sigl .aa-in{animation:sgin .5s cubic-bezier(.2,.8,.2,1) both}
.sigl .sgready{display:flex;align-items:center;gap:14px;margin:14px 0 0;padding:12px 16px;border-radius:16px;border:1px solid color-mix(in srgb,var(--accent) 30%,var(--border));background:var(--accent-soft)}
.sigl .rdic{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:var(--accent);color:var(--accent-ink);flex:none;animation:sgbob 2.6s ease-in-out infinite}
@keyframes sgbob{50%{transform:translateY(-3px) rotate(-6deg)}}
.sigl .rdtx{display:flex;flex-direction:column;min-width:0;flex:1;font-size:12px;color:var(--ink-2)}
.sigl .rdtx b{color:var(--ink);font-size:13px}
.sigl .trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}

.sigl .sglist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.sigl .sgrow{--st:var(--border-strong);position:relative;border-radius:16px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);opacity:0;max-height:200px;animation:sgin .55s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .22s cubic-bezier(.2,.8,.2,1),box-shadow .22s,max-height .45s,opacity .3s,background .3s}
.sigl .sgrow.st-wait{--st:var(--warn)}.sigl .sgrow.st-ok{--st:var(--good)}.sigl .sgrow.st-bad{--st:var(--crit)}.sigl .sgrow.st-late{--st:var(--warn)}
.sigl .sgrow:hover{transform:translateY(-2px);box-shadow:0 16px 30px -22px rgba(20,26,40,.45),0 0 0 1px var(--border-strong)}
.sigl .sgrow.leaving{max-height:0;opacity:0;transform:translateX(30px);background:var(--good-soft);overflow:hidden;margin-bottom:-8px}
/* waiting five days or more: a slow amber pulse so a stalled envelope stands out */
.sigl .sgrow.st-late{animation:sgin .55s cubic-bezier(.2,.8,.2,1) forwards,sgalert 2.6s 1.2s ease-in-out infinite}
@keyframes sgalert{0%,100%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--warn) 40%,var(--border)),0 0 0 0 color-mix(in srgb,var(--warn) 30%,transparent)}50%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--warn) 65%,var(--border)),0 0 0 6px color-mix(in srgb,var(--warn) 0%,transparent)}}
.sigl .sggrid{display:grid;grid-template-columns:var(--sgcols);gap:14px;align-items:center;padding:11px 16px 11px 18px;border-radius:16px}
.sigl .sggrid.click{cursor:pointer}
.sigl .sggrid.click:hover .lnk{color:var(--accent)}
.sigl .sgic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;flex:none;transition:transform .3s}
.sigl .sgrow:hover .sgic{transform:rotate(-6deg) scale(1.06)}
.sigl .sgic.c{background:var(--accent-soft);color:var(--accent)}
.sigl .sgic.r{background:var(--warn-soft);color:var(--warn)}
.sigl .sgnm{min-width:0;display:flex;flex-direction:column;gap:5px}
.sigl .sgtitle{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:670;color:var(--ink);min-width:0;letter-spacing:-.01em}
.sigl .sgtitle .lnk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.sigl .lnk{color:var(--ink);text-decoration:none}
.sigl .sgref{flex:none;font:700 10px var(--mono);color:var(--accent);background:var(--accent-soft);padding:1px 7px;border-radius:99px}
.sigl .sgmeta{display:flex;align-items:center;gap:5px;min-width:0;overflow:hidden}
.sigl .sgchip{display:inline-flex;align-items:center;gap:5px;flex:0 1 auto;min-width:0;max-width:210px;height:20px;padding:0 8px;border-radius:99px;font-size:11px;color:var(--ink-2);background:var(--surface-2);text-decoration:none;white-space:nowrap}
.sigl .sgchip .ic{width:11px;height:11px;color:var(--ink-3)}
.sigl a.sgchip:hover{background:var(--accent-soft);color:var(--accent)}
.sigl .sgcol{min-width:0;display:flex;flex-direction:column;gap:3px;align-items:flex-start}
.sigl .sgsm{font-size:11px;color:var(--ink-3)}
.sigl .sgsm.warnt{color:var(--warn);font-weight:650}
.sigl .sgdate .v{font-weight:620;font-size:12.5px;color:var(--ink);white-space:nowrap}
.sigl .sgsigners{display:flex}
.sigl .sgav{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;font:700 10px var(--sans);background:var(--surface-2);color:var(--ink-2);box-shadow:0 0 0 2px var(--surface);margin-left:-6px;transition:transform .2s}
.sigl .sgav:first-child{margin-left:0}
.sigl .sgav.ok{background:var(--good-soft);color:var(--good)}.sigl .sgav.bad{background:var(--crit-soft);color:var(--crit)}
.sigl .sgrow:hover .sgav{transform:translateY(-2px)}
.sigl .sgav.more{background:var(--surface-2);color:var(--ink-3)}
.sigl .sgact{display:flex;align-items:center;justify-content:flex-end;gap:6px;white-space:nowrap}
.sigl .abtn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 12px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:600 12px var(--sans);transition:transform .15s,background .15s,box-shadow .2s;cursor:pointer}
.sigl .abtn:hover:not(:disabled){transform:translateY(-1px)}
.sigl .abtn:active:not(:disabled){transform:scale(.96)}
.sigl .abtn:disabled{opacity:.6;cursor:progress}
.sigl .abtn.ok{min-width:134px;border-color:transparent;background:var(--good);color:#fff}
.sigl .abtn.ok:hover:not(:disabled){box-shadow:0 8px 18px -8px var(--good)}
.sigl .abtn.no{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
.sigl .abtn.no:hover:not(:disabled){background:var(--crit-soft)}
.sigl .spin{width:13px;height:13px;border-radius:50%;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;animation:sgspin .7s linear infinite}
@keyframes sgspin{to{transform:rotate(360deg)}}
.sigl .sgdone{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--ink-2)}
.sigl .sgdone.completed{color:var(--good)}.sigl .sgdone.declined,.sigl .sgdone.voided{color:var(--crit)}
.sigl .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:99px;font:600 10.5px var(--sans);white-space:nowrap}
.sigl .pill.ok{background:var(--good-soft);color:var(--good)}.sigl .pill.warn{background:var(--warn-soft);color:var(--warn)}.sigl .pill.crit{background:var(--crit-soft);color:var(--crit)}.sigl .pill.neutral{background:var(--surface-2);color:var(--ink-2)}
.sigl .pill.accent{background:var(--accent-soft);color:var(--accent)}
.sigl .abtn.pri{min-width:148px;border-color:transparent;background:var(--accent);color:var(--accent-ink)}
.sigl .abtn.pri:hover:not(:disabled){box-shadow:0 8px 18px -8px var(--accent)}
.sigl .sgpager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:0 4px;font-size:12.5px;color:var(--ink-2)}
.sigl .skel{height:56px;border-radius:16px;background:var(--surface-2);animation:sgpulse 1.4s ease-in-out infinite;margin-bottom:8px}
@keyframes sgpulse{50%{opacity:.55}}
.sigl .sgempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px}
.sigl .sgempty .eic{width:48px;height:48px;border-radius:14px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:10px}
.sigl .sgempty.err{color:var(--crit)}
.sigl .sgempty .et{font-weight:660;font-size:15px;color:var(--ink)}
.sigl .sgempty .ed{font-size:12.5px;color:var(--ink-2);max-width:380px;margin:3px 0 12px}
.sigl .pill i{width:6px;height:6px;border-radius:50%;background:currentColor;flex:none}
.sigl .pill.live i{animation:sgdot 1.6s ease-in-out infinite}
@keyframes sgdot{0%,100%{box-shadow:0 0 0 0 currentColor;opacity:1}60%{box-shadow:0 0 0 5px transparent;opacity:.6}}
/* a soft light sweeps across a row when it is hovered */
.sigl .sgrow{overflow:hidden}
.sigl .sgrow::after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 30%,color-mix(in srgb,var(--accent) 7%,transparent) 50%,transparent 70%);transform:translateX(-100%);pointer-events:none}
.sigl .sgrow:hover::after{transform:translateX(100%);transition:transform .8s ease}
.sigl .abtn svg{transition:transform .25s}
.sigl .abtn.pri:hover svg{transform:translate(3px,-3px) rotate(8deg)}
.sigl .abtn.ok:hover svg{transform:scale(1.2)}
@media (max-width:1400px){
  .sigl .sggrid{grid-template-columns:36px minmax(0,1fr);row-gap:8px}
  .sigl .sggrid>.sgcol,.sigl .sggrid>.sgact{grid-column:2}
  .sigl .sggrid>.sgcol{flex-direction:row;align-items:center;gap:10px}
  .sigl .sgact{justify-content:flex-start}
}
@media (prefers-reduced-motion:reduce){.sigl *{animation:none!important;transition:none!important}.sigl .sgrow{opacity:1!important}}
`;
