"use client";

// The contract detail route is now the AI-first CLM editor workspace (Screen 4).
// The old full-lifecycle page was retired — approvals, signatures, sharing, etc.
// live on their own routes; this surface is drafting + review, driven by the
// workflow engine. See ./_clm-workspace.

import { use } from "react";
import { ClmWorkspace } from "./_clm-workspace";

export default function ContractDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ClmWorkspace id={id} />;
}
