"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  Field,
  Input,
  MessageBar,
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import type { SearchSimilarResponse, TrademarkType } from "@/lib/types";
import { SearchSimilarModal } from "./_search-similar-modal";

const STEPS = ["Basic info", "Goods & services", "Jurisdictions", "Review & submit"];
const JURISDICTIONS = [
  { code: "IN", label: "India" },
  { code: "US", label: "United States" },
  { code: "EU", label: "European Union" },
  { code: "WIPO", label: "WIPO (Madrid Protocol)" },
];

interface IntakeState {
  name: string;
  description: string;
  trademarkType: TrademarkType;
  niceClass: string;
  goodsServices: string;
  jurisdictions: string[];
  applicantName: string;
  renewalDueOn: string;
}

const INITIAL_STATE: IntakeState = {
  name: "",
  description: "",
  trademarkType: "word_mark",
  niceClass: "",
  goodsServices: "",
  jurisdictions: ["IN"],
  applicantName: "",
  renewalDueOn: "",
};

export default function TrademarkIntakePage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<IntakeState>(INITIAL_STATE);
  const [showSearch, setShowSearch] = useState(false);
  const [searchAck, setSearchAck] = useState<SearchSimilarResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function update<K extends keyof IntakeState>(key: K, value: IntakeState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function toggleJurisdiction(code: string) {
    setForm((f) => ({
      ...f,
      jurisdictions: f.jurisdictions.includes(code)
        ? f.jurisdictions.filter((j) => j !== code)
        : [...f.jurisdictions, code],
    }));
  }

  async function submit() {
    setSubmitting(true);
    try {
      const trademark = await trademarksApi.intake({
        name: form.name,
        description: form.description || null,
        trademark_type: form.trademarkType,
        nice_class: form.niceClass || null,
        goods_services: form.goodsServices || null,
        jurisdictions: form.jurisdictions.length ? form.jurisdictions : ["IN"],
        filing_context: form.applicantName ? { applicant_name: form.applicantName } : null,
        renewal_due_on: form.renewalDueOn ? new Date(form.renewalDueOn).toISOString() : null,
        search_query_id: searchAck?.query_id ?? null,
      });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      qc.invalidateQueries({ queryKey: ["trademarks", "dashboard"] });
      notify("Trademark intake submitted", "success");
      router.push(`/trademarks/${trademark.id}`);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Intake submission failed", "error");
    } finally {
      setSubmitting(false);
    }
  }

  const canAdvance = step === 0 ? form.name.trim().length > 0 : true;

  return (
    <div className="space-y-4">
      <PageHeader
        title="New trademark intake"
        description="Capture a new mark, check for conflicts, and file it into your portfolio."
      />

      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            <div
              className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-medium ${
                i === step
                  ? "bg-brand-600 text-white"
                  : i < step
                    ? "bg-success-subtle text-success"
                    : "bg-slate-50 text-slate-400"
              }`}
            >
              {i + 1}
            </div>
            <span className={`text-[13px] ${i === step ? "font-medium text-slate-900" : "text-slate-500"}`}>
              {label}
            </span>
            {i < STEPS.length - 1 && <div className="h-px w-8 bg-slate-200" />}
          </div>
        ))}
      </div>

      <Card>
        <CardBody className="space-y-4">
          {step === 0 && (
            <div className="space-y-4">
              <Field label="Trademark name" hint="The word mark or brand name being filed.">
                <Input
                  value={form.name}
                  onChange={(e) => update("name", e.target.value)}
                  placeholder="e.g. PHARMOZAC"
                />
              </Field>
              <Field label="Description">
                <Textarea
                  rows={3}
                  value={form.description}
                  onChange={(e) => update("description", e.target.value)}
                  placeholder="Brief description of the mark and its use"
                />
              </Field>
              <Field label="Mark type">
                <Select
                  value={form.trademarkType}
                  onChange={(e) => update("trademarkType", e.target.value as TrademarkType)}
                >
                  <option value="word_mark">Word mark</option>
                  <option value="device_mark">Device mark</option>
                  <option value="combination">Combination</option>
                  <option value="sound">Sound</option>
                  <option value="collective">Collective</option>
                </Select>
              </Field>
              <div>
                <Button
                  variant="outline"
                  disabled={!form.name.trim()}
                  onClick={() => setShowSearch(true)}
                >
                  <Search className="h-4 w-4" />
                  Search similar trademarks
                </Button>
                {searchAck && (
                  <Badge tone="green" className="ml-2">
                    Checked · {searchAck.summary.recommendation.replace(/_/g, " ")}
                  </Badge>
                )}
              </div>
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <Field label="Nice classification" hint="Comma-separated class numbers, e.g. 5, 10">
                <Input
                  value={form.niceClass}
                  onChange={(e) => update("niceClass", e.target.value)}
                  placeholder="5"
                />
              </Field>
              <Field label="Goods & services description">
                <Textarea
                  rows={5}
                  value={form.goodsServices}
                  onChange={(e) => update("goodsServices", e.target.value)}
                  placeholder="Describe the goods/services this mark will cover"
                />
              </Field>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <Field label="Filing jurisdictions">
                <div className="flex flex-wrap gap-3">
                  {JURISDICTIONS.map((j) => (
                    <label
                      key={j.code}
                      className="flex items-center gap-2 rounded-lg border border-slate-300 px-3 py-1.5 text-[13px]"
                    >
                      <input
                        type="checkbox"
                        checked={form.jurisdictions.includes(j.code)}
                        onChange={() => toggleJurisdiction(j.code)}
                      />
                      {j.label}
                    </label>
                  ))}
                </div>
              </Field>
              <Field label="Applicant name">
                <Input
                  value={form.applicantName}
                  onChange={(e) => update("applicantName", e.target.value)}
                  placeholder="Legal entity filing the application"
                />
              </Field>
              <Field
                label="Renewal due date"
                hint="Optional — when known, drives the renewal calendar and dashboard."
              >
                <Input
                  type="date"
                  value={form.renewalDueOn}
                  onChange={(e) => update("renewalDueOn", e.target.value)}
                />
              </Field>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              {!searchAck && (
                <MessageBar intent="warning">
                  You haven&apos;t run a similarity search for this mark yet. Consider going back to Step 1
                  before filing.
                </MessageBar>
              )}
              <dl className="grid grid-cols-2 gap-4 text-[13px]">
                <ReviewRow label="Name" value={form.name} />
                <ReviewRow label="Type" value={form.trademarkType.replace("_", " ")} />
                <ReviewRow label="Nice class" value={form.niceClass || "—"} />
                <ReviewRow label="Jurisdictions" value={form.jurisdictions.join(", ") || "—"} />
                <ReviewRow label="Applicant" value={form.applicantName || "—"} />
                <ReviewRow label="Renewal due" value={form.renewalDueOn || "—"} />
              </dl>
              {form.description && <ReviewRow label="Description" value={form.description} full />}
              {form.goodsServices && <ReviewRow label="Goods & services" value={form.goodsServices} full />}
            </div>
          )}
        </CardBody>
      </Card>

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Back
        </Button>
        {step < STEPS.length - 1 ? (
          <Button disabled={!canAdvance} onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>
            Next
          </Button>
        ) : (
          <Button loading={submitting} onClick={submit}>
            Submit intake
          </Button>
        )}
      </div>

      {showSearch && (
        <SearchSimilarModal
          request={{
            trademark_name: form.name,
            description: form.description,
            trademark_type: form.trademarkType,
            jurisdictions: form.jurisdictions.length ? form.jurisdictions : ["IN"],
          }}
          onClose={() => setShowSearch(false)}
          onAcknowledge={(response) => {
            setSearchAck(response);
            setShowSearch(false);
          }}
        />
      )}
    </div>
  );
}

function ReviewRow({ label, value, full }: { label: string; value: string; full?: boolean }) {
  return (
    <div className={full ? "col-span-2" : undefined}>
      <dt className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">{label}</dt>
      <dd className="mt-0.5 whitespace-pre-wrap text-slate-800">{value}</dd>
    </div>
  );
}
