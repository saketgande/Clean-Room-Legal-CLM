"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { intakeApi, trademarksApi } from "@/lib/endpoints";
import { isClearanceRequest } from "@/lib/clearance-case";
import { RequestOverview } from "./_request-overview";

/**
 * Opens a request. A trademark clearance is worked on its case page in Trademarks, so it goes there;
 * every other request gets the usual page. This is the one place a request is opened from (the queue,
 * New request, and links such as My Work's `?open=`), so no second page for a clearance exists.
 *
 * If the case cannot be found or the person cannot open Trademarks, the request page is shown as before,
 * so a link never dead-ends.
 */
export function OpenRequest({ id, canManage, onBack }: { id: string; canManage: boolean; onBack: () => void }) {
  const router = useRouter();
  // Same key and loader as the request page, so opening a request that stays here costs no extra call.
  const { data: request, isLoading } = useQuery({ queryKey: ["intake-request", id], queryFn: () => intakeApi.get(id) });
  const clearance = isClearanceRequest(request?.field_values as Record<string, unknown> | null | undefined);
  const { data: found, isError } = useQuery({
    queryKey: ["trademark-by-request", id],
    queryFn: () => trademarksApi.byRequest(id),
    enabled: clearance,
    retry: false,
  });

  useEffect(() => {
    // `replace`: the Back button should not return to a request link that would send the person on again.
    if (found) router.replace(`/trademarks/${found.trademark_id}`);
  }, [found, router]);

  // A clearance stays on this placeholder while its case is looked up and while the redirect happens,
  // so the request page never flashes up first.
  if (isLoading || (clearance && !isError)) {
    return <p className="px-1 py-6 text-[13px] text-slate-500">Opening request…</p>;
  }
  return <RequestOverview id={id} canManage={canManage} onBack={onBack} />;
}
