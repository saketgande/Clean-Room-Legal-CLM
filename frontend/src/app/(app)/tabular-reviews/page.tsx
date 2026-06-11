"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Table2, Plus } from "lucide-react";
import { tabularApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CenterSpinner,
  EmptyState,
  ErrorState,
  PageHeader,
} from "@/components/ui";
import { CreateReviewModal } from "@/components/create-review-modal";
import { fmtRelative, statusTone, titleCase } from "@/lib/utils";

export default function TabularReviewsPage() {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["tabular-reviews"],
    queryFn: tabularApi.list,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tabular Reviews"
        description="Run column-based questions across many contracts at once."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            New review
          </Button>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading reviews…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data?.length ? (
        <EmptyState
          icon={<Table2 className="h-6 w-6" />}
          title="No tabular reviews yet"
          description="Create a review to extract structured answers across a set of contracts."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New review
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {data.map((r) => (
            <Card key={r.id} className="flex flex-col">
              <CardBody className="flex flex-1 flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold text-slate-900">
                    {r.name}
                  </h3>
                  <Badge tone={statusTone(r.status)}>
                    {titleCase(r.status)}
                  </Badge>
                </div>
                <p className="text-sm text-slate-500">
                  {r.source_contract_ids.length} contract
                  {r.source_contract_ids.length === 1 ? "" : "s"}
                </p>
                <p className="text-xs text-slate-400">
                  Updated {fmtRelative(r.updated_at)}
                </p>
                <Button
                  variant="outline"
                  className="mt-auto w-full"
                  onClick={() => router.push(`/tabular-reviews/${r.id}`)}
                >
                  Open
                </Button>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <CreateReviewModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => router.push(`/tabular-reviews/${id}`)}
      />
    </div>
  );
}
