"use client";

/* Notice detail — a full page, not a dialog.
   A worked notice carries a lot at once: the particulars, a drafted reply that
   is a whole letter, the attachments it was drafted from, and the timeline.
   Two columns keep the reply readable at full width while the evidence it came
   from stays beside it. */

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, Sparkles, Trash2, Upload } from "lucide-react";
import { noticesApi } from "@/lib/endpoints";
import {
  Badge,
  Breadcrumbs,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  ErrorState,
  Field,
  Input,
  Textarea,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import {
  NOTICE_TYPES,
  POSTURE_TONE,
  REMINDER_LABEL,
  STATUS_TONE,
  deadlineLabel,
  fmtDate,
  typeLabel,
} from "../shared";

export default function NoticeDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();

  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [escalating, setEscalating] = useState(false);
  const [escalateReason, setEscalateReason] = useState("");
  const [note, setNote] = useState("");
  const [responseSummary, setResponseSummary] = useState("");
  const [draft, setDraft] = useState("");

  const { data: n, isLoading, error } = useQuery({
    queryKey: ["notice", id],
    queryFn: () => noticesApi.get(id),
  });

  // Seed the editable draft once, and only while the box is untouched —
  // re-syncing on every refetch would throw away the lawyer's edits.
  useEffect(() => {
    if (!draft && n?.draft_response) setDraft(n.draft_response);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [n?.draft_response]);

  const reload = () => qc.invalidateQueries({ queryKey: ["notice", id] });
  const invalidateList = () => {
    qc.invalidateQueries({ queryKey: ["notices"] });
    qc.invalidateQueries({ queryKey: ["notices-summary"] });
  };

  if (isLoading) return <CenterSpinner label="Loading the notice…" />;
  if (error) return <ErrorState error={error} />;
  if (!n) return null;

  async function transition(status: string) {
    setBusy(true);
    try {
      await noticesApi.setStatus(id, {
        status,
        ...(status === "responded" && responseSummary.trim()
          ? { response_summary: responseSummary.trim() }
          : {}),
      });
      notify(`Notice marked ${status}`, "success");
      setResponseSummary("");
      reload();
      invalidateList();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not update the notice", "error");
    } finally {
      setBusy(false);
    }
  }

  async function makeDraft() {
    setDrafting(true);
    try {
      const updated = await noticesApi.draftResponse(id);
      setDraft(updated.draft_response ?? "");
      reload();
      notify(
        updated.draft_generated === false
          ? "AI unavailable — inserted a holding-reply template"
          : "Draft ready — review it before sending",
        updated.draft_generated === false ? "info" : "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not draft a reply", "error");
    } finally {
      setDrafting(false);
    }
  }

  async function doEscalate() {
    if (!escalateReason.trim()) return;
    setBusy(true);
    try {
      const updated = await noticesApi.escalate(id, { reason: escalateReason.trim() });
      notify(`Escalated — opened ${updated.escalated_intake_ref}`, "success");
      setEscalating(false);
      setEscalateReason("");
      reload();
      invalidateList();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not escalate", "error");
    } finally {
      setBusy(false);
    }
  }

  async function uploadDoc(file: File | null) {
    if (!file) return;
    setUploading(true);
    try {
      await noticesApi.addDocument(id, file);
      reload();
      notify("Document attached", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not attach that file", "error");
    } finally {
      setUploading(false);
    }
  }

  async function removeDoc(documentId: string) {
    try {
      await noticesApi.deleteDocument(id, documentId);
      reload();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not remove that file", "error");
    }
  }

  async function saveNote() {
    if (!note.trim()) return;
    setBusy(true);
    try {
      await noticesApi.addNote(id, note.trim());
      setNote("");
      reload();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not add the note", "error");
    } finally {
      setBusy(false);
    }
  }

  const live = n.status !== "responded" && n.status !== "closed";

  return (
    <div className="space-y-6">
      <div>
        <Breadcrumbs items={[{ label: "Notices", href: "/notices" }, { label: n.ref }]} />
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
              {n.subject}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={POSTURE_TONE[n.deadline_posture]}>{deadlineLabel(n)}</Badge>
              <Badge tone={STATUS_TONE[n.status]}>{n.status}</Badge>
              <Badge tone="slate">{n.direction}</Badge>
              <Badge tone="violet">{typeLabel(n.notice_type)}</Badge>
              <span className="text-sm text-slate-500">{n.ref}</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {n.status !== "responded" && (
              <Button onClick={() => transition("responded")} loading={busy}>Mark responded</Button>
            )}
            {!n.escalated_intake_request_id && (
              <Button variant="outline" onClick={() => setEscalating(true)}>Escalate</Button>
            )}
            {n.status !== "closed" ? (
              <Button variant="outline" onClick={() => transition("closed")} loading={busy}>Close</Button>
            ) : (
              <Button variant="outline" onClick={() => transition("open")} loading={busy}>Re-open</Button>
            )}
          </div>
        </div>
      </div>

      {n.escalated_intake_ref && (
        <div className="rounded-lg border border-danger/30 bg-danger-subtle/30 px-4 py-3 text-sm">
          Escalated to{" "}
          <Link href="/intake" className="font-medium text-brand-600 hover:underline">
            {n.escalated_intake_ref}
          </Link>{" "}
          — routing, SLA and approvals are tracked on that ticket.
        </div>
      )}

      {escalating && (
        <Card>
          <CardBody className="space-y-3">
            <Field
              label="Why are you escalating?"
              hint="opens a linked intake ticket, which carries routing, SLA and approvals"
            >
              <Textarea
                rows={2} value={escalateReason}
                onChange={(e) => setEscalateReason(e.target.value)}
                placeholder="Counterparty threatening arbitration; needs senior counsel."
              />
            </Field>
            <div className="flex gap-2">
              <Button onClick={doEscalate} loading={busy} disabled={!escalateReason.trim()}>
                Escalate to legal queue
              </Button>
              <Button variant="ghost" onClick={() => setEscalating(false)}>Cancel</Button>
            </div>
          </CardBody>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* ---- main column ---- */}
        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle>Particulars</CardTitle></CardHeader>
            <CardBody>
              <dl className="grid gap-x-8 gap-y-4 text-sm sm:grid-cols-2">
                <Detail label="Counterparty" value={n.counterparty_name} />
                <Detail label="Their reference" value={n.counterparty_ref || "—"} />
                <Detail label="Date on notice" value={fmtDate(n.notice_date)} />
                <Detail label="Received" value={fmtDate(n.received_at)} />
                <Detail label="Response due" value={fmtDate(n.response_due_date)} />
                <Detail label="Owner" value={n.owner_name || "Unassigned"} />
                <Detail
                  label="Owner last chased"
                  value={
                    n.last_reminder_stage
                      ? `${REMINDER_LABEL[n.last_reminder_stage]}${
                          n.last_reminder_at
                            ? ` · ${new Date(n.last_reminder_at).toLocaleDateString()}`
                            : ""
                        }`
                      : !n.owner_user_id && n.response_due_date
                        ? "Nobody assigned to chase"
                        : "Not yet"
                  }
                  tone={!n.owner_user_id && n.response_due_date && !n.last_reminder_stage ? "warn" : undefined}
                />
                {n.contract_id && (
                  <Detail
                    label="Linked contract"
                    value={
                      <Link href={`/contracts/${n.contract_id}`} className="text-brand-600 hover:underline">
                        {n.contract_title || "Open contract"}
                      </Link>
                    }
                  />
                )}
              </dl>
              {n.description && (
                <div className="mt-5 border-t border-slate-200 pt-4">
                  <p className="text-xs uppercase tracking-wide text-slate-400">Summary</p>
                  <p className="mt-1 whitespace-pre-wrap text-sm leading-relaxed">{n.description}</p>
                </div>
              )}
            </CardBody>
          </Card>

          {n.response_summary && (
            <Card>
              <CardHeader><CardTitle>Our response</CardTitle></CardHeader>
              <CardBody>
                <p className="whitespace-pre-wrap text-sm leading-relaxed">{n.response_summary}</p>
              </CardBody>
            </Card>
          )}

          {live && (
            <Card>
              <CardHeader className="flex items-center justify-between">
                <CardTitle>Drafted reply</CardTitle>
                <Button variant="outline" size="sm" onClick={makeDraft} loading={drafting}>
                  <Sparkles className="h-3.5 w-3.5" />
                  {n.draft_response ? "Redraft" : "Draft a reply"}
                </Button>
              </CardHeader>
              <CardBody>
                {n.draft_response ? (
                  <>
                    <p className="text-xs text-warning">
                      A draft for you to review and edit — nothing is sent from here.
                    </p>
                    {/* The reply is a full letter; give it real height rather than
                        a scroll-in-a-scroll. */}
                    <Textarea
                      rows={26}
                      className="mt-3 font-mono text-xs leading-relaxed"
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                    />
                    <Button
                      variant="outline" size="sm" className="mt-3"
                      onClick={() => setResponseSummary(draft)}
                      disabled={!draft.trim()}
                    >
                      Use as the response record
                    </Button>
                  </>
                ) : (
                  <p className="text-sm text-slate-500">
                    Generate a first-draft reply from this notice and its attachments.
                  </p>
                )}
              </CardBody>
            </Card>
          )}

          {live && (
            <Card>
              <CardHeader><CardTitle>Response record</CardTitle></CardHeader>
              <CardBody>
                <Field label="" hint="recorded against the notice when you mark it responded">
                  <Textarea
                    rows={3} value={responseSummary}
                    onChange={(e) => setResponseSummary(e.target.value)}
                    placeholder="What you sent, and when."
                  />
                </Field>
              </CardBody>
            </Card>
          )}
        </div>

        {/* ---- sidebar: the evidence ---- */}
        <div className="space-y-6">
          <Card>
            <CardHeader className="flex items-center justify-between">
              <CardTitle>Documents</CardTitle>
              <label className="cursor-pointer text-xs text-brand-600 hover:underline">
                {uploading ? "Uploading…" : "Attach"}
                <input
                  type="file" className="hidden"
                  accept=".pdf,.docx,.doc,.txt,application/pdf,text/plain"
                  onChange={(e) => uploadDoc(e.target.files?.[0] ?? null)}
                />
              </label>
            </CardHeader>
            <CardBody>
              {!(n.documents ?? []).length ? (
                <p className="text-sm text-slate-400">Nothing attached.</p>
              ) : (
                <ul className="space-y-2">
                  {(n.documents ?? []).map((d) => (
                    <li key={d.id} className="flex items-center gap-2 text-sm">
                      <FileText className="h-3.5 w-3.5 shrink-0 text-slate-400" />
                      <span className="min-w-0 flex-1 truncate">{d.filename}</span>
                      <span className="shrink-0 text-xs tabular-nums text-slate-400">
                        {Math.max(1, Math.round(d.size_bytes / 1024))} KB
                        {!d.has_text && " · no text"}
                      </span>
                      <button
                        type="button" title="Remove"
                        className="shrink-0 text-slate-400 hover:text-danger"
                        onClick={() => removeDoc(d.id)}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>

          <Card>
            <CardHeader><CardTitle>Timeline</CardTitle></CardHeader>
            <CardBody>
              <ol className="space-y-3">
                {(n.events ?? []).map((e) => (
                  <li key={e.id} className="flex gap-3 text-sm">
                    <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-brand-500" />
                    <div className="min-w-0">
                      <span className="font-medium">{e.kind.replace(/_/g, " ")}</span>
                      {e.body && <span className="text-slate-500"> — {e.body}</span>}
                      <div className="text-xs text-slate-400">
                        {e.actor_name ? `${e.actor_name} · ` : ""}
                        {new Date(e.created_at).toLocaleString()}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
              <div className="mt-4 flex gap-2">
                <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note…" />
                <Button variant="outline" onClick={saveNote} loading={busy} disabled={!note.trim()}>
                  Add
                </Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Detail({
  label, value, tone,
}: { label: string; value: React.ReactNode; tone?: "warn" }) {
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-400">{label}</dt>
      <dd className={`mt-0.5 ${tone === "warn" ? "text-warning" : ""}`}>{value}</dd>
    </div>
  );
}
