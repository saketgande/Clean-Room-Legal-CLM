"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, RefreshCw } from "lucide-react";
import { contractsApi, renewalsApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Modal,
  PageHeader,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
  Textarea,
} from "@/components/ui";
import { fmtDate, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { RenewalEvent } from "@/lib/types";

type RenewalDecision = "renew" | "terminate" | "renegotiate";
const DECISIONS: RenewalDecision[] = ["renew", "terminate", "renegotiate"];

export default function RenewalsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [deciding, setDeciding] = useState<RenewalEvent | null>(null);
  const [windowBusy, setWindowBusy] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["renewals"],
    queryFn: () => renewalsApi.list(),
  });
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });

  const titleMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of contracts ?? []) m.set(c.id, c.title);
    return m;
  }, [contracts]);

  async function runWindowCheck() {
    setWindowBusy(true);
    try {
      const r = await renewalsApi.runWindowCheck();
      qc.invalidateQueries({ queryKey: ["renewals"] });
      qc.invalidateQueries({ queryKey: ["contracts"] });
      notify(
        `${r.contracts_moved_to_renewal_due} contract(s) moved to renewal due`,
        "success",
      );
    } catch (e) {
      notify(
        e instanceof Error ? e.message : "Window check failed",
        "error",
      );
    } finally {
      setWindowBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Renewals"
        description="Monitor upcoming expirations and record renewal decisions."
        actions={
          <Button
            variant="outline"
            loading={windowBusy}
            onClick={runWindowCheck}
          >
            <RefreshCw className="h-4 w-4" />
            Run window check
          </Button>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading renewals…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<CalendarClock className="h-6 w-6" />}
          title="No renewal events"
          description="Renewal events are created as contracts approach their expiration window."
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Contract</TH>
                <TH>Expiration</TH>
                <TH>Notice date</TH>
                <TH>Window starts</TH>
                <TH>Decision</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((r) => (
                <TR key={r.id}>
                  <TD className="font-medium text-slate-900">
                    {titleMap.get(r.contract_id) ?? r.contract_id}
                  </TD>
                  <TD>{fmtDate(r.expiration_date)}</TD>
                  <TD>{fmtDate(r.notice_date)}</TD>
                  <TD>{fmtDate(r.renewal_window_starts_at)}</TD>
                  <TD>
                    <Badge tone={statusTone(r.decision)}>
                      {titleCase(r.decision)}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setDeciding(r)}
                    >
                      Decide
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <DecideModal
        renewal={deciding}
        onClose={() => setDeciding(null)}
        onDecided={() => {
          qc.invalidateQueries({ queryKey: ["renewals"] });
          notify("Renewal decision recorded", "success");
          setDeciding(null);
        }}
      />
    </div>
  );
}

function DecideModal({
  renewal,
  onClose,
  onDecided,
}: {
  renewal: RenewalEvent | null;
  onClose: () => void;
  onDecided: () => void;
}) {
  const { notify } = useToast();
  const [decision, setDecision] = useState<RenewalDecision>("renew");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!renewal) return;
    setBusy(true);
    try {
      await renewalsApi.decide(renewal.id, decision, note.trim() || undefined);
      setDecision("renew");
      setNote("");
      onDecided();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={!!renewal}
      onClose={onClose}
      title="Record renewal decision"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy}>
            Save decision
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Decision">
          <Select
            value={decision}
            onChange={(e) => setDecision(e.target.value as RenewalDecision)}
          >
            {DECISIONS.map((d) => (
              <option key={d} value={d}>
                {titleCase(d)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Note" hint="Optional context for this decision.">
          <Textarea
            rows={4}
            placeholder="Add a note…"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}
