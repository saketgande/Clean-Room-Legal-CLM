"use client";

// Approvals — the sign-offs in flight, as a decision queue in the Legal Intake layout: counts on top,
// view pills, and one card per approval with who it is waiting on, how far it has got and what you
// can do about it. Who approves is set on each workflow's Approval step (Admin → Workflows).

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronDown, Clock, FileText, Inbox, MoreHorizontal, Search, ShieldCheck, UserCheck, X, XCircle, AlertTriangle, Building2, Tag, Wallet } from "lucide-react";
import { approvalsApi, contractsApi, intakeApi } from "@/lib/endpoints";
import { contractDisplayName, fmtMoney, fmtRelative, riskBand, statusTone, titleCase } from "@/lib/utils";
import { dueText } from "@/lib/approval-due";
import { useToast } from "@/components/toast";
import { useConfirm } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { HttpError } from "@/lib/api";
import { RISK_ACK_REQUIRED_MESSAGE, type ApprovalRequest, type IntakeRequest } from "@/lib/types";
import { MockupShell, pageList } from "../intake/_legal-intake";

// tone -> local pill class
function toneCls(tone: string): string {
  switch (tone) {
    case "green":
      return "ok";
    case "amber":
      return "warn";
    case "red":
      return "crit";
    case "blue":
    case "violet":
    case "cyan":
      return "accent";
    default:
      return "neutral";
  }
}

type Tab = "mine" | "pending" | "overdue" | "done";

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

export default function ApprovalsPage() {
  return (
    <MockupShell icon={<ShieldCheck />} title="Approvals" subtitle="every sign-off in flight, and where each one is waiting">
      <div className="apprv">
        <style dangerouslySetInnerHTML={{ __html: APPRV_CSS }} />
        <Board />
      </div>
    </MockupShell>
  );
}

// ---- The queue -----------------------------------------------------------
function Board() {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  const [rejectFor, setRejectFor] = useState<ApprovalRequest | null>(null);
  const [reassignFor, setReassignFor] = useState<
    { req: ApprovalRequest; kind: "delegate" | "escalate" } | null
  >(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [tabSel, setTabSel] = useState<Tab | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const menuRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: approvalsApi.list,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  // The requests the contracts came from, to say which request an approval belongs to.
  // A person who cannot see requests just gets cards without that line.
  const { data: requests } = useQuery({ queryKey: ["intake-requests-all"], queryFn: () => intakeApi.list(), retry: false, staleTime: 60_000 });
  const contractById = useMemo(() => new Map((contracts ?? []).map((c) => [c.id, c])), [contracts]);
  const reqById = useMemo(() => new Map((requests ?? []).map((r) => [r.id, r])), [requests]);
  const reqByContract = useMemo(() => {
    const m = new Map<string, (typeof requests extends (infer T)[] | undefined ? T : never)>();
    (requests ?? []).forEach((r) => { if (r.contract_id && !m.has(r.contract_id)) m.set(r.contract_id, r); });
    return m;
  }, [requests]);

  useEffect(() => {
    if (!menuFor) return;
    const h = (e: MouseEvent) => { if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuFor(null); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [menuFor]);

  const titleMap = useMemo(() => {
    const m = new Map<string, string>();
    // Name the agreement, not the upload it arrived as — the same rule the
    // contract register uses, so a row reads identically in both places.
    for (const c of contracts ?? []) m.set(c.id, contractDisplayName(c));
    return m;
  }, [contracts]);

  /** What a row is about, in words: the agreement, or the request when it has no contract. */
  function subjectName(req: ApprovalRequest): string {
    if (req.contract_id) {
      return req.contract_title
        ? contractDisplayName({ title: req.contract_title, contract_type: req.contract_type })
        : titleMap.get(req.contract_id) ?? "this contract";
    }
    if (req.intake_request_id) return [req.intake_ref, req.intake_title].filter(Boolean).join(" · ") || "this request";
    return "this approval";
  }
  const userRoles = useMemo(
    () => new Set([...(user?.roles ?? []), user?.active_role_name ?? ""].filter(Boolean)),
    [user],
  );

  function canDecide(req: ApprovalRequest) {
    // Prefer the server's verdict (it knows approver-group membership).
    if (req.can_decide !== undefined) return req.can_decide;
    if (!user || req.requested_by_user_id === user.id) return false;
    if (userRoles.has("admin")) return true;
    if (req.approver_user_id && req.approver_user_id === user.id) return true;
    return !!req.approver_role && userRoles.has(req.approver_role);
  }

  /** Who the step is with, in words. */
  function approverName(req: ApprovalRequest): string {
    return req.approver_role
      ? titleCase(req.approver_role)
      : req.approver_team_name ??
          (req.approver_team_id ? "Team" : req.approver_user_id ? req.approver_user_name ?? "Assigned approver" : "—");
  }

  const { confirm, dialog: confirmDialog } = useConfirm();

  async function decide(
    req: ApprovalRequest,
    decision: "approve" | "reject",
    comment?: string,
  ) {
    setBusyId(req.id);
    try {
      try {
        await approvalsApi.decide(req.id, decision, comment);
      } catch (e) {
        // The server refuses an approve with open high risks until acknowledged (D4): ask once, resend once.
        if (!(e instanceof HttpError && e.status === 422 && e.message === RISK_ACK_REQUIRED_MESSAGE)) throw e;
        if (!(await confirm({ title: "Approve anyway?", message: `${e.message}. Approve anyway?`, confirmLabel: "Approve anyway" }))) throw e;
        await approvalsApi.decide(req.id, "approve", comment, true);
      }
      notify(`${decision === "approve" ? "Approved" : "Rejected"}: ${subjectName(req)}`, "success");
      setRejectFor(null);
      // Let the card slide away before the list refreshes underneath it.
      setLeaving(req.id);
      setTimeout(() => { setLeaving(null); qc.invalidateQueries({ queryKey: ["approvals"] }); }, 380);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function reassign(req: ApprovalRequest, toUserId: string, kind: "delegate" | "escalate") {
    setBusyId(req.id);
    try {
      await approvalsApi.reassign(req.id, toUserId, kind);
      qc.invalidateQueries({ queryKey: ["approvals"] });
      notify(`${kind === "delegate" ? "Delegated" : "Escalated"}: ${subjectName(req)}`, "success");
      setReassignFor(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Reassign failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  const all = useMemo(() => data ?? [], [data]);
  const isPending = (r: ApprovalRequest) => r.status === "pending";
  // Only rows still waiting count: "can decide" says who is eligible, and an
  // administrator is eligible for rows that were decided long ago.
  const mine = all.filter((r) => isPending(r) && r.can_decide);
  const pending = all.filter(isPending);
  const overdue = all.filter((r) => r.overdue);
  const done = all.filter((r) => !isPending(r));
  const tab: Tab = tabSel ?? (mine.length ? "mine" : "pending");
  const pickTab = (t: Tab) => { setTabSel(t); setPage(0); };

  const q = search.trim().toLowerCase();
  const shown = useMemo(() => {
    const base = tab === "mine" ? mine : tab === "pending" ? pending : tab === "overdue" ? overdue : done;
    const list = base.filter((r) => !q || `${subjectName(r)} ${approverName(r)} ${(r.contract_id ? reqByContract.get(r.contract_id)?.ref : "") ?? ""} ${contractById.get(r.contract_id ?? "")?.counterparty_name ?? ""}`.toLowerCase().includes(q));
    const due = (r: ApprovalRequest) => (r.due_at ? new Date(r.due_at).getTime() : Infinity);
    return [...list].sort((a, b) =>
      tab === "done"
        ? new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
        : Number(b.overdue ?? false) - Number(a.overdue ?? false) || due(a) - due(b),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, tab, q, titleMap, reqByContract, contractById]);
  // Approvals on the same request sit together, under the request's name.
  const groups = useMemo(() => {
    type G = { key: string; rq?: IntakeRequest; items: ApprovalRequest[] };
    const m = new Map<string, G>();
    for (const r of shown) {
      const rid = r.intake_request_id ?? (r.contract_id ? reqByContract.get(r.contract_id)?.id : undefined);
      const key = rid ? `r:${rid}` : `a:${r.id}`;
      const g = m.get(key) ?? { key, rq: rid ? reqById.get(rid) : undefined, items: [] };
      g.items.push(r);
      m.set(key, g);
    }
    const out = [...m.values()];
    out.forEach((g) => g.items.sort((a, b) => (a.step_order ?? 0) - (b.step_order ?? 0)));
    return out;
  }, [shown, reqByContract, reqById]);
  const pageCount = Math.max(1, Math.ceil(groups.length / pageSize));
  const cur = Math.min(page, pageCount - 1);
  const visible = groups.slice(cur * pageSize, (cur + 1) * pageSize);

  const kcards: { t: Tab; n: number; l: string; I: typeof Inbox; c: string }[] = [
    { t: "mine", n: mine.length, l: "Yours to action", I: UserCheck, c: "ind" },
    { t: "pending", n: pending.length, l: "Pending", I: Clock, c: "amb" },
    { t: "overdue", n: overdue.length, l: "Overdue", I: AlertTriangle, c: "red" },
    { t: "done", n: done.length, l: "Decided", I: CheckCircle2, c: "emr" },
  ];
  const EMPTY: Record<Tab, { t: string; d: string }> = {
    mine: { t: "You're all caught up", d: "Nothing is waiting on you right now." },
    pending: { t: "Nothing pending", d: "Approvals start when a contract's workflow reaches an Approval step." },
    overdue: { t: "Nothing overdue", d: "Every approval is inside its due date." },
    done: { t: "Nothing decided yet", d: "Approved and rejected sign-offs will appear here." },
  };

  return (
    <div>
      {confirmDialog}
      <div className="kcards" style={{ gridTemplateColumns: "repeat(4,minmax(0,1fr))", marginTop: 4 }}>
        {kcards.map((k) => (
          <button key={k.t} type="button" className={`kcard kc-${k.c}`} onClick={() => pickTab(k.t)} tabIndex={-1} aria-hidden="true">
            <span className="kic"><k.I className="ic" aria-hidden /></span>
            <span className="ktx"><span className="kl">{k.l}</span><b className="kn num"><CountUp n={k.n} /></b></span>
          </button>
        ))}
      </div>

      <div className="aptoolbar">
        <div className="stabs" role="group" aria-label="Filter approvals" style={{ marginTop: 0 }}>
          {([["mine", "Yours to action", mine.length, "ind"], ["pending", "Pending", pending.length, "amb"], ["overdue", "Overdue", overdue.length, "red"], ["done", "Decided", done.length, "emr"]] as const).map(([t, l, n, c]) => (
            <button key={t} type="button" aria-pressed={tab === t} onClick={() => pickTab(t)} className={`stab${tab === t ? " on" : ""}`} style={{ ["--c" as string]: `var(--c-${c})` }}>
              <i aria-hidden /><span className="tl">{l}</span><span className="tn num">{n}</span>
            </button>
          ))}
        </div>
        <div className="search" style={{ marginLeft: "auto" }}>
          <Search className="ic" />
          <input aria-label="Search approvals" value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search contract, request or approver…" />
        </div>
      </div>

      {isLoading ? (
        <div className="aplist">
          {[0, 1, 2, 3].map((i) => <div key={i} className="skelcard" />)}
        </div>
      ) : error ? (
        <div className="apempty err">{error instanceof Error ? error.message : "Couldn't load approvals."}</div>
      ) : shown.length === 0 ? (
        <div className="apempty aa-in">
          <div className="eic"><CheckCircle2 className="ic" /></div>
          <div className="et">{q ? "No approvals match your search" : EMPTY[tab].t}</div>
          <div className="ed">{q ? "Try a different word." : EMPTY[tab].d}</div>
        </div>
      ) : (
        <div className="apboard" key={`${tab}-${cur}`}>
          <ul className="aplist2">
            {visible.map((g, gi) => {
              const first = g.items[0];
              const rq = g.rq;
              const ct = first.contract_id ? contractById.get(first.contract_id) : undefined;
              const band = ct ? riskBand(ct.risk_summary, ct.current_authoritative_version_id) : null;
              const typeLabel = first.contract_type ? titleCase(first.contract_type) : rq?.type_label ?? null;
              const anyLate = g.items.some((r) => r.overdue);
              const allDone = g.items.every((r) => r.status !== "pending");
              const state = anyLate ? "late" : allDone ? "ok" : "wait";
              const openHref = rq ? `/intake?open=${rq.id}` : first.intake_request_id ? `/intake?open=${first.intake_request_id}` : first.contract_id ? `/contracts/${first.contract_id}` : null;
              // The heading is the request; an approval with no request falls back to what it is about.
              const heading = rq ? rq.subject || rq.type_label : subjectName(first);
              const waitingN = g.items.filter(isPending).length;
              const single = g.items.length === 1;

              // The cells for one approval: who it is with, status, due, and what you can do.
              const stepInfo = (req: ApprovalRequest) => {
                const stepNow = rq?.workflow?.find((w) => w.active)?.label;
                return (
                  <div className="apcol">
                    <div className="apstep trunc">Step {req.step_order ?? 1}{isPending(req) && stepNow ? <span className="dim"> · {stepNow.replace(/^.*? · /, "")}</span> : null}</div>
                    <div className="apsm trunc">With {approverName(req)}</div>
                  </div>
                );
              };
              const statusCell = (req: ApprovalRequest) => {
                const needed = req.needed ?? 1;
                const signed = req.approvals ?? 0;
                return (
                  <div className="apcol">
                    <div className="apstat">
                      <span className={`pill ${toneCls(statusTone(req.status))}`}>{titleCase(req.status)}</span>
                      {req.overdue && <span className="pill crit beat">Overdue</span>}
                    </div>
                    {needed > 1 && (
                      <div className="approg" aria-label={`${signed} of ${needed} signed`}>
                        {Array.from({ length: needed }, (_, k) => <span key={k} className={k < signed ? "on" : ""} />)}
                        <em>{signed}/{needed}</em>
                      </div>
                    )}
                  </div>
                );
              };
              const dueCell = (req: ApprovalRequest) => (
                <div className={`apcol apdue${req.overdue ? " late" : ""}`}>
                  <span className="v">{isPending(req) ? dueText(req) : fmtRelative(req.updated_at)}</span>
                  <span className="apsm trunc">{isPending(req) ? "Due" : "Decided"}</span>
                </div>
              );
              const actions = (req: ApprovalRequest) => {
                const waiting = isPending(req);
                const decidable = waiting && canDecide(req);
                const busy = busyId === req.id;
                return (
                  <div className="apact">
                    {decidable ? (
                      <>
                        <button className="abtn ok" disabled={busy} onClick={() => decide(req, "approve")}>{busy ? <span className="spin" /> : <CheckCircle2 className="ic" />}{busy ? "" : "Approve"}</button>
                        <button className="abtn no" disabled={busy} onClick={() => setRejectFor(req)}>Reject</button>
                        <div className="apmore" ref={menuFor === req.id ? menuRef : undefined}>
                          <button className="abtn ico" aria-label="More actions" aria-expanded={menuFor === req.id} disabled={busy} onClick={() => setMenuFor(menuFor === req.id ? null : req.id)}><MoreHorizontal className="ic" /></button>
                          {menuFor === req.id && (
                            <div className="apmenu aa-pop" role="menu">
                              <button role="menuitem" title="Hand this step to another approver" onClick={() => { setMenuFor(null); setReassignFor({ req, kind: "delegate" }); }}>Delegate</button>
                              <button role="menuitem" title="Escalate this step to a senior approver" onClick={() => { setMenuFor(null); setReassignFor({ req, kind: "escalate" }); }}>Escalate</button>
                            </div>
                          )}
                        </div>
                      </>
                    ) : waiting ? (
                      <span className="apwait"><Clock className="ic" />Waiting</span>
                    ) : (
                      <span className={`apdone ${req.status}`}>{req.status === "approved" ? <CheckCircle2 className="ic" /> : <XCircle className="ic" />}{titleCase(req.status)}</span>
                    )}
                  </div>
                );
              };

              return (
                <li key={g.key} className={`apgroup st-${state}${single && leaving === first.id ? " leaving" : ""}`} style={{ animationDelay: `${Math.min(gi, 8) * 50}ms` }}>
                  <div
                    className={`aphd${single ? " aprow" : ""}${openHref ? " click" : ""}${single && first.overdue ? " late" : ""}`}
                    onClick={(e) => { if (openHref && !(e.target as HTMLElement).closest("a,button")) router.push(openHref); }}
                  >
                    <span className={`apic ${rq ? "r" : "c"}`}>{rq ? <Inbox className="ic" /> : <FileText className="ic" />}</span>
                    <div className="apnm">
                      <div className="aptitle">
                        {openHref ? <Link href={openHref} className="lnk">{heading}</Link> : <span className="dim">Nothing linked to this approval</span>}
                        {rq && <b className="apref">{rq.ref}</b>}
                        {first.contract_archived && <span className="pill neutral">archived</span>}
                      </div>
                      <div className="apmeta">
                        {first.contract_id && (
                          <Link href={`/contracts/${first.contract_id}`} className="apchip ct" title="Open the contract">
                            <FileText className="ic" /><span className="trunc">{first.contract_title ? contractDisplayName({ title: first.contract_title, contract_type: first.contract_type }) : titleMap.get(first.contract_id) ?? "Contract"}</span>
                          </Link>
                        )}
                        {ct?.counterparty_name && <span className="apchip"><Building2 className="ic" /><span className="trunc">{ct.counterparty_name}</span></span>}
                        {(typeLabel || ct?.value_amount != null) && <span className="apchip"><Tag className="ic" /><span className="trunc">{[typeLabel, ct?.value_amount != null ? fmtMoney(ct.value_amount, ct.currency) : null].filter(Boolean).join(" · ")}</span></span>}
                        {band && band !== "unknown" && <span className={`aprisk risk-${band}`}><i />{titleCase(band)}</span>}
                      </div>
                    </div>
                    {single ? (
                      <>
                        {stepInfo(first)}
                        {statusCell(first)}
                        {dueCell(first)}
                        {actions(first)}
                      </>
                    ) : (
                      <div className="aphr">
                        <span className="apcount">{g.items.length} approvals{waitingN > 0 ? ` · ${waitingN} waiting` : ""}</span>
                        {rq && <span className="apsm">Requested {fmtRelative(rq.created_at ?? first.created_at)}{rq.requester_name ? ` by ${rq.requester_name}` : ""}</span>}
                      </div>
                    )}
                  </div>

                  {!single && (
                    <ul className="apsteps">
                      {g.items.map((req) => (
                        <li key={req.id} className={`apwrap${leaving === req.id ? " leaving" : ""}`}>
                          <div className="apin">
                            <div className={`aprow sub${req.overdue ? " late" : ""}`}>
                              <div className="apspan">{stepInfo(req)}</div>
                              {statusCell(req)}
                              {dueCell(req)}
                              {actions(req)}
                            </div>
                          </div>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
          <div className="appager">
            <span className="num">{groups.length === 0 ? "0 results" : `${cur * pageSize + 1}–${Math.min((cur + 1) * pageSize, groups.length)} of ${groups.length}`}</span>
            <nav className="pager" aria-label="Pagination">
              <button type="button" className="pgb" disabled={cur === 0} onClick={() => setPage(cur - 1)} aria-label="Previous page"><ChevronDown className="ic prev" /></button>
              {pageList(pageCount, cur).map((n, i) => n === -1
                ? <span key={`e${i}`} className="pge">…</span>
                : <button key={n} type="button" className={`pgb${n === cur ? " on" : ""}`} aria-current={n === cur ? "page" : undefined} onClick={() => setPage(n)}>{n + 1}</button>)}
              <button type="button" className="pgb" disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} aria-label="Next page"><ChevronDown className="ic next" /></button>
            </nav>
            <label className="rpp">Requests per page <select aria-label="Rows per page" value={pageSize} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(0); }}>{[5, 10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
          </div>
        </div>
      )}

      <RejectModal
        request={rejectFor}
        subject={rejectFor ? subjectName(rejectFor) : ""}
        onClose={() => setRejectFor(null)}
        busy={busyId === rejectFor?.id}
        onReject={(comment) => rejectFor && decide(rejectFor, "reject", comment)}
      />

      <ReassignModal
        target={reassignFor}
        subject={reassignFor ? subjectName(reassignFor.req) : ""}
        onClose={() => setReassignFor(null)}
        busy={!!reassignFor && busyId === reassignFor.req.id}
        onConfirm={(toUserId) =>
          reassignFor && reassign(reassignFor.req, toUserId, reassignFor.kind)
        }
      />
    </div>
  );
}

function Modal({
  open,
  onClose,
  title,
  size,
  footer,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  size?: "sm";
  footer: React.ReactNode;
  children: React.ReactNode;
}) {
  if (!open) return null;
  return (
    <div className="ovl" onClick={onClose}>
      <div className={`dlg${size === "sm" ? " sm" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="dlghd">
          <h2>{title}</h2>
          <button className="xbtn" onClick={onClose} aria-label="Close">
            <X className="ic" />
          </button>
        </div>
        <div className="dlgbd">{children}</div>
        <div className="dlgft">{footer}</div>
      </div>
    </div>
  );
}

function ReassignModal({
  target,
  subject,
  onClose,
  onConfirm,
  busy,
}: {
  target: { req: ApprovalRequest; kind: "delegate" | "escalate" } | null;
  subject: string;
  onClose: () => void;
  onConfirm: (toUserId: string) => void;
  busy: boolean;
}) {
  const [toUserId, setToUserId] = useState("");

  useEffect(() => {
    setToUserId("");
  }, [target?.req.id, target?.kind]);

  const kind = target?.kind ?? "delegate";
  // Only the people the server would accept: someone who can approve (delegate) or a senior
  // approver (escalate). It used to be every active user, real mailboxes included.
  const { data: pool, isLoading, error } = useQuery({
    queryKey: ["reassign-candidates", target?.req.id, kind],
    queryFn: () => approvalsApi.reassignCandidates(target!.req.id, kind),
    enabled: !!target,
    staleTime: 0,
  });
  const chosen = (pool ?? []).find((a) => a.id === toUserId);

  return (
    <Modal
      open={!!target}
      onClose={onClose}
      title={kind === "delegate" ? "Delegate approval" : "Escalate approval"}
      size="sm"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" disabled={!toUserId || busy} onClick={() => onConfirm(toUserId)}>
            {busy ? "…" : kind === "delegate" ? "Delegate" : "Escalate"}
          </button>
        </>
      }
    >
      <p className="subj">
        <span className="k">For</span> {subject}
        {target?.req.step_order ? <span className="dim"> · step {target.req.step_order}</span> : null}
      </p>
      <p className="hint">
        {kind === "delegate"
          ? "Reassign this pending step to another approver. They receive a fresh single-use approval link."
          : "Escalate this step to a senior approver. They take over the pending decision."}
      </p>
      <div className="field">
        <label htmlFor="reassign-to">{kind === "delegate" ? "Delegate to" : "Escalate to"}</label>
        <select id="reassign-to" className="sel" value={toUserId} onChange={(e) => setToUserId(e.target.value)}>
          <option value="">{isLoading ? "Loading…" : "Select approver…"}</option>
          {(pool ?? []).map((a) => (
            <option key={a.id} value={a.id}>
              {a.full_name} — {a.email}
            </option>
          ))}
        </select>
        {error ? (
          <span className="hint err" role="alert">{error instanceof Error ? error.message : "Couldn't load the list."}</span>
        ) : !isLoading && (pool ?? []).length === 0 ? (
          <span className="hint" role="status">
            {kind === "delegate"
              ? "No one else can take this approval."
              : "No senior approver is available to take this. Try Delegate, or ask an administrator."}
          </span>
        ) : null}
      </div>
      {chosen ? (
        <p className="hint" role="status">
          {chosen.full_name} ({chosen.email}) will get an email with a link to approve or reject.
        </p>
      ) : null}
    </Modal>
  );
}

function RejectModal({
  request,
  subject,
  onClose,
  onReject,
  busy,
}: {
  request: ApprovalRequest | null;
  subject: string;
  onClose: () => void;
  onReject: (comment: string) => void;
  busy: boolean;
}) {
  const [comment, setComment] = useState("");

  useEffect(() => {
    setComment("");
  }, [request?.id]);

  return (
    <Modal
      open={!!request}
      onClose={onClose}
      title="Reject approval"
      size="sm"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn danger"
            disabled={!comment.trim() || busy}
            onClick={() => onReject(comment.trim())}
          >
            {busy ? "…" : "Reject"}
          </button>
        </>
      }
    >
      <p className="subj">
        <span className="k">For</span> {subject}
        {request?.step_order ? <span className="dim"> · step {request.step_order}</span> : null}
      </p>
      <div className="field">
        <label htmlFor="reject-reason">Reason</label>
        <input
          id="reject-reason"
          autoFocus
          className="inp"
          placeholder="Why is this being rejected?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
        />
        <span className="hint">A comment is required to reject.</span>
      </div>
    </Modal>
  );
}

const APPRV_CSS = `
.apprv{padding:0}
.apprv *{box-sizing:border-box}
.apprv .dim{color:var(--ink-3)}
.apprv .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}
.apprv .kcard{padding:14px;gap:12px}
.apprv .kcard{cursor:pointer}
.apprv .aptoolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0 14px}
.apprv .btn.danger{border-color:color-mix(in srgb,var(--crit) 45%,var(--border));color:var(--crit);background:var(--surface)}
.apprv .btn.danger:hover{background:var(--crit-soft)}
.apprv .btn.sm{padding:6px 11px;font-size:12px;display:inline-flex;align-items:center;gap:6px}
.apprv .btn.iconbtn{padding:6px 8px}

@keyframes apin{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
.apprv .aa-in{animation:apin .5s cubic-bezier(.2,.7,.2,1) both}
@keyframes appop{from{opacity:0;transform:translateY(-4px) scale(.97)}to{opacity:1;transform:none}}
.apprv .aa-pop{animation:appop .16s ease both}

.apprv .aplist{display:flex;flex-direction:column;gap:10px}
.apprv .apboard{--apcols:36px minmax(0,3.4fr) minmax(120px,.9fr) minmax(130px,.8fr) minmax(110px,.7fr) 224px}
.apprv .aplist2{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.apprv .apgroup{--st:var(--border-strong);position:relative;border-radius:16px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);opacity:0;max-height:400px;animation:aprise .55s cubic-bezier(.2,.8,.2,1) forwards;transition:transform .22s cubic-bezier(.2,.8,.2,1),box-shadow .22s,max-height .45s,opacity .3s,background .3s}
@keyframes aprise{from{opacity:0;transform:translateY(14px) scale(.99)}to{opacity:1;transform:none}}
.apprv .apgroup.st-late{--st:var(--crit)}.apprv .apgroup.st-wait{--st:var(--warn)}.apprv .apgroup.st-ok{--st:var(--good)}
.apprv .apgroup:hover{transform:translateY(-2px);box-shadow:0 16px 30px -22px rgba(20,26,40,.45),0 0 0 1px var(--border-strong)}
.apprv .apgroup.leaving{max-height:0;opacity:0;transform:translateX(30px);background:var(--good-soft);overflow:hidden;margin-bottom:-8px}
/* overdue: a shake on arrival, then a slow red pulse so it cannot be missed */
.apprv .apgroup.st-late{animation:aprise .55s cubic-bezier(.2,.8,.2,1) forwards,apshake .6s .6s ease-in-out,apalert 2.4s 1.25s ease-in-out infinite}
@keyframes apshake{0%,100%{transform:none}20%{transform:translateX(-5px)}40%{transform:translateX(5px)}60%{transform:translateX(-3px)}80%{transform:translateX(3px)}}
@keyframes apalert{0%,100%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 40%,var(--border)),0 0 0 0 color-mix(in srgb,var(--crit) 35%,transparent)}50%{box-shadow:0 1px 2px rgba(20,26,40,.05),0 0 0 1px color-mix(in srgb,var(--crit) 65%,var(--border)),0 0 0 6px color-mix(in srgb,var(--crit) 0%,transparent)}}
.apprv .pill.beat{animation:apbeat 1.4s ease-in-out infinite}
@keyframes apbeat{50%{opacity:.55;transform:scale(.96)}}
.apprv .apdue.late .v{animation:apbeat 1.6s ease-in-out infinite}

.apprv .aphd{display:grid;grid-template-columns:var(--apcols);gap:14px;align-items:center;padding:11px 16px 11px 18px;border-radius:16px}
.apprv .aphd.click{cursor:pointer}
.apprv .aphd.click:hover .lnk{color:var(--accent)}
.apprv .apic{width:36px;height:36px;border-radius:11px;display:grid;place-items:center;flex:none;transition:transform .3s}
.apprv .apgroup:hover .apic{transform:rotate(-6deg) scale(1.06)}
.apprv .apic.c{background:var(--accent-soft);color:var(--accent)}
.apprv .apic.r{background:var(--warn-soft);color:var(--warn)}
.apprv .apic .ic{width:17px;height:17px}
.apprv .apnm{min-width:0;display:flex;flex-direction:column;gap:5px}
.apprv .aptitle{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:670;color:var(--ink);min-width:0;letter-spacing:-.01em}
.apprv .aptitle .lnk{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.apprv .lnk{color:var(--ink);text-decoration:none}
.apprv .apref{flex:none;font:700 10px var(--mono);color:var(--accent);background:var(--accent-soft);padding:1px 7px;border-radius:99px}
.apprv .apmeta{display:flex;flex-wrap:nowrap;align-items:center;gap:5px;min-width:0;overflow:hidden}
.apprv .apchip{display:inline-flex;align-items:center;gap:5px;flex:0 1 auto;min-width:0;max-width:210px;height:20px;padding:0 8px;border-radius:99px;font-size:11px;color:var(--ink-2);background:var(--surface-2);text-decoration:none;white-space:nowrap}
.apprv .apchip .ic{width:11px;height:11px;color:var(--ink-3);flex:none}
.apprv a.apchip:hover{background:var(--accent-soft);color:var(--accent)}
.apprv .trunc{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.apprv .aprisk{display:inline-flex;align-items:center;gap:5px;flex:none;font-size:11px;font-weight:600;margin-left:2px}
.apprv .aprisk i{width:6px;height:6px;border-radius:50%;background:currentColor}
.apprv .aprisk.risk-high{color:var(--crit)}.apprv .aprisk.risk-medium{color:var(--warn)}.apprv .aprisk.risk-low{color:var(--good)}
.apprv .aphr{grid-column:3/-1;display:flex;align-items:center;justify-content:flex-end;gap:12px;text-align:right}
.apprv .apcount{font-size:11.5px;font-weight:650;color:var(--ink-2);background:var(--surface-2);padding:3px 10px;border-radius:99px;white-space:nowrap}
.apprv .apsm{font-size:11px;color:var(--ink-3)}

.apprv .apsteps{list-style:none;margin:0;padding:0 10px 10px 18px;display:flex;flex-direction:column;gap:5px}
.apprv .apwrap{display:grid;grid-template-rows:1fr;transition:grid-template-rows .45s cubic-bezier(.4,0,.2,1),opacity .3s}
.apprv .apin{min-height:0}
.apprv .apwrap.leaving{grid-template-rows:0fr;opacity:0}
.apprv .apwrap.leaving .apin{overflow:hidden}
.apprv .apwrap.leaving .aprow{background:var(--good-soft)}
.apprv .aprow.sub{display:grid;grid-template-columns:var(--apcols);gap:14px;align-items:center;padding:7px 8px 7px 0;border-radius:11px;background:var(--inset);transition:background .35s,transform .2s}
.apprv .aprow.sub:hover{transform:translateX(3px)}
.apprv .aprow.sub.late{background:color-mix(in srgb,var(--crit) 7%,var(--inset))}
.apprv .apspan{grid-column:1/4;padding-left:12px;min-width:0}
.apprv .apcol{min-width:0;display:flex;flex-direction:column;gap:1px}
.apprv .apstep{font-weight:640;font-size:12.5px;color:var(--ink)}
.apprv .apstep .dim{font-weight:500}
.apprv .apstat{display:flex;flex-wrap:wrap;gap:4px}
.apprv .apdue .v{font-weight:650;font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.apprv .apdue.late .v{color:var(--crit)}
.apprv .apact{display:flex;align-items:center;justify-content:flex-end;gap:6px;white-space:nowrap}
.apprv .abtn{display:inline-flex;align-items:center;justify-content:center;gap:6px;height:32px;padding:0 12px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:600 12px var(--sans);transition:transform .15s,background .15s,box-shadow .2s}
.apprv .abtn:hover:not(:disabled){transform:translateY(-1px)}
.apprv .abtn:active:not(:disabled){transform:scale(.96)}
.apprv .abtn:disabled{opacity:.6;cursor:progress}
.apprv .abtn.ok{min-width:96px;border-color:transparent;background:var(--good);color:#fff}
.apprv .abtn.ok:hover:not(:disabled){box-shadow:0 8px 18px -8px var(--good)}
.apprv .abtn.no{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
.apprv .abtn.no:hover:not(:disabled){background:var(--crit-soft)}
.apprv .abtn.ico{width:32px;padding:0}
.apprv .abtn.ico:hover:not(:disabled){background:var(--surface-2)}
.apprv .spin{width:13px;height:13px;border-radius:50%;border:2px solid rgba(255,255,255,.4);border-top-color:#fff;animation:apspin .7s linear infinite}
@keyframes apspin{to{transform:rotate(360deg)}}
.apprv .apwait,.apprv .apdone{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-3)}
.apprv .apdone{font-weight:600;color:var(--ink-2)}
.apprv .apdone.approved{color:var(--good)}.apprv .apdone.rejected{color:var(--crit)}
.apprv .apmore{position:relative}
.apprv .apmenu{position:absolute;right:0;top:calc(100% + 6px);z-index:30;min-width:150px;padding:5px;border-radius:12px;border:1px solid var(--border);background:var(--surface);box-shadow:var(--pop,0 12px 30px rgba(20,26,40,.18));text-align:left}
.apprv .apmenu button{display:block;width:100%;text-align:left;padding:8px 10px;border-radius:8px;border:0;background:none;color:var(--ink);font-size:12.5px;cursor:pointer}
.apprv .apmenu button:hover{background:var(--surface-2)}
.apprv .appager{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:14px;padding:0 4px;font-size:12.5px;color:var(--ink-2)}
@media (max-width:1400px){
  .apprv .aphd{grid-template-columns:36px minmax(0,1fr);row-gap:8px}
  .apprv .aphd>.apcol,.apprv .aphd>.apact{grid-column:2}
  .apprv .aphd>.apcol{display:inline-flex;flex-direction:row;gap:10px;align-items:center}
  .apprv .aphr{grid-column:2;justify-content:flex-start}
  .apprv .aprow.sub{display:flex;flex-wrap:wrap;gap:8px 20px;padding-left:0}
  .apprv .apspan{grid-column:auto;flex:1 1 180px}
  .apprv .apcol{flex:1 1 120px}
  .apprv .apact{flex:1 1 100%;justify-content:flex-start}
}
.apprv .approg{display:flex;align-items:center;gap:3px;margin-top:4px}
.apprv .approg span{width:16px;height:4px;border-radius:2px;background:var(--surface-2)}
.apprv .approg span.on{background:var(--good)}
.apprv .approg em{font-style:normal;font-size:10.5px;color:var(--ink-3);margin-left:5px}

.apprv .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:99px;font:600 10.5px var(--sans);white-space:nowrap}
.apprv .pill.ok{background:var(--good-soft);color:var(--good)}
.apprv .pill.warn{background:var(--warn-soft);color:var(--warn)}
.apprv .pill.crit{background:var(--crit-soft);color:var(--crit)}
.apprv .pill.accent{background:var(--accent-soft);color:var(--accent)}
.apprv .pill.neutral{background:var(--surface-2);color:var(--ink-2)}
.apprv .pill.risk-high{background:var(--crit-soft);color:var(--crit)}
.apprv .pill.risk-medium{background:var(--warn-soft);color:var(--warn)}
.apprv .pill.risk-low{background:var(--good-soft);color:var(--good)}

.apprv .skelcard{height:60px;border-radius:16px;background:var(--surface-2);animation:apprvpulse 1.4s ease-in-out infinite}
@keyframes apprvpulse{50%{opacity:.55}}
.apprv .apempty{display:flex;flex-direction:column;align-items:center;text-align:center;padding:56px 16px;border:1px dashed var(--border-strong);border-radius:18px}
.apprv .apempty .eic{width:48px;height:48px;border-radius:14px;background:var(--good-soft);color:var(--good);display:grid;place-items:center;margin-bottom:10px}
.apprv .apempty.err{color:var(--crit)}
.apprv .apempty .et{font-weight:660;font-size:15px;color:var(--ink)}
.apprv .apempty .ed{font-size:12.5px;color:var(--ink-2);max-width:380px;margin-top:3px}

.apprv .ovl{position:fixed;inset:0;background:rgba(10,13,20,.45);backdrop-filter:blur(2px);display:flex;align-items:center;justify-content:center;padding:20px;z-index:50;animation:apin .2s ease both}
.apprv .dlg{width:100%;max-width:460px;background:var(--surface);border:1px solid var(--border);border-radius:18px;box-shadow:var(--pop,0 12px 30px rgba(20,26,40,.2));display:flex;flex-direction:column;max-height:calc(100vh - 40px);animation:appop .2s ease both}
.apprv .dlg.sm{max-width:420px}
.apprv .dlghd{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:16px 20px;border-bottom:1px solid var(--border)}
.apprv .dlghd h2{margin:0;font-size:15.5px;font-weight:660}
.apprv .xbtn{width:28px;height:28px;border-radius:8px;border:0;background:none;color:var(--ink-3);display:grid;place-items:center}
.apprv .xbtn:hover{background:var(--surface-2);color:var(--ink)}
.apprv .dlgbd{padding:18px 20px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.apprv .dlgft{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:14px 20px;border-top:1px solid var(--border)}

.apprv .field{display:flex;flex-direction:column;gap:5px}
.apprv .field label{font:600 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3)}
.apprv .inp,.apprv .sel{padding:9px 11px;border-radius:10px;border:1px solid var(--border-strong);background:var(--inset);color:var(--ink);font:inherit;outline:none}
.apprv .inp:focus,.apprv .sel:focus{border-color:var(--accent)}
.apprv .hint{font-size:11.5px;color:var(--ink-3);margin:0}
.apprv .hint.err{color:var(--crit)}
.apprv .subj{margin:0;font-size:12.5px;color:var(--ink);font-weight:600}
.apprv .subj .k{font:600 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3);margin-right:6px}
.apprv .subj .dim{font-weight:500}
@media (prefers-reduced-motion:reduce){.apprv *{animation:none!important;transition:none!important}.apprv .apgroup{opacity:1!important}}
`;
