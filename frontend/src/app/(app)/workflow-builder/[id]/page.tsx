"use client";

// The workflow editor route now renders the full-screen visual designer (Screen 6).
// The old two-column form editor was retired; see ./_designer.

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { CenterSpinner, ErrorState } from "@/components/ui";
import { workflowsApi } from "@/lib/endpoints";
import { WorkflowDesigner } from "./_designer";

export default function WorkflowEditorPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const isNew = id === "new";

  const { data: flow, isLoading, error } = useQuery({
    queryKey: ["flows", id],
    queryFn: () => workflowsApi.getFlow(id),
    enabled: !isNew,
  });

  if (!isNew && isLoading) return <CenterSpinner label="Loading workflow…" />;
  if (!isNew && error) return <ErrorState error={error} />;

  return <WorkflowDesigner flow={isNew ? null : flow ?? null} isNew={isNew} />;
}
