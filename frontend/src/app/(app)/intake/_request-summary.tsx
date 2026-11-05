// "Request in words" and the extra instructions, read-only, inside "What was asked" (spec 013, FR-30/35/39).
import type { RequestSummary } from "@/lib/types";
import { differsNote } from "@/lib/summary-edits";
import { fmtDateTime } from "@/lib/utils";

export const SUMMARY_CSS = `
.ro .xi{margin:8px 0 0;padding-left:20px;list-style:decimal;display:grid;gap:8px;font-size:12.5px;line-height:1.55;color:var(--ink-2);overflow-wrap:anywhere;white-space:pre-line}
.ro .xi-note{display:block;margin-top:2px;font:11.5px var(--sans);color:var(--warn);white-space:normal}
`;

export function RequestSummarySection(p: {
  status: "loading" | "error" | "ready";
  data: RequestSummary | undefined;
  onRetry: () => void;
}) {
  if (p.status === "loading") return null;
  if (p.status === "error") {
    return (
      <div style={{ marginTop: 16 }}>
        <span className="dim" style={{ fontSize: 12.5 }}>The request summary could not be loaded. </span>
        <button type="button" className="sel" style={{ width: "auto" }} onClick={p.onRetry}>Retry</button>
      </div>
    );
  }
  const d = p.data;
  if (!d || (!d.text && d.instructions.length === 0)) return null;
  return (
    <div style={{ marginTop: 16 }}>
      {d.text ? (
        <>
          <div className="lbl">Request in words · as filed</div>
          <div className="dim" style={{ fontSize: 11.5, marginTop: 4 }}>Built from the answers when the request was filed. The answers are what count.</div>
          <div className="quote" style={{ marginTop: 8 }}>{d.text}</div>
        </>
      ) : null}
      {d.instructions.length > 0 ? (
        <>
          <div className="lbl" style={{ marginTop: 14 }}>Extra instructions for Legal ({d.instructions.length})</div>
          <ol className="xi">
            {d.instructions.map((x, i) => {
              const n = differsNote(x.differs);
              return <li key={i}>{x.text}{n ? <span className="xi-note">{n}</span> : null}</li>;
            })}
          </ol>
        </>
      ) : null}
      {d.by_name || d.at ? (
        <div className="dim" style={{ fontSize: 11.5, marginTop: 8 }}>
          Written{d.by_name ? ` by ${d.by_name}` : ""}{d.at ? ` · ${fmtDateTime(d.at)}` : ""}
        </div>
      ) : null}
    </div>
  );
}
