"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Search, Sparkles } from "lucide-react";
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
import type { ClassSuggestionResponse, SearchSimilarResponse, TrademarkType } from "@/lib/types";
import { SearchSimilarModal } from "./_search-similar-modal";

// Matches the source module's intake page (app/intake/page.tsx): suggest
// while typing the description, not on a manual button click.
const SUGGEST_DEBOUNCE_MS = 900;
const SUGGEST_MIN_CHARS = 12;

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
  // The source module's "Minimum match to show" slider (sliderrow/sliderval
  // in its intake page), 0-100 step 5 — forwarded to the search as
  // min_match_score (0-1) to drop low-scoring results before display.
  const [minMatchPercent, setMinMatchPercent] = useState(0);
  const [niceClassManuallySet, setNiceClassManuallySet] = useState(false);
  const [classSuggestion, setClassSuggestion] = useState<ClassSuggestionResponse | null>(null);
  const [suggestLoading, setSuggestLoading] = useState(false);
  const [suggestError, setSuggestError] = useState<string | null>(null);
  const suggestRequestId = useRef(0);

  function update<K extends keyof IntakeState>(key: K, value: IntakeState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  // Debounced NICE-class suggestion as the user types the description —
  // same trigger, threshold and race-guard as the source module's intake
  // page (SUGGEST_DEBOUNCE_MS / SUGGEST_MIN_CHARS / suggestRequestId).
  useEffect(() => {
    const trimmed = form.description.trim();
    if (trimmed.length < SUGGEST_MIN_CHARS) {
      setClassSuggestion(null);
      setSuggestError(null);
      setSuggestLoading(false);
      return;
    }
    setSuggestLoading(true);
    const timer = setTimeout(() => {
      const requestId = ++suggestRequestId.current;
      trademarksApi
        .classifyGoods({ name: form.name, description: trimmed })
        .then((res) => {
          if (requestId !== suggestRequestId.current) return; // a newer keystroke superseded this request
          setClassSuggestion(res);
          setSuggestError(null);
          if (!niceClassManuallySet) update("niceClass", res.nice_class);
        })
        .catch((err) => {
          if (requestId !== suggestRequestId.current) return;
          setSuggestError(err instanceof Error ? err.message : "Could not get a class suggestion");
          setClassSuggestion(null);
        })
        .finally(() => {
          if (requestId === suggestRequestId.current) setSuggestLoading(false);
        });
    }, SUGGEST_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.description]);

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
      router.push(`/trademarks?created=${encodeURIComponent(trademark.id)}`);
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
        title="New Trademark intake"
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
              <Field label="Minimum match to show">
                <div className="flex items-center gap-3">
                  <input
                    type="range"
                    min={0}
                    max={100}
                    step={5}
                    value={minMatchPercent}
                    onChange={(e) => setMinMatchPercent(Number(e.target.value))}
                    className="h-1.5 w-full max-w-xs cursor-pointer appearance-none rounded-full bg-slate-200 accent-brand-600"
                  />
                  <span className="w-10 text-[13px] font-medium text-slate-700">{minMatchPercent}%</span>
                </div>
                <p className="mt-1 text-[11.5px] text-slate-400">
                  {minMatchPercent === 0
                    ? "Shows every result from Signa, TM Search, and your internal portfolio, regardless of match strength."
                    : `Only shows results from Signa, TM Search, and your internal portfolio scoring ${minMatchPercent}% or higher. Web search results aren't filtered — they don't carry a comparable match score.`}
                </p>
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
                <div className="relative">
                  <Input
                    value={form.niceClass}
                    onChange={(e) => {
                      setNiceClassManuallySet(true);
                      update("niceClass", e.target.value);
                    }}
                    placeholder="5"
                  />
                  {suggestLoading && (
                    <Sparkles className="absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 animate-pulse text-slate-400" />
                  )}
                </div>
                {suggestError ? (
                  <p className="mt-1.5 text-[12.5px] text-danger">{suggestError}</p>
                ) : classSuggestion && (
                  <p className="mt-1.5 text-[12.5px] text-slate-500">
                    <span className="font-medium text-slate-700">{classSuggestion.heading}</span> — {classSuggestion.reasoning}
                    {niceClassManuallySet && classSuggestion.nice_class !== form.niceClass && (
                      <> · <button type="button" className="text-brand-700 hover:underline" onClick={() => { setNiceClassManuallySet(false); update("niceClass", classSuggestion.nice_class); }}>use suggested class {classSuggestion.nice_class}</button></>
                    )}
                  </p>
                )}
                <p className="mt-1 text-[11.5px] text-slate-400">
                  Suggested automatically from the description on the previous step as you type.
                </p>
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
            min_match_score: minMatchPercent / 100,
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
