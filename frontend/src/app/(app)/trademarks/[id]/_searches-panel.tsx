"use client";

import { Fragment, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Search } from "lucide-react";
import {
  Badge,
  Button,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Select,
  Table,
  TD,
  TH,
  THead,
  Textarea,
  TR,
} from "@/components/ui";
import { SearchSimilarModal } from "@/app/(app)/trademarks/intake/_search-similar-modal";
import { trademarksApi } from "@/lib/endpoints";
import { fmtDateTime, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { Trademark, TrademarkRiskLevel, TrademarkSearchRun } from "@/lib/types";

const RISK_TONE = { high: "red", medium: "amber", low: "green", context_only: "slate" } as const;
const RISK_OPTIONS: { value: TrademarkRiskLevel; label: string }[] = [
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

/** Saved similarity searches for the case: the preliminary run, detailed runs, the LTE's risk rating and the Excel export. */
export function SearchesPanel({ trademark }: { trademark: Trademark }) {
  const qc = useQueryClient();
  const [searching, setSearching] = useState(false);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const queryKey = ["trademark", trademark.id, "search-runs"];
  const { data, isLoading, error } = useQuery({
    queryKey,
    queryFn: () => trademarksApi.searchRuns(trademark.id),
  });

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] text-slate-500">
          Every search run for this mark is saved here. Rate the overall risk on a detailed run, then export it to Excel
          for the report.
        </p>
        <Button onClick={() => setSearching(true)}>
          <Search className="h-4 w-4" />
          Run detailed search
        </Button>
      </div>

      {isLoading ? (
        <CenterSpinner label="Loading searches…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState title="No searches saved" description="Run a detailed search to start the risk mapping." />
      ) : (
        <Table>
          <THead>
            <tr>
              <TH>Run on</TH>
              <TH>Type</TH>
              <TH>Matches (high / medium / low)</TH>
              <TH>Overall risk</TH>
              <TH />
            </tr>
          </THead>
          <tbody>
            {data.map((run) => (
              <Fragment key={run.id}>
                <TR>
                  <TD>{fmtDateTime(run.created_at)}</TD>
                  <TD>
                    <Badge tone={run.purpose === "detailed" ? "violet" : "slate"}>{titleCase(run.purpose ?? "Search")}</Badge>
                  </TD>
                  <TD>
                    {run.summary.high_risk_count} / {run.summary.medium_risk_count} / {run.summary.low_risk_count}
                  </TD>
                  <TD>
                    {run.risk_level ? <Badge tone={RISK_TONE[run.risk_level]}>{titleCase(run.risk_level)}</Badge> : "Not rated"}
                  </TD>
                  <TD className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => setOpenRun(openRun === run.id ? null : run.id)}>
                      {openRun === run.id ? "Close" : "Rate"}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => trademarksApi.exportSearchRun(run.id)}>
                      <Download className="h-3.5 w-3.5" />
                      Excel
                    </Button>
                  </TD>
                </TR>
                {openRun === run.id && (
                  <tr>
                    <td colSpan={5} className="bg-slate-50 px-4 py-3">
                      <RunRating run={run} onSaved={() => qc.invalidateQueries({ queryKey })} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}

      {searching && (
        <SearchSimilarModal
          request={{
            trademark_name: trademark.name,
            description: trademark.description ?? "",
            trademark_type: trademark.trademark_type,
            jurisdictions: trademark.jurisdictions ?? [trademark.jurisdiction],
            trademark_id: trademark.id,
            purpose: "detailed",
          }}
          onClose={() => {
            setSearching(false);
            qc.invalidateQueries({ queryKey });
          }}
          onAcknowledge={() => {
            setSearching(false);
            qc.invalidateQueries({ queryKey });
          }}
        />
      )}
    </div>
  );
}

function RunRating({ run, onSaved }: { run: TrademarkSearchRun; onSaved: () => void }) {
  const { notify } = useToast();
  const [risk, setRisk] = useState<TrademarkRiskLevel | "">(run.risk_level ?? "");
  const [notes, setNotes] = useState(run.notes ?? "");
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await trademarksApi.updateSearchRun(run.id, { risk_level: risk || null, notes: notes.trim() || null });
      notify("Risk rating saved", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Could not save the rating", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <Field label="Overall risk">
        <Select value={risk} onChange={(e) => setRisk(e.target.value as TrademarkRiskLevel | "")}>
          <option value="">Not rated</option>
          {RISK_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </Select>
      </Field>
      <Field label="Notes" className="sm:col-span-2">
        <Textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      <div>
        <Button loading={busy} onClick={save}>
          Save rating
        </Button>
      </div>
    </div>
  );
}
