"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button, Card, CardBody, CardHeader, CardTitle, Field, Input, MessageBar } from "@/components/ui";
import { trademarksApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import type { Trademark } from "@/lib/types";

const dateInput = (iso: string | null) => (iso ? iso.slice(0, 10) : "");

/** The filing record: application number, form, portal reference, docket number and filing date. */
export function FilingPanel({ trademark }: { trademark: Trademark }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [applicationNumber, setApplicationNumber] = useState(trademark.application_number ?? "");
  const [filingForm, setFilingForm] = useState(trademark.filing_form ?? "");
  const [portalReference, setPortalReference] = useState(trademark.portal_reference ?? "");
  const [docketNumber, setDocketNumber] = useState(trademark.docket_number ?? "");
  const [filedOn, setFiledOn] = useState(dateInput(trademark.filed_on));
  const [busy, setBusy] = useState(false);

  if (!trademark.case) {
    return <MessageBar intent="info">This trademark has no clearance case, so there is no filing to record.</MessageBar>;
  }

  async function save() {
    setBusy(true);
    try {
      await trademarksApi.recordFiling(trademark.id, {
        application_number: applicationNumber.trim() || null,
        filing_form: filingForm.trim() || null,
        portal_reference: portalReference.trim() || null,
        docket_number: docketNumber.trim() || null,
        filed_on: filedOn ? new Date(filedOn).toISOString() : null,
      });
      qc.invalidateQueries({ queryKey: ["trademark", trademark.id] });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      notify("Filing recorded", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not record the filing", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Filing &amp; docketing</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4 text-[13px]">
        <MessageBar intent="info">
          Recording an application number or filing date marks the trademark as Filed and sets its renewal date. Only
          the person the workflow step is assigned to (or an administrator) can record it. Attach filing documents on
          the Documents tab.
        </MessageBar>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field label="Application number">
            <Input value={applicationNumber} onChange={(e) => setApplicationNumber(e.target.value)} />
          </Field>
          <Field label="Filing form" hint="e.g. TM-A">
            <Input value={filingForm} onChange={(e) => setFilingForm(e.target.value)} />
          </Field>
          <Field label="Portal reference">
            <Input value={portalReference} onChange={(e) => setPortalReference(e.target.value)} />
          </Field>
          <Field label="Docket number">
            <Input value={docketNumber} onChange={(e) => setDocketNumber(e.target.value)} />
          </Field>
          <Field label="Filing date">
            <Input type="date" value={filedOn} onChange={(e) => setFiledOn(e.target.value)} />
          </Field>
        </div>
        <Button loading={busy} onClick={save}>
          Save filing
        </Button>
      </CardBody>
    </Card>
  );
}
