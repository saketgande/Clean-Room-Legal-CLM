"use client";

import { useRouter } from "next/navigation";
import { MockupShell } from "../intake/_legal-intake";
import { MyWorkBoard } from "../intake/_my-work";

export default function MyWorkPage() {
  const router = useRouter();
  return (
    <MockupShell active="mywork" title="My Work" subtitle="your tasks and the requests you raised, in one place">
      <MyWorkBoard onOpen={(id) => router.push(`/intake?open=${id}`)} />
    </MockupShell>
  );
}
