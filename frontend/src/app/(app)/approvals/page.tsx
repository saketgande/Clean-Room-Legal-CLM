"use client";

// Approvals & routing — contract sign-off chains + approver groups, in the new
// mockup style (scoped `.apprv`). "Approval routing" (rule builder) and
// "Intake routing" tabs delegate to their own files unchanged.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, Send, Users, X } from "lucide-react";
import { approvalsApi, contractsApi } from "@/lib/endpoints";
import { can } from "@/lib/intake";
import { RoutingTab as IntakeRoutingTab } from "../intake/_phase1";
import { RulesTab } from "./_rules-builder";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
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
  const [tab, setTab] = useState("requests");
  const { user } = useAuth();
  const isAdmin = can(user, "admin_panel:access");

  const tabs = [
    { id: "requests", label: "Requests" },
    { id: "rules", label: "Approval routing" },
    { id: "groups", label: "Approver groups" },
    { id: "intake", label: "Intake routing" },
  ];

  return (
    <div className="apprv">
      <style dangerouslySetInnerHTML={{ __html: APPRV_CSS }} />
      <div className="hd">
        <div>
          <h1>Approvals &amp; routing</h1>
          <p className="sub">
            Route contracts through a multi-step sign-off chain, and route incoming legal requests to the right team.
          </p>
        </div>
      </div>

      <div className="tabrow">
        {tabs.map((t) => (
          <button key={t.id} className={`tabp${tab === t.id ? " on" : ""}`} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="body">
        {tab === "requests" ? (
          <RequestsTab />
        ) : tab === "rules" ? (
          <RulesTab />
        ) : tab === "groups" ? (
          <GroupsTab />
        ) : (
          <IntakeRoutingTab isAdmin={isAdmin} />
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
  const [submitOpen, setSubmitOpen] = useState(false);
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

  const titleMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of contracts ?? []) m.set(c.id, c.title);
    return m;
  }, [contracts]);
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
      <div className="actrow">
        <button className="btn pri" onClick={() => setSubmitOpen(true)}>
          <Send className="ic" />
          Submit for approval
        </button>
      </div>

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
          <div className="ed">Submit a contract for approval to start the sign-off process.</div>
          <button className="btn pri" onClick={() => setSubmitOpen(true)}>
            <Send className="ic" />
            Submit for approval
          </button>
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
                      <Link href={`/contracts/${req.contract_id}`} className="lnk">
                        {titleMap.get(req.contract_id) ?? req.contract_id}
                      </Link>
                    </td>
                    <td>
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
                    <td>
                      {req.approver_role
                        ? titleCase(req.approver_role)
                        : req.approver_group_name ?? (req.approver_group_id ? "Group" : req.approver_user_id ?? "—")}
                    </td>
                    <td className={req.overdue ? "c-danger" : undefined}>{fmtDate(req.due_at)}</td>
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

      <SubmitModal
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        contracts={contracts ?? []}
        onSubmitted={() => {
          qc.invalidateQueries({ queryKey: ["approvals"] });
          notify("Submitted for approval", "success");
          setSubmitOpen(false);
        }}
      />

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

function SubmitModal({
  open,
  onClose,
  contracts,
  onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  contracts: { id: string; title: string }[];
  onSubmitted: () => void;
}) {
  const { notify } = useToast();
  const [contractId, setContractId] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!contractId) return;
    setBusy(true);
    try {
      await approvalsApi.submit({ contract_id: contractId });
      setContractId("");
      onSubmitted();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Submit for approval"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={submit} disabled={!contractId || busy}>
            {busy ? "…" : "Submit"}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Contract</label>
        <select className="sel" value={contractId} onChange={(e) => setContractId(e.target.value)}>
          <option value="">Select a contract…</option>
          {contracts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </select>
      </div>
      <p className="hint">
        The matching routing rule decides the approval chain. The first step is notified now; later steps
        activate automatically as each one approves.
      </p>
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

// ---- Approver groups -----------------------------------------------------
function GroupsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [newOpen, setNewOpen] = useState(false);
  const [membersFor, setMembersFor] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["approval-groups"],
    queryFn: approvalsApi.groups,
  });

  const activeGroup = (data ?? []).find((g) => g.id === membersFor) ?? null;

  function refresh() {
    qc.invalidateQueries({ queryKey: ["approval-groups"] });
  }

  return (
    <div>
      <div className="actrow">
        <p className="note">
          Named sets of people who <strong>sign off</strong> approval steps (any-one or all-members). For
          load-balanced <em>work assignment</em>, use Teams / pools under Legal Intake — those are a different
          thing.
        </p>
        <button className="btn pri" onClick={() => setNewOpen(true)}>
          <Plus className="ic" />
          New group
        </button>
      </div>

      {isLoading ? (
        <div className="tablewrap">
          <div className="skelrows">
            {[0, 1, 2].map((i) => (
              <div key={i} className="skelrow" />
            ))}
          </div>
        </div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load groups."}</div>
      ) : (data ?? []).length === 0 ? (
        <div className="empty">
          <div className="eic">
            <Users className="ic" />
          </div>
          <div className="et">No approver groups</div>
          <div className="ed">Create a group (e.g. Legal Counsel) and add the people who can approve for it.</div>
          <button className="btn pri" onClick={() => setNewOpen(true)}>
            <Plus className="ic" />
            New group
          </button>
        </div>
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Group</th>
                <th>Members</th>
                <th>Active</th>
                <th className="c-r">Actions</th>
              </tr>
            </thead>
            <tbody>
              {(data ?? []).map((g) => (
                <tr key={g.id}>
                  <td>
                    <div className="c-strong">{g.name}</div>
                    {g.description ? <div className="c-sub">{g.description}</div> : null}
                  </td>
                  <td>
                    {g.members.length === 0 ? (
                      <span className="pill warn">No members yet</span>
                    ) : (
                      g.members.map((m) => m.full_name).join(", ")
                    )}
                  </td>
                  <td>
                    <span className={`pill ${g.is_active ? "ok" : "neutral"}`}>
                      {g.is_active ? "Active" : "Inactive"}
                    </span>
                  </td>
                  <td className="c-r">
                    <button className="btn sm" onClick={() => setMembersFor(g.id)}>
                      <Users className="ic" />
                      Members
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <NewGroupModal
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreated={() => {
          refresh();
          notify("Group created", "success");
          setNewOpen(false);
        }}
      />
      <ManageMembersModal
        group={activeGroup}
        onClose={() => setMembersFor(null)}
        onSaved={() => {
          refresh();
          notify("Members updated", "success");
          setMembersFor(null);
        }}
      />
    </div>
  );
}

function NewGroupModal({
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
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await approvalsApi.createGroup({
        name: name.trim(),
        description: description.trim() || undefined,
      });
      setName("");
      setDescription("");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New approver group"
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={submit} disabled={!name.trim() || busy}>
            {busy ? "…" : "Create group"}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Name</label>
        <input className="inp" placeholder="e.g. Legal Counsel" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label>Description</label>
        <input className="inp" value={description} onChange={(e) => setDescription(e.target.value)} />
        <span className="hint">Optional.</span>
      </div>
    </Modal>
  );
}

function ManageMembersModal({
  group,
  onClose,
  onSaved,
}: {
  group: { id: string; name: string; members: { id: string }[] } | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const { data: people } = useQuery({
    queryKey: ["eligible-approvers"],
    queryFn: approvalsApi.eligibleApprovers,
    enabled: !!group,
  });

  useEffect(() => {
    setSelected(new Set((group?.members ?? []).map((m) => m.id)));
  }, [group?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function save() {
    if (!group) return;
    setBusy(true);
    try {
      await approvalsApi.setGroupMembers(group.id, [...selected]);
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!group}
      onClose={onClose}
      title={group ? `Members — ${group.name}` : "Members"}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={save} disabled={busy}>
            {busy ? "…" : "Save members"}
          </button>
        </>
      }
    >
      <div className="memlist">
        {(people ?? []).length === 0 ? (
          <p className="hint">No active users to add yet.</p>
        ) : (
          (people ?? []).map((u) => (
            <label key={u.id} className="memrow">
              <input type="checkbox" checked={selected.has(u.id)} onChange={() => toggle(u.id)} />
              <span className="mn">{u.full_name}</span>
              <span className="me">{u.email}</span>
            </label>
          ))
        )}
      </div>
    </Modal>
  );
}

const APPRV_CSS = `
.apprv{
  --bg:#f4f6f9;--surface:#ffffff;--surface-2:#eef1f6;--inset:#f8fafc;
  --ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;
  --accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;
  --good:#2f875f;--good-soft:#e4f1ea;--warn:#a9772b;--warn-soft:#f6edd9;--crit:#bb4835;--crit-soft:#f7e4df;
  --shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);
  --mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans);-webkit-font-smoothing:antialiased;
}
.dark .apprv{
  --bg:#0c0f16;--surface:#141922;--surface-2:#1b2130;--inset:#10141d;
  --ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;
  --accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;
  --good:#5cbf90;--good-soft:#15271f;--warn:#d3a24e;--warn-soft:#2a2213;--crit:#e2705c;--crit-soft:#2c1a17;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55);
}
.apprv *{box-sizing:border-box}
.apprv button{cursor:pointer;font:inherit}
.apprv .dim{color:var(--ink-3)}
.apprv .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.9;fill:none;stroke-linecap:round;stroke-linejoin:round}

.apprv .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.apprv .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.apprv .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}

.apprv .tabrow{display:flex;gap:6px;flex-wrap:wrap;border-bottom:1px solid var(--border);padding-bottom:10px;margin-bottom:16px}
.apprv .tabp{padding:7px 13px;border-radius:99px;border:1px solid transparent;background:none;color:var(--ink-2);font-weight:600;font-size:12.5px}
.apprv .tabp:hover{background:var(--surface-2);color:var(--ink)}
.apprv .tabp.on{background:var(--accent-soft);color:var(--accent)}

.apprv .actrow{display:flex;align-items:center;justify-content:flex-end;gap:12px;margin-bottom:14px}
.apprv .note{font-size:12px;color:var(--ink-2);max-width:640px;margin:0}
.apprv .note strong{color:var(--ink)}

.apprv .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px}
.apprv .btn:hover{background:var(--surface-2)}
.apprv .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.apprv .btn.pri:hover{filter:brightness(1.06)}
.apprv .btn.danger{border-color:color-mix(in srgb,var(--crit) 45%,var(--border));color:var(--crit);background:var(--surface)}
.apprv .btn.danger:hover{background:var(--crit-soft)}
.apprv .btn.sm{padding:5px 10px;font-size:12px}
.apprv .btn[disabled]{opacity:.55;pointer-events:none}

.apprv .tablewrap{overflow-x:auto}
.apprv table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.apprv thead th{text-align:left;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);padding:10px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
.apprv tbody td{padding:10px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
.apprv tbody tr:last-child td{border-bottom:0}
.apprv tbody tr:hover td{background:var(--inset)}
.apprv .c-r{text-align:right}
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

.apprv .empty{border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:34px 26px;text-align:center;display:flex;flex-direction:column;align-items:center;gap:6px}
.apprv .empty.err{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit);display:block;padding:18px}
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
