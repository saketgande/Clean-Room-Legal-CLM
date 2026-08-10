"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { trademarksApi } from "@/lib/endpoints";
import { Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, ErrorState, PageHeader } from "@/components/ui";
import { useToast } from "@/components/toast";

const SERVICES: { key: "signa" | "serper" | "postgres"; label: string; description: string }[] = [
  { key: "signa", label: "Signa", description: "Trademark search across 200+ offices." },
  { key: "serper", label: "Serper.dev", description: "Web search context for the similarity checker." },
  { key: "postgres", label: "Local Postgres (pgvector)", description: "Internal portfolio similarity search." },
];

export default function TrademarkIntegrationsPage() {
  const { notify } = useToast();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["trademarks", "integrations-status"],
    queryFn: () => trademarksApi.integrationsStatus(),
  });
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, { status: string; detail?: string | null; latency_ms?: number | null }>>({});

  async function test(service: string) {
    setTesting(service);
    try {
      const result = await trademarksApi.testIntegration(service);
      setResults((r) => ({ ...r, [service]: result }));
      notify(`${service}: ${result.status}`, result.status === "ok" ? "success" : "error");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Test failed", "error");
    } finally {
      setTesting(null);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Integration status"
        description="Connection health for the trademark similarity-search providers."
        actions={
          <Button variant="outline" onClick={() => refetch()}>
            Refresh
          </Button>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Checking integrations…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          {SERVICES.map((svc) => {
            const status = data?.[svc.key];
            const tested = results[svc.key];
            const tone =
              tested?.status === "ok" || status?.status === "connected"
                ? "green"
                : tested?.status === "error" || status?.status === "error"
                  ? "red"
                  : "amber";
            return (
              <Card key={svc.key}>
                <CardHeader>
                  <CardTitle>{svc.label}</CardTitle>
                  <Badge tone={tone}>{tested?.status ?? status?.status ?? "unknown"}</Badge>
                </CardHeader>
                <CardBody className="space-y-3">
                  <p className="text-[13px] text-slate-500">{svc.description}</p>
                  {(tested?.detail ?? status?.detail) && (
                    <p className="text-xs text-slate-500">{tested?.detail ?? status?.detail}</p>
                  )}
                  {tested?.latency_ms !== undefined && tested.latency_ms !== null && (
                    <p className="text-xs text-slate-500">{Math.round(tested.latency_ms)} ms</p>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    loading={testing === svc.key}
                    onClick={() => test(svc.key)}
                  >
                    Test connection
                  </Button>
                </CardBody>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
