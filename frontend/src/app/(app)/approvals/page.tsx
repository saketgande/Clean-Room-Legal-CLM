"use client";

// Approvals — every sign-off chain in flight + the approver groups workflow
// Approval steps draw from, in the mockup style (scoped `.apprv`). Who approves
// is set on each workflow's Approval step (Admin → Workflows); there are no
// routing rules.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Send, X } from "lucide-react";
import { approvalsApi, contractsApi, usersApi } from "@/lib/endpoints";
import { can } from "@/lib/intake";
import { ChainsTab } from "./_chains-tab";
import { ConditionRulesTab } from "./_condition-rules-tab";
import { contractDisplayName, fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import type { ApprovalRequest } from "@/lib/types";

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

export default function ApprovalsPage() {
  // Same query key as RequestsTab, so react-query serves both from one fetch.
  const { data: allReqs } = useQuery({ queryKey: ["approvals"], queryFn: approvalsApi.list });
  const pending = (allReqs ?? []).filter((r) => r.status === "pending").length;
  const overdue = (allReqs ?? []).filter((r) => r.overdue).length;
  const mine = (allReqs ?? []).filter((r) => r.can_decide).length;

  // Condition-driven approval chains sit beside the request list. Chains are
  // started by a workflow's Approval step, never from this page.
  const [tab, setTab] = useState("requests");
  const { user } = useAuth();
  const canManageChains = can(user, "approval_chain:manage");
  const tabs = [
    { id: "requests", label: "Requests" },
    { id: "chains", label: "Chains" },
    ...(canManageChains ? [{ id: "conditions", label: "Condition rules" }] : []),
  ];

  return (
    <div className="apprv">
      <style dangerouslySetInnerHTML={{ __html: APPRV_CSS }} />
      <div className="hd">
        <h1>Approvals</h1>
        <span className="sub">every sign-off in flight, and where each one is waiting</span>
        <div className="sp" />
        <div className="stat">
          <span>
            <b>{pending}</b> pending
          </span>
          <span className="crit">
            <b>{overdue}</b> overdue
          </span>
          <span>
            <b>{mine}</b> yours to action
          </span>
        </div>
      </div>

      <div className="body">
        <div className="tabrow">
          {tabs.map((t) => (
            <button key={t.id} className={`tabp${tab === t.id ? " on" : ""}`} onClick={() => setTab(t.id)}>
              {t.label}
            </button>
          ))}
        </div>
        {tab === "chains" ? (
          <ChainsTab />
        ) : tab === "conditions" && canManageChains ? (
          <ConditionRulesTab />
        ) : (
          <RequestsTab />
        )}
      </div>
    </div>
  );
}

// ---- Requests ------------------------------------------------------------
function RequestsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const [rejectFor, setRejectFor] = useState<ApprovalRequest | null>(null);
  const [reassignFor, setReassignFor] = useState<
    { req: ApprovalRequest; kind: "delegate" | "escalate" } | null
  >(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approvals"],
    queryFn: approvalsApi.list,
  });
  const { data: eligible } = useQuery({
    queryKey: ["eligible-approvers"],
    queryFn: approvalsApi.eligibleApprovers,
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const { data: orgUsers } = useQuery({
    queryKey: ["org-users"],
    queryFn: () => usersApi.list(),
  });

  const titleMap = useMemo(() => {
    const m = new Map<string, string>();
    // Name the agreement, not the upload it arrived as — the same rule the
    // contract register uses, so a row reads identically in both places.
    for (const c of contracts ?? []) m.set(c.id, contractDisplayName(c));
    return m;
  }, [contracts]);
  // Resolve approver ids to names (falls back to the eligible-approver pool).
  const nameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of eligible ?? []) m.set(a.id, a.full_name);
    for (const u of orgUsers ?? []) m.set(u.id, u.full_name || u.email);
    return m;
  }, [eligible, orgUsers]);
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

  async function decide(
    req: ApprovalRequest,
    decision: "approve" | "reject",
    comment?: string,
  ) {
    setBusyId(req.id);
    try {
      await approvalsApi.decide(req.id, decision, comment);
      qc.invalidateQueries({ queryKey: ["approvals"] });
      notify(`Approval ${decision === "approve" ? "approved" : "rejected"}`, "success");
      setRejectFor(null);
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
      notify(kind === "delegate" ? "Delegated" : "Escalated", "success");
      setReassignFor(null);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Reassign failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div>
      {isLoading ? (
        <div className="tablewrap">
          <div className="skelrows">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="skelrow" />
            ))}
          </div>
        </div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load approvals."}</div>
      ) : (data ?? []).length === 0 ? (
        <div className="empty">
          <div className="eic">
            <CheckCircle2 className="ic" />
          </div>
          <div className="et">No approval requests</div>
          <div className="ed">Approvals start when a contract's workflow reaches an Approval step.</div>
        </div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Contract</th>
                <th>Step</th>
                <th>Status</th>
                <th>Approver</th>
                <th>Due</th>
                <th className="c-r">Actions</th>
              </tr>
            </thead>
            <tbody>
              {[...(data ?? [])]
                .sort((a, b) => Number(b.overdue ?? false) - Number(a.overdue ?? false))
                .map((req) => (
                  <tr key={req.id}>
                    <td className="c-strong">
                      {req.contract_id ? (
                        <Link href={`/contracts/${req.contract_id}`} className="lnk">
                          {/* The row carries its own title now, so an approval
                              against an archived contract still shows the name
                              instead of falling through to "Untitled". */}
                          {req.contract_title
                            ? contractDisplayName({
                                title: req.contract_title,
                                contract_type: req.contract_type,
                              })
                            : titleMap.get(req.contract_id) ?? "Contract unavailable"}
                        </Link>
                      ) : (
                        <span className="dim">No contract linked</span>
                      )}
                      {req.contract_archived && <span className="pill neutral">archived</span>}
                    </td>
                    <td className="c-nw">
                      <span className="stepcell">
                        {req.step_order ? `Step ${req.step_order}` : "—"}
                        {(req.needed ?? 1) > 1 && (
                          <span className={`pill ${(req.approvals ?? 0) >= (req.needed ?? 1) ? "ok" : "warn"}`}>
                            {req.approvals ?? 0}/{req.needed} signed
                          </span>
                        )}
                      </span>
                    </td>
                    <td>
                      <span className="stepcell">
                        <span className={`pill ${toneCls(statusTone(req.status))}`}>{titleCase(req.status)}</span>
                        {req.overdue && <span className="pill crit">Overdue</span>}
                      </span>
                    </td>
                    <td className="c-nw">
                      {req.approver_role
                        ? titleCase(req.approver_role)
                        : req.approver_team_name ??
                          (req.approver_team_id
                            ? "Team"
                            : req.approver_user_id
                              ? nameMap.get(req.approver_user_id) ?? "Assigned approver"
                              : "—")}
                    </td>
                    <td className={req.overdue ? "c-nw c-danger" : "c-nw"}>{fmtDate(req.due_at)}</td>
                    <td className="c-r">
                      {req.status === "pending" && canDecide(req) ? (
                        <div className="rowact">
                          <button
                            className="btn sm pri"
                            disabled={busyId === req.id}
                            onClick={() => decide(req, "approve")}
                          >
                            {busyId === req.id ? "…" : "Approve"}
                          </button>
                          <button
                            className="btn sm danger"
                            disabled={busyId === req.id}
                            onClick={() => setRejectFor(req)}
                          >
                            Reject
                          </button>
                          <button
                            className="btn sm"
                            disabled={busyId === req.id}
                            onClick={() => setReassignFor({ req, kind: "delegate" })}
                            title="Hand this step to another approver"
                          >
                            Delegate
                          </button>
                          <button
                            className="btn sm"
                            disabled={busyId === req.id}
                            onClick={() => setReassignFor({ req, kind: "escalate" })}
                            title="Escalate this step to a senior approver"
                          >
                            Escalate
                          </button>
                        </div>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      <RejectModal
        request={rejectFor}
        onClose={() => setRejectFor(null)}
        busy={busyId === rejectFor?.id}
        onReject={(comment) => rejectFor && decide(rejectFor, "reject", comment)}
      />

      <ReassignModal
        target={reassignFor}
        approvers={eligible ?? []}
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
  approvers,
  onClose,
  onConfirm,
  busy,
}: {
  target: { req: ApprovalRequest; kind: "delegate" | "escalate" } | null;
  approvers: { id: string; full_name: string; email: string }[];
  onClose: () => void;
  onConfirm: (toUserId: string) => void;
  busy: boolean;
}) {
  const [toUserId, setToUserId] = useState("");

  useEffect(() => {
    setToUserId("");
  }, [target?.req.id, target?.kind]);

  const kind = target?.kind ?? "delegate";
  const pool = approvers.filter((a) => a.id !== target?.req.approver_user_id);

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
      <p className="hint">
        {kind === "delegate"
          ? "Reassign this pending step to another approver. They receive a fresh single-use approval link."
          : "Escalate this step to a senior approver. They take over the pending decision."}
      </p>
      <div className="field">
        <label>{kind === "delegate" ? "Delegate to" : "Escalate to"}</label>
        <select className="sel" value={toUserId} onChange={(e) => setToUserId(e.target.value)}>
          <option value="">Select approver…</option>
          {pool.map((a) => (
            <option key={a.id} value={a.id}>
              {a.full_name} — {a.email}
            </option>
          ))}
        </select>
      </div>
    </Modal>
  );
}

function RejectModal({
  request,
  onClose,
  onReject,
  busy,
}: {
  request: ApprovalRequest | null;
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
      <div className="field">
        <label>Reason</label>
        <input
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
.apprv{
  --bg:#f4f6f9;
  
  
  
  --shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:var(--font-sans);
  padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans);-webkit-font-smoothing:antialiased;
}
.dark .apprv{
  --bg:#0c0f16;
  
  
  
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55);
}
.apprv *{box-sizing:border-box}
.apprv button{cursor:pointer;font:inherit}
.apprv .dim{color:var(--ink-3)}
.apprv .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}

/* Same top bar as Contracts and Legal Intake: title and subtitle on one line,
   live counters pushed right. Was a stacked 19px title with the subtitle below,
   which is why this page read as a different app from the rest of the shell. */
.apprv .hd{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.apprv .hd h1{margin:0;font-size:16px;font-weight:660;letter-spacing:-.015em}
.apprv .hd .sub{margin:0;font-size:12.5px;font-weight:500;color:var(--ink-3)}
.apprv .hd .sp{flex:1}
.apprv .hd .stat{display:flex;gap:14px;align-items:center;font-size:12px;color:var(--ink-2)}
.apprv .hd .stat b{color:var(--ink);font-weight:650}
.apprv .hd .stat .warn{color:var(--warn)} .apprv .hd .stat .crit{color:var(--crit)}

.apprv .tabrow{display:flex;gap:2px;flex-wrap:wrap;border-bottom:1px solid var(--border);margin-bottom:16px}
.apprv .tabp{padding:8px 12px;border:none;border-bottom:2px solid transparent;margin-bottom:-1px;background:none;color:var(--ink-2);font-weight:600;font-size:12.5px;cursor:pointer}
.apprv .tabp:hover{color:var(--ink)}
.apprv .tabp.on{color:var(--accent);border-bottom-color:var(--accent)}

.apprv .actrow{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-bottom:14px}
.apprv .note{font-size:12px;color:var(--ink-2);max-width:640px;margin:0}
.apprv .note strong{color:var(--ink)}





.apprv .btn.danger{border-color:color-mix(in srgb,var(--crit) 45%,var(--border));color:var(--crit);background:var(--surface)}
.apprv .btn.danger:hover{background:var(--crit-soft)}
.apprv .btn.sm{padding:5px 10px;font-size:12px}


.apprv .tablewrap{overflow-x:auto}
.apprv .tablewrap table{min-width:820px}
.apprv table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.apprv thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:10px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
.apprv tbody td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
.apprv tbody tr:last-child td{border-bottom:0}
.apprv tbody tr:hover td{background:var(--inset)}
.apprv .c-r{text-align:right}
/* Real agreement names are longer than the filenames this column used to show,
   so give it the slack and keep the short cells on one line — otherwise every
   row grows to two lines and the table loses half its rows per screen. */
.apprv td:first-child{width:38%}
.apprv .c-nw{white-space:nowrap}
.apprv .c-strong{font-weight:600;color:var(--ink)}
.apprv .c-sub{font-size:11.5px;color:var(--ink-3);margin-top:2px}
.apprv .c-danger{font-weight:600;color:var(--crit)}
.apprv .lnk{color:var(--accent);font-weight:600;text-decoration:none}
.apprv .lnk:hover{text-decoration:underline}

.apprv .stepcell{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.apprv .rowact{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:6px}

.apprv .pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:99px;font:600 10.5px var(--sans);white-space:nowrap}
.apprv .pill.ok{background:var(--good-soft);color:var(--good)}
.apprv .pill.warn{background:var(--warn-soft);color:var(--warn)}
.apprv .pill.crit{background:var(--crit-soft);color:var(--crit)}
.apprv .pill.accent{background:var(--accent-soft);color:var(--accent)}
.apprv .pill.neutral{background:var(--surface-2);color:var(--ink-2)}

.apprv .skelrows{padding:6px}
.apprv .skelrow{height:38px;border-radius:8px;margin:6px;background:var(--surface-2);animation:apprvpulse 1.4s ease-in-out infinite}
@keyframes apprvpulse{50%{opacity:.55}}



.apprv .empty .eic{width:40px;height:40px;border-radius:10px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:4px}
.apprv .empty .et{font-weight:660;font-size:14px;color:var(--ink)}
.apprv .empty .ed{font-size:12.5px;color:var(--ink-2);max-width:380px;margin-bottom:8px}

.apprv .ovl{position:fixed;inset:0;background:rgba(10,13,20,.45);display:flex;align-items:center;justify-content:center;padding:20px;z-index:50}
.apprv .dlg{width:100%;max-width:460px;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--pop);display:flex;flex-direction:column;max-height:calc(100vh - 40px)}
.apprv .dlg.sm{max-width:400px}
.apprv .dlghd{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 18px;border-bottom:1px solid var(--border)}
.apprv .dlghd h2{margin:0;font-size:15px;font-weight:660}
.apprv .xbtn{width:26px;height:26px;border-radius:7px;border:0;background:none;color:var(--ink-3);display:grid;place-items:center}
.apprv .xbtn:hover{background:var(--surface-2);color:var(--ink)}
.apprv .dlgbd{padding:16px 18px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.apprv .dlgft{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:14px 18px;border-top:1px solid var(--border)}

.apprv .field{display:flex;flex-direction:column;gap:5px}
.apprv .field label{font:600 10.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3)}
.apprv .inp,.apprv .sel{padding:8px 10px;border-radius:8px;border:1px solid var(--border-strong);background:var(--inset);color:var(--ink);font:inherit;outline:none}
.apprv .inp:focus,.apprv .sel:focus{border-color:var(--accent)}
.apprv .hint{font-size:11.5px;color:var(--ink-3);margin:0}

.apprv .memlist{display:flex;flex-direction:column;gap:1px;max-height:320px;overflow-y:auto}
.apprv .memrow{display:flex;align-items:center;gap:9px;padding:8px;border-radius:8px;cursor:pointer}
.apprv .memrow:hover{background:var(--surface-2)}
.apprv .memrow input{width:15px;height:15px;accent-color:var(--accent)}
.apprv .memrow .mn{font-size:12.5px;color:var(--ink)}
.apprv .memrow .me{font-size:11.5px;color:var(--ink-3);margin-left:auto}
`;
