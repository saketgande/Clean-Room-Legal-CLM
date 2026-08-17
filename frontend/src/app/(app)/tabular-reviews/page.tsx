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

export default function TabularReviewsPage() {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["tabular-reviews"],
    queryFn: tabularApi.list,
  });

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
.tabr{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .tabr{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.tabr .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.tabr .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em} .tabr .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.tabr .acts{margin-left:auto;display:flex;gap:8px}
.tabr .btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer} .tabr .btn:hover{background:var(--surface-2)} .tabr .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .tabr .btn.pri:hover{filter:brightness(1.06)} .tabr .btn[disabled]{opacity:.6;pointer-events:none}
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
.tabr .empty{border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:30px;text-align:center;font-size:13px;color:var(--ink-2)} .tabr .empty.err{border-color:color-mix(in srgb,#bb4835 40%,var(--border));color:#bb4835}
.tabr .eicon{width:24px;height:24px;margin:0 auto 10px;color:var(--ink-3)}
.tabr .etitle{font-weight:650;font-size:14px;color:var(--ink)}
.tabr .edesc{margin-top:4px;font-size:12.5px;color:var(--ink-2)}
`;
