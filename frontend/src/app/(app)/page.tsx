"use client";

import { Suspense } from "react";
import { AssistantWorkspace } from "@/components/assistant-workspace";
import { CenterSpinner } from "@/components/ui";

// The opening page is the AI assistant chatbot (Legora/Mike style).
export default function HomePage() {
  return (
    <Suspense fallback={<CenterSpinner label="Loading assistant…" />}>
      <AssistantWorkspace />
    </Suspense>
  );
}
