"use client";

import { Suspense } from "react";
import { AssistantWorkspace } from "@/components/assistant-workspace";
import { CenterSpinner } from "@/components/ui";

export default function AssistantPage() {
  return (
    <Suspense fallback={<CenterSpinner label="Loading assistant…" />}>
      <AssistantWorkspace />
    </Suspense>
  );
}
