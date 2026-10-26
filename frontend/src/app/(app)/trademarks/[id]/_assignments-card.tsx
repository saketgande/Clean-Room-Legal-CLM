"use client";

import Link from "next/link";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui";
import type { Trademark } from "@/lib/types";

function Row({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">{label}</p>
      <p className="mt-0.5 text-slate-800">{value || "—"}</p>
    </div>
  );
}

/** The requisition (who asked, for which business unit) and where the case lives in Legal Intake. */
export function AssignmentsCard({ trademark }: { trademark: Trademark }) {
  const context = trademark.filing_context ?? {};
  const text = (key: string) => (typeof context[key] === "string" ? (context[key] as string) : null);
  const trademarkCase = trademark.case;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Requisition</CardTitle>
      </CardHeader>
      <CardBody className="space-y-3 text-[13px]">
        <Row label="Business unit" value={text("business_unit")} />
        <Row label="Business Unit SPOC" value={text("business_unit_spoc")} />
        <Row label="Applicant" value={text("applicant_name")} />
        {trademarkCase && (
          <>
            <Row label="Current step owner" value={trademarkCase.assignee_name} />
            <div>
              <p className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">Legal Intake request</p>
              <Link
                href={`/intake?open=${trademarkCase.request_id}`}
                className="mt-0.5 inline-block text-brand-700 hover:underline"
              >
                {trademarkCase.request_ref ?? "Open request"}
              </Link>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}
