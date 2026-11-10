"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { contractsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useScreenAccess } from "@/lib/screen-access";
import { ContractTypePicker } from "@/components/contract-type-picker";

const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

// Contracts whose type is blank or unrecognised, so type-based checks can't find them (FR-37).
export function TypeNeededPanel() {
  const [open, setOpen] = useState(false);
  const { user } = useAuth();
  const { canEdit } = useScreenAccess("contracts");
  const { data } = useQuery({ queryKey: ["contracts-type-needed"], queryFn: () => contractsApi.typeNeeded() });
  if (!data || data.items.length === 0) return null;
  const canSet = canEdit && !!user && (user.permissions.includes("contract:update") || user.permissions.includes("*"));
  const n = data.items.length;
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <div className="border-b border-slate-200 px-6 py-2">
      <button type="button" className="flex items-center gap-2 text-[13px] text-slate-900" aria-expanded={open} onClick={() => setOpen(!open)}>
        <Chevron className="h-4 w-4" />
        <b>{n} {n === 1 ? "contract needs" : "contracts need"} a type</b>
        <span className="text-slate-500">so type-based checks can find them</span>
      </button>
      {open && (
        <ul className="mt-2 divide-y divide-slate-200 text-[13px]">
          {data.items.map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-4 py-2">
              <Link href={`/contracts/${i.id}`} className="min-w-48 flex-1 font-medium text-brand-600 hover:underline">{i.title}</Link>
              <span className="w-40 text-slate-700">{i.counterparty_name ?? "—"}</span>
              <span className="w-24 text-slate-700">{title(i.stage)}</span>
              <span className="w-20 text-slate-500">{i.contract_type ?? "blank"}</span>
              {canSet
                ? <ContractTypePicker contractId={i.id} current={i.contract_type} suggested={i.suggested_type} />
                : <span className="text-slate-500">Ask someone who can edit contracts</span>}
            </li>
          ))}
        </ul>
      )}
      {open && data.truncated && <p className="mt-1 text-[12px] text-slate-500">Showing the newest 500.</p>}
    </div>
  );
}
