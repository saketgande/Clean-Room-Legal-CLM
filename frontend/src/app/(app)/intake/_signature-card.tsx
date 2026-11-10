"use client";

// "Send for signature" on the request page. Shown while the workflow waits at its signature step: before
// the envelope goes out it is the form (parties pre-filled, editable); after, the signers' status.
// The server stays the authority — approved text, no blanks, signers must be parties or users — so a
// refusal is shown as it comes back. Rendered inside `.ro`.

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { contractsApi, signaturesApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import type { LifecycleRow, SignerOption } from "@/lib/types";

export interface SignerRow { name: string; email: string; role: string }

const CSS = `
.ro .sgc .srow{display:grid;grid-template-columns:1.2fr 1.6fr 1fr auto;gap:8px;margin-bottom:8px}
.ro .sgc input{min-width:0;padding:7px 10px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .sgc .note{font-size:12px;color:var(--ink-3);margin:2px 0 10px}
.ro .sgc .err{font-size:12.5px;color:var(--crit);margin-bottom:8px}
.ro .sgc .who{display:flex;justify-content:space-between;gap:10px;padding:6px 0;border-top:1px solid var(--border);font-size:12.5px}
.ro .sgc .who .st{color:var(--ink-3);font-weight:600;white-space:nowrap}
.ro .sgc .who .st.done{color:var(--good)}
`;

// Pre-fill: every contract party with an email (they are the other side's signers). Users are offered in the
// email box's list but never added unasked — who signs for us is the sender's call.
export function startingRows(options: SignerOption[]): SignerRow[] {
  const parties = options.filter((o) => o.kind === "party" && o.email).map((o) => ({ name: o.name, email: o.email, role: "Counterparty" }));
  return parties.length ? parties : [{ name: "", email: "", role: "" }];
}

export const signersReady = (rows: SignerRow[]) => rows.length > 0 && rows.every((r) => r.name.trim() && r.email.trim());

export function SignatureCard({ contractId, rows, stepName }: { contractId: string; rows: LifecycleRow[]; stepName?: string }) {
  const { notify } = useToast();
  const qc = useQueryClient();
  // The sent envelope shows as "Sent for signature" (+ one row per signer) in the signature stage's rows.
  const sent = rows.some((r) => r.name === "Sent for signature");
  const { data: options } = useQuery({ queryKey: ["contract-signers", contractId], queryFn: () => contractsApi.signers(contractId), enabled: !sent });
  const [draft, setDraft] = useState<SignerRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const list = draft ?? (options ? startingRows(options) : []);
  const edit = (i: number, patch: Partial<SignerRow>) => setDraft(list.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const send = useMutation({
    mutationFn: () => signaturesApi.send({
      contract_id: contractId,
      recipients: list.map((r) => ({ name: r.name.trim(), email: r.email.trim(), role: r.role.trim() || null })),
    }),
    onSuccess: () => {
      notify("Sent for signature", "success"); setError(null);
      for (const k of [["lifecycle-rows"], ["flow-run"], ["intake-request"], ["contract-signers", contractId]]) qc.invalidateQueries({ queryKey: k });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Send failed"),
  });

  return (
    <div className="card sgc"><style>{CSS}</style>
      <div className="ch"><div className="hd"><h2>{stepName || "Signature"}</h2></div><span className="cnt">{sent ? "Out for signature" : "Ready to send"}</span></div>
      <div className="cb">
        {sent ? (
          rows.filter((r) => r.name !== "Sent for signature").map((r) => (
            <div className="who" key={r.name + r.detail}><span>{r.who}<span className="note"> · {r.detail}</span></span>
              <span className={`st ${r.status === "done" ? "done" : ""}`}>{r.status === "done" ? "Signed" : r.status === "skipped" ? "Declined" : "Waiting"}</span></div>
          ))
        ) : (
          <>
            <div className="note">Signers get the approved version by email. Edit, remove or add before sending.</div>
            <datalist id="sgc-emails">{(options ?? []).map((o) => <option key={o.email} value={o.email}>{o.name}</option>)}</datalist>
            {list.map((r, i) => (
              <div className="srow" key={i}>
                <input aria-label={`Signer ${i + 1} name`} placeholder="Name" value={r.name} onChange={(e) => edit(i, { name: e.target.value })} />
                <input aria-label={`Signer ${i + 1} email`} list="sgc-emails" type="email" placeholder="Email" value={r.email}
                  onChange={(e) => { const o = (options ?? []).find((x) => x.email === e.target.value); edit(i, { email: e.target.value, ...(o && !r.name.trim() ? { name: o.name } : {}) }); }} />
                <input aria-label={`Signer ${i + 1} role`} placeholder="Role (optional)" value={r.role} onChange={(e) => edit(i, { role: e.target.value })} />
                <button type="button" className="btn" aria-label={`Remove signer ${i + 1}`} disabled={list.length === 1} onClick={() => setDraft(list.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button type="button" className="btn" onClick={() => setDraft([...list, { name: "", email: "", role: "" }])}>+ Add signer</button>
            {error ? <div className="err" role="alert">{error}</div> : null}
            <div className="db" style={{ marginTop: 12 }}>
              <button type="button" className="btn pri wide" disabled={send.isPending || !signersReady(list)} onClick={() => send.mutate()}>Send for signature →</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
