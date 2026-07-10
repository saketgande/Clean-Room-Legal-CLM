"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CenterSpinner } from "@/components/ui";

// The portfolio lives on the Command wall now.
export default function ContractsRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/command");
  }, [router]);
  return <CenterSpinner label="Opening Command…" />;
}
