"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { contractsApi } from "@/lib/endpoints";
import { Button, Select } from "@/components/ui";
import { useToast } from "@/components/toast";
import type { ContractResponse } from "@/lib/types";

// Shared by the contract page prompt and the "need a type" list (FR-35, FR-36).
export function ContractTypePicker({ contractId, current, suggested, onSaved }: {
  contractId: string;
  current: string | null;
  suggested: string | null;
  onSaved?: (c: ContractResponse) => void;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const { data: options = [] } = useQuery({ queryKey: ["contract-types"], queryFn: () => contractsApi.types(), staleTime: Infinity });
  const [picked, setPicked] = useState<string | null>(null);
  // suggested ?? (current if listed) ?? "" — until the person chooses.
  const fallback = suggested ?? (current && options.some((o) => o.value === current) ? current : "");
  const value = picked ?? fallback;

  const save = useMutation({
    mutationFn: () => contractsApi.setType(contractId, value),
    onSuccess: (c) => {
      for (const key of [["contract", contractId], ["contracts-type-needed"], ["contracts-list"]]) qc.invalidateQueries({ queryKey: key });
      notify("Contract type saved", "success");
      onSaved?.(c);
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Couldn't save the type", "error"),
  });

  return (
    <span className="inline-flex items-center gap-2">
      <Select aria-label="Contract type" className="w-48" value={value} onChange={(e) => setPicked(e.target.value)}>
        <option value="">Choose a type…</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Select>
      <Button size="sm" disabled={!value || save.isPending} onClick={() => save.mutate()}>Save type</Button>
    </span>
  );
}
