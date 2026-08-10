"use client";

import { use, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { trademarksApi } from "@/lib/endpoints";
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
  Select,
} from "@/components/ui";
import { fmtDate, fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { TrademarkStatus } from "@/lib/types";

const STATUSES: TrademarkStatus[] = [
  "draft",
  "filed",
  "registered",
  "opposed",
  "abandoned",
  "renewed",
];

export default function TrademarkDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const { data: trademark, isLoading, error } = useQuery({
    queryKey: ["trademark", id],
    queryFn: () => trademarksApi.get(id),
  });

  async function changeStatus(status: TrademarkStatus) {
    setBusy(true);
    try {
      await trademarksApi.update(id, { status });
      qc.invalidateQueries({ queryKey: ["trademark", id] });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      notify("Status updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading trademark…" />;
  if (error) return <ErrorState error={error} />;
  if (!trademark) return null;

  return (
    <div className="space-y-4">
      <Breadcrumbs
        items={[
          { label: "Trademarks", href: "/trademarks" },
          { label: trademark.name },
        ]}
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-[20px] font-semibold leading-tight tracking-[-0.01em] text-slate-900">
            {trademark.name}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(trademark.status)}>
              {titleCase(trademark.status)}
            </Badge>
            <Badge tone="slate">{titleCase(trademark.trademark_type)}</Badge>
            <Badge tone="slate">{titleCase(trademark.source)}</Badge>
          </div>
        </div>
        <Field label="Status" className="w-48">
          <Select
            value={trademark.status}
            disabled={busy}
            onChange={(e) => changeStatus(e.target.value as TrademarkStatus)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Details</CardTitle>
          </CardHeader>
          <CardBody className="space-y-4 text-[13px]">
            {trademark.description && (
              <div>
                <p className="mb-1 text-xs font-medium uppercase tracking-[0.04em] text-slate-500">
                  Description
                </p>
                <p className="whitespace-pre-wrap text-slate-700">
                  {trademark.description}
                </p>
              </div>
            )}
            {trademark.goods_services && (
              <div>
                <p className="mb-1 text-xs font-medium uppercase tracking-[0.04em] text-slate-500">
                  Goods &amp; services
                </p>
                <p className="whitespace-pre-wrap text-slate-700">
                  {trademark.goods_services}
                </p>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4">
              <DetailRow label="Nice class" value={trademark.nice_class ?? "—"} />
              <DetailRow
                label="Jurisdictions"
                value={(trademark.jurisdictions ?? [trademark.jurisdiction]).join(", ")}
              />
              <DetailRow label="Workflow state" value={titleCase(trademark.workflow_state)} />
              <DetailRow label="Filed on" value={fmtDate(trademark.filed_on)} />
              <DetailRow label="Renewal due" value={fmtDate(trademark.renewal_due_on)} />
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Record info</CardTitle>
          </CardHeader>
          <CardBody className="space-y-3 text-[13px]">
            <DetailRow label="Created" value={fmtDateTime(trademark.created_at)} />
            <DetailRow label="Updated" value={fmtDateTime(trademark.updated_at)} />
            {trademark.source_document_extract_id && (
              <DetailRow
                label="Source extract"
                value={trademark.source_document_extract_id.slice(0, 8)}
              />
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">
        {label}
      </p>
      <p className="mt-0.5 text-slate-800">{value}</p>
    </div>
  );
}
