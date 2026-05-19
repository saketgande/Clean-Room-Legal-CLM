"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CenterSpinner } from "@/components/ui";

// Contracts now live inside the Contract Hub.
export default function ContractsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/contract-hub");
  }, [router]);
  return <CenterSpinner label="Opening Contract Hub…" />;
}
