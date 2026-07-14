"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CenterSpinner } from "@/components/ui";

// The portfolio lives on the Legal Intake hub wall now.
export default function ContractsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/intake");
  }, [router]);
  return <CenterSpinner label="Opening Legal Intake…" />;
}
