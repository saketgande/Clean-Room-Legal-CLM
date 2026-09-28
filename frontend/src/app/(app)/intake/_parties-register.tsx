"use client";

/**
 * Operations → Entities & counterparties: the party register the intake wizard
 * looks up. Admins maintain our legal entities here; counterparties are mostly
 * created from the wizard and corrected or retired here.
 */

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PenLine, Plus } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CenterSpinner, EmptyState, ErrorState, Field, Input, Modal,
  Table, TD, TH, THead, TR, Textarea,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import { partiesApi } from "@/lib/endpoints";
import type { Counterparty, LegalEntity } from "@/lib/types";

type Kind = "entity" | "counterparty";
type Row = LegalEntity | Counterparty;

const FIELDS: Record<Kind, { key: string; label: string; long?: boolean; type?: string }[]> = {
  entity: [
    { key: "name", label: "Registered legal name" },
    { key: "jurisdiction", label: "Jurisdiction" },
    { key: "authorised_signatory", label: "Authorised signatory" },
    { key: "registered_address", label: "Registered address", long: true },
  ],
  counterparty: [
    { key: "name", label: "Legal name" },
    { key: "jurisdiction", label: "Jurisdiction" },
    { key: "contact_email", label: "Contact email", type: "email" },
    { key: "address", label: "Address", long: true },
  ],
};

export function PartiesRegisterTab() {
  return (
    <div className="space-y-8">
      <Register kind="entity" title="Our legal entities"
        hint="The companies on our side of an agreement. Requesters must pick one of these on step 1 of a new agreement." />
      <Register kind="counterparty" title="Counterparties"
        hint="The other side. Requesters find them in the wizard's look-up, or add a new one there." />
    </div>
  );
}

function Register({ kind, title, hint }: { kind: Kind; title: string; hint: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Row | "new" | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["party-register", kind, q],
    queryFn: () => (kind === "entity" ? partiesApi.entities(q, true) : partiesApi.counterparties(q, true, 200)) as Promise<Row[]>,
  });

  async function setActive(row: Row, active: boolean) {
    try {
      if (kind === "entity") await partiesApi.updateEntity(row.id, { active });
      else await partiesApi.updateCounterparty(row.id, { active });
      qc.invalidateQueries({ queryKey: ["party-register"] });
      qc.invalidateQueries({ queryKey: ["party-lookup"] });
      notify(active ? `${row.name} is available again` : `${row.name} retired — it no longer appears in look-ups`, "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Update failed", "error"); }
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <p className="text-xs text-slate-500">{hint}</p>
        </div>
        <div className="flex items-center gap-2">
          <Input aria-label={`Search ${title}`} placeholder="Search by name" value={q} onChange={(e) => setQ(e.target.value)} className="w-56" />
          <Button onClick={() => setEditing("new")}><Plus className="h-4 w-4" />{kind === "entity" ? "New entity" : "New counterparty"}</Button>
        </div>
      </div>
      <Card>
        {isLoading ? <CardBody><CenterSpinner /></CardBody>
          : error ? <CardBody><ErrorState error={error} /></CardBody>
          : (data ?? []).length === 0 ? (
            <CardBody><EmptyState title={q ? "Nothing matches" : kind === "entity" ? "No legal entities yet" : "No counterparties yet"}
              description={kind === "entity" ? "Add your company's legal entities so requesters can pick them." : "Counterparties appear here as requesters add them."} /></CardBody>
          ) : (
            <Table>
              <THead><TR>
                <TH>Name</TH><TH>Jurisdiction</TH><TH>{kind === "entity" ? "Authorised signatory" : "Contact email"}</TH><TH>Status</TH><TH></TH>
              </TR></THead>
              <tbody>{(data ?? []).map((row) => (
                <TR key={row.id}>
                  <TD className="font-medium">{row.name}</TD>
                  <TD className="text-slate-500">{row.jurisdiction ?? "—"}</TD>
                  <TD className="text-slate-500">{(kind === "entity" ? (row as LegalEntity).authorised_signatory : (row as Counterparty).contact_email) ?? "—"}</TD>
                  <TD>{row.active ? <Badge tone="green">Active</Badge> : <Badge tone="slate">Retired</Badge>}</TD>
                  <TD className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(row)}><PenLine className="h-3.5 w-3.5" />Edit</Button>
                    <Button variant="ghost" size="sm" onClick={() => setActive(row, !row.active)}>{row.active ? "Retire" : "Restore"}</Button>
                  </TD>
                </TR>
              ))}</tbody>
            </Table>
          )}
      </Card>
      {editing && (
        <RecordModal kind={kind} existing={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { qc.invalidateQueries({ queryKey: ["party-register"] }); qc.invalidateQueries({ queryKey: ["party-lookup"] }); setEditing(null); }} />
      )}
    </section>
  );
}

function RecordModal({ kind, existing, onClose, onSaved }: { kind: Kind; existing: Row | null; onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(FIELDS[kind].map((f) => [f.key, String((existing as unknown as Record<string, unknown>)?.[f.key] ?? "")]))
  );
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const payload = Object.fromEntries(FIELDS[kind].map((f) => [f.key, values[f.key].trim() || null]));
      if (kind === "entity") existing ? await partiesApi.updateEntity(existing.id, payload) : await partiesApi.createEntity(payload);
      else existing ? await partiesApi.updateCounterparty(existing.id, payload) : await partiesApi.createCounterparty(payload);
      notify(existing ? "Saved" : "Added to the register", "success");
      onSaved();
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed", "error"); }
    finally { setBusy(false); }
  }

  const noun = kind === "entity" ? "legal entity" : "counterparty";
  return (
    <Modal open onClose={onClose} title={existing ? `Edit ${existing.name}` : `New ${noun}`} size="lg">
      <div className="space-y-3">
        {FIELDS[kind].map((f) => (
          <Field key={f.key} label={f.label}>
            {f.long
              ? <Textarea rows={2} value={values[f.key]} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
              : <Input type={f.type ?? "text"} value={values[f.key]} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />}
          </Field>
        ))}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!values.name.trim()}>{existing ? "Save changes" : `Add ${noun}`}</Button>
        </div>
      </div>
    </Modal>
  );
}
