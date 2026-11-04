"use client";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui";
import { fmtDate, fmtDateTime, titleCase } from "@/lib/utils";
import type { Trademark } from "@/lib/types";

function Field({ label, value, wide = false }: { label: string; value: string | null | undefined; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <p className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">{label}</p>
      <p className="mt-0.5 whitespace-pre-wrap text-slate-800">{value || "—"}</p>
    </div>
  );
}

/**
 * Everything about the mark and its requisition in one wide card, laid out across the page instead of down
 * it: what the mark covers, where and when it is filed, who asked, and the record's dates.
 */
export function OverviewCard({ trademark }: { trademark: Trademark }) {
  const context = trademark.filing_context ?? {};
  const text = (key: string) => (typeof context[key] === "string" ? (context[key] as string) : null);
  const trademarkCase = trademark.case;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Overview</CardTitle>
      </CardHeader>
      <CardBody className="text-[13px]">
        <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Description" value={trademark.description} wide />
          <Field label="Goods & services" value={trademark.goods_services} wide />

          <Field label="Nice class" value={trademark.nice_class} />
          <Field label="Jurisdictions" value={(trademark.jurisdictions ?? [trademark.jurisdiction]).join(", ")} />
          <Field label="Filed on" value={trademark.filed_on ? fmtDate(trademark.filed_on) : null} />
          <Field label="Renewal due" value={trademark.renewal_due_on ? fmtDate(trademark.renewal_due_on) : null} />
          <Field
            label="Next date"
            value={trademark.next_action_on ? `${fmtDate(trademark.next_action_on)}${trademark.next_action_note ? ` · ${trademark.next_action_note}` : ""}` : null}
          />

          <Field label="Business unit" value={text("business_unit")} />
          <Field label="Business Unit SPOC" value={text("business_unit_spoc")} />
          <Field label="Applicant" value={text("applicant_name")} />
          <Field label="Type" value={titleCase(trademark.trademark_type)} />

          {trademarkCase && <Field label="Legal Intake request" value={trademarkCase.request_ref} />}
          {trademarkCase && <Field label="Current step owner" value={trademarkCase.assignee_name} />}
          <Field label="Created" value={fmtDateTime(trademark.created_at)} />
          <Field label="Updated" value={fmtDateTime(trademark.updated_at)} />
        </div>
      </CardBody>
    </Card>
  );
}
