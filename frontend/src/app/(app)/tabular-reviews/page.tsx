"use client";

// Tabular Reviews list page — reskinned to the new mockup style (scoped `.tabr`).
// Opening/creating a review still routes to /tabular-reviews/[id] via CreateReviewModal.

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Table2 } from "lucide-react";
import { tabularApi } from "@/lib/endpoints";
import { CreateReviewModal } from "@/components/create-review-modal";
import { fmtRelative, titleCase } from "@/lib/utils";

const ic = (p: string) => (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: p }} />
);

export default function TabularReviewsPage() {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["tabular-reviews"],
    queryFn: tabularApi.list,
  });

  const reviews = data ?? [];
  const completed = reviews.filter((r) => /complet/i.test(r.status)).length;
  const running = reviews.filter((r) => /run|process|progress|queued/i.test(r.status)).length;
  const contractsCovered = new Set(reviews.flatMap((r) => r.source_contract_ids)).size;

  return (
    <div className="tabr">
      <style dangerouslySetInnerHTML={{ __html: TABR_CSS }} />
      <div className="hd">
        <div>
          <h1>Tabular Reviews</h1>
          <p className="sub">Run column-based questions across many contracts at once.</p>
        </div>
        <div className="acts">
          <button className="btn pri" onClick={() => setCreateOpen(true)}>+ New review</button>
        </div>
      </div>

      {reviews.length > 0 && (
        <div className="stats">
          <div className="stat">
            <span className="si a">{ic('<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18"/>')}</span>
            <div><div className="sv">{reviews.length}</div><div className="sl">review{reviews.length === 1 ? "" : "s"}</div></div>
          </div>
          <div className="stat">
            <span className="si g">{ic('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>')}</span>
            <div><div className="sv">{completed}</div><div className="sl">completed</div></div>
          </div>
          <div className="stat">
            <span className="si b">{ic('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</span>
            <div><div className="sv">{running}</div><div className="sl">in progress</div></div>
          </div>
          <div className="stat">
            <span className="si">{ic('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>')}</span>
            <div><div className="sv">{contractsCovered}</div><div className="sl">contracts covered</div></div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="grid">{[0, 1, 2].map((i) => <div key={i} className="rcard skel" />)}</div>
      ) : error ? (
        <div className="empty err">{error instanceof Error ? error.message : "Couldn't load reviews."}</div>
      ) : !data?.length ? (
        <div className="empty">
          <Table2 className="eicon" />
          <div className="etitle">No tabular reviews yet</div>
          <div className="edesc">Create a review to extract structured answers across a set of contracts.</div>
          <div style={{ marginTop: 12 }}><button className="btn pri" onClick={() => setCreateOpen(true)}>+ New review</button></div>
        </div>
      ) : (
        <div className="grid">
          {data.map((r) => (
            <div key={r.id} className="rcard">
              <div className="rch">
                <span className="rnm">{r.name}</span>
                <span className={`st st-${r.status}`}>{titleCase(r.status)}</span>
              </div>
              <p className="rmeta">
                {r.source_contract_ids.length} contract{r.source_contract_ids.length === 1 ? "" : "s"}
              </p>
              <p className="rupd">Updated {fmtRelative(r.updated_at)}</p>
              <button className="btn open" onClick={() => router.push(`/tabular-reviews/${r.id}`)}>Open</button>
            </div>
          ))}
        </div>
      )}

      <CreateReviewModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(id) => router.push(`/tabular-reviews/${id}`)}
      />
    </div>
  );
}

const TABR_CSS = `
.tabr{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .tabr{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.tabr .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.tabr .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .tabr .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.tabr .acts{margin-left:auto;display:flex;gap:8px}
.tabr .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.tabr .stat{display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px 16px}
.tabr .si{flex:none;display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:var(--surface-2);color:var(--ink-2)}
.tabr .si.a{background:var(--accent-soft);color:var(--accent)} .tabr .si.g{background:var(--good-soft);color:var(--good)} .tabr .si.b{background:var(--accent-soft);color:var(--accent)}
.tabr .si .ic{width:19px;height:19px}
.tabr .sv{font-size:21px;font-weight:700;letter-spacing:-.02em;line-height:1} .tabr .sl{margin-top:3px;font-size:11.5px;color:var(--ink-2)}
    
.tabr .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.tabr .rcard{text-align:left;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:15px 16px;display:flex;flex-direction:column;gap:8px;font:inherit;color:inherit;min-height:150px}
.tabr .rcard.skel{background:var(--surface-2);border-style:dashed;animation:tabrpulse 1.4s ease-in-out infinite}
@keyframes tabrpulse{50%{opacity:.55}}
.tabr .rch{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.tabr .rnm{font-weight:660;font-size:14px}
.tabr .st{flex:none;font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-2)}
.tabr .st-complete,.tabr .st-completed{background:var(--good-soft);color:var(--good)}
.tabr .st-running,.tabr .st-processing{background:var(--accent-soft);color:var(--accent)}
.tabr .rmeta{margin:0;font-size:12.5px;color:var(--ink-2)}
.tabr .rupd{margin:0;font-size:12px;color:var(--ink-3)}
.tabr .btn.open{margin-top:auto;width:100%;justify-content:center}
 
.tabr .eicon{width:24px;height:24px;margin:0 auto 10px;color:var(--ink-3)}
.tabr .etitle{font-weight:650;font-size:14px;color:var(--ink)}
.tabr .edesc{margin-top:4px;font-size:12.5px;color:var(--ink-2)}
`;
