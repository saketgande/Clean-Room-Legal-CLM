"use client";

import { use } from "react";

import { DocstudioWorkspace } from "@/features/docstudio/DocstudioWorkspace";

export default function DocstudioVersionPage({
  params,
}: {
  params: Promise<{ versionId: string }>;
}) {
  const { versionId } = use(params);
  return <DocstudioWorkspace versionId={versionId} />;
}
