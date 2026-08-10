"use client";

import { useEffect, useRef, useState } from "react";
import { trademarksApi } from "@/lib/endpoints";
import { Badge, Button, Card, MessageBar, Modal, Table, TD, TH, THead, TR } from "@/components/ui";
import { useToast } from "@/components/toast";
import type {
  SearchSimilarRequest,
  SearchSimilarResponse,
  TrademarkRiskLevel,
} from "@/lib/types";

const SOURCE_LABELS: Record<string, string> = {
  signa: "Signa",
  tmsearch: "TM Search",
  web_search: "Web search (Serper)",
  internal_portfolio: "Internal portfolio",
};

// Deliberate order — mirrors the backend's insertion order and the exported
// PDF report's section order.
const SOURCE_ORDER = ["signa", "tmsearch", "web_search", "internal_portfolio"];

// Fixed report accent colors — always the same regardless of app dark/light
// mode, so the exported PDF reads consistently as a formal document.
const REPORT_COLORS: Record<string, string> = {
  signa: "#6B4C9A",
  tmsearch: "#0E7C86",
  web_search: "#44546B",
  internal_portfolio: "#2440B8",
};

function riskTone(risk: TrademarkRiskLevel | string | undefined) {
  switch (risk) {
    case "high":
      return "red" as const;
    case "medium":
      return "amber" as const;
    case "low":
      return "green" as const;
    default:
      return "slate" as const;
  }
}

interface ItemDetail {
  label: string;
  value: string | null | undefined;
  full?: boolean;
  isLink?: boolean;
}

interface ItemInfo {
  title: string;
  subtitle: string;
  score: number | undefined;
  risk: TrademarkRiskLevel | undefined;
  details: ItemDetail[];
}

function describeItem(source: string, item: Record<string, unknown>): ItemInfo {
  switch (source) {
    case "internal_portfolio":
      return {
        title: String(item.name ?? ""),
        subtitle: `${item.jurisdiction ?? "—"} · Class ${item.nice_class ?? "—"}`,
        score: item.similarity_score as number | undefined,
        risk: item.risk_level as TrademarkRiskLevel | undefined,
        details: [
          { label: "Status", value: item.status as string },
          { label: "Filed on", value: item.filed_on as string },
          { label: "Internal ID", value: item.trademark_id as string },
        ],
      };
    case "web_search":
      return {
        title: String(item.title ?? ""),
        subtitle: "Web search · context only",
        score: undefined,
        risk: item.relevance as TrademarkRiskLevel | undefined,
        details: [
          { label: "Snippet", value: item.snippet as string, full: true },
          { label: "Link", value: item.url as string, isLink: true },
        ],
      };
    case "signa":
      return {
        title: String(item.mark_text ?? ""),
        subtitle: `${((item.office_code as string) ?? "—").toUpperCase()} · ${item.status_primary ?? "status unknown"}`,
        score: item.similarity_score as number | undefined,
        risk: item.risk_level as TrademarkRiskLevel | undefined,
        details: [
          { label: "Owner", value: item.owner_name as string },
          { label: "Filed on", value: item.filing_date as string },
          { label: "Nice classes", value: ((item.nice_classes as number[]) ?? []).join(", ") },
          { label: "Signa ID", value: item.signa_id as string },
        ],
      };
    case "tmsearch":
      return {
        title: String(item.mark_text ?? ""),
        subtitle: `${((item.office_code as string) ?? "—").toUpperCase()} · ${item.status ?? "status unknown"}`,
        score: item.similarity_score as number | undefined,
        risk: item.risk_level as TrademarkRiskLevel | undefined,
        details: [
          { label: "Application no.", value: item.application_number as string },
          { label: "Registration no.", value: item.registration_number as string },
          { label: "Filed on", value: item.filed_date as string },
          { label: "Protected in", value: ((item.protection_countries as string[]) ?? []).join(", ") },
          { label: "Image", value: item.image_url as string, isLink: true },
          { label: "TMSearch ID", value: item.tmsearch_id as string },
        ],
      };
    default:
      return { title: "", subtitle: "", score: undefined, risk: undefined, details: [] };
  }
}

const RECOMMENDATION_COPY: Record<string, { label: string; intent: "success" | "warning" | "error" }> = {
  clear_to_proceed: { label: "Clear to proceed", intent: "success" },
  review_recommended: { label: "Review recommended", intent: "warning" },
  proceed_with_caution: { label: "Proceed with caution", intent: "error" },
};

export function SearchSimilarModal({
  request,
  onClose,
  onAcknowledge,
}: {
  request: SearchSimilarRequest;
  onClose: () => void;
  onAcknowledge: (response: SearchSimilarResponse) => void;
}) {
  const { notify } = useToast();
  const [response, setResponse] = useState<SearchSimilarResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [generatedAt] = useState(() => new Date());
  const reportRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    trademarksApi
      .searchSimilar(request)
      .then((r) => {
        if (!cancelled) setResponse(r?.summary && r?.sources ? r : null);
      })
      .catch((e) => {
        if (!cancelled) notify(e instanceof Error ? e.message : "Search failed", "error");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.trademark_name]);

  async function handleExportPdf() {
    if (!reportRef.current || !response) return;
    setExporting(true);
    try {
      const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
        import("html2canvas"),
        import("jspdf"),
      ]);

      // Render at a fixed CSS width matching A4 proportions, so the scale
      // math below is exact rather than relying on jsPDF's own (fragile)
      // html()/autoPaging scaling.
      const canvas = await html2canvas(reportRef.current, {
        scale: 2,
        backgroundColor: "#ffffff",
        windowWidth: 794,
      });

      const pdf = new jsPDF({ orientation: "portrait", unit: "pt", format: "a4" });
      const pageWidthPt = pdf.internal.pageSize.getWidth();
      const pageHeightPt = pdf.internal.pageSize.getHeight();
      const footerReservePt = 26; // blank space reserved at the bottom of every page for the footer

      const pxPerPt = canvas.width / pageWidthPt;
      const contentHeightPt = pageHeightPt - footerReservePt;
      const pageHeightPx = Math.floor(contentHeightPt * pxPerPt);

      let renderedY = 0;
      let pageIndex = 0;

      while (renderedY < canvas.height) {
        const sliceHeightPx = Math.min(pageHeightPx, canvas.height - renderedY);

        const sliceCanvas = document.createElement("canvas");
        sliceCanvas.width = canvas.width;
        sliceCanvas.height = sliceHeightPx;
        const ctx = sliceCanvas.getContext("2d")!;
        ctx.drawImage(canvas, 0, renderedY, canvas.width, sliceHeightPx, 0, 0, canvas.width, sliceHeightPx);

        const sliceImgData = sliceCanvas.toDataURL("image/png");
        const sliceHeightPt = sliceHeightPx / pxPerPt;

        if (pageIndex > 0) pdf.addPage();
        pdf.addImage(sliceImgData, "PNG", 0, 0, pageWidthPt, sliceHeightPt);

        renderedY += sliceHeightPx;
        pageIndex += 1;
      }

      const pageCount = pdf.getNumberOfPages();
      for (let i = 1; i <= pageCount; i++) {
        pdf.setPage(i);
        pdf.setFontSize(7);
        pdf.setTextColor(150);
        pdf.text(`AEGIS Legal CLM · Trademark Similarity Search Report — Page ${i} of ${pageCount}`, 20, pageHeightPt - 12);
      }

      const safeName = request.trademark_name.replace(/[^a-z0-9]+/gi, "-").toLowerCase();
      pdf.save(`${safeName}-similarity-search-report.pdf`);
    } catch (e) {
      notify(e instanceof Error ? e.message : "PDF export failed", "error");
    } finally {
      setExporting(false);
    }
  }

  const recommendation = response ? RECOMMENDATION_COPY[response.summary.recommendation] : undefined;
  const totalResults = response
    ? SOURCE_ORDER.reduce((sum, key) => {
        const s = response.sources[key];
        return sum + (s?.status === "complete" ? s.results.length : 0);
      }, 0)
    : 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Similarity search — "${request.trademark_name}"`}
      size="xl"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Back to form
          </Button>
          {response && (
            <Button variant="outline" onClick={handleExportPdf} loading={exporting}>
              Export PDF
            </Button>
          )}
          <Button disabled={!response} onClick={() => response && onAcknowledge(response)}>
            Acknowledge and continue
          </Button>
        </>
      }
    >
      {loading ? (
        <div className="flex items-center justify-center py-10 text-sm text-slate-500">
          Searching Signa, TM Search, web, and your internal portfolio…
        </div>
      ) : !response ? (
        <MessageBar intent="error">Search failed. Try again or continue without it.</MessageBar>
      ) : (
        <div className="space-y-4">
          {/* ---------- Visible, interactive UI ---------- */}
          {recommendation && (
            <MessageBar intent={recommendation.intent}>
              <span className="font-medium">{recommendation.label}.</span>{" "}
              {response.summary.high_risk_count} high, {response.summary.medium_risk_count} medium,{" "}
              {response.summary.low_risk_count} low risk match(es) found.
            </MessageBar>
          )}

          {SOURCE_ORDER.map((sourceKey) => {
            const source = response.sources[sourceKey];
            if (!source) return null;
            return (
              <Card key={sourceKey} className="p-0">
                <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
                  <h4 className="text-[13px] font-semibold text-slate-900">
                    {SOURCE_LABELS[sourceKey] ?? sourceKey}
                  </h4>
                  <Badge tone={source.status === "complete" ? "green" : source.status === "not_configured" ? "slate" : "red"}>
                    {source.status.replace("_", " ")}
                  </Badge>
                </div>
                {source.status !== "complete" ? (
                  <p className="px-4 py-3 text-[13px] text-slate-500">
                    {source.error_message ?? "Not available for this search."}
                  </p>
                ) : source.results.length === 0 ? (
                  <p className="px-4 py-3 text-[13px] text-slate-500">No matches found.</p>
                ) : (
                  <Table>
                    <THead>
                      <tr>
                        <TH>Match</TH>
                        <TH>Detail</TH>
                        <TH>Score</TH>
                        <TH>Risk</TH>
                      </tr>
                    </THead>
                    <tbody>
                      {source.results.map((item, i) => {
                        const desc = describeItem(sourceKey, item);
                        return (
                          <TR key={i}>
                            <TD className="font-medium text-slate-900">{desc.title || "—"}</TD>
                            <TD>{desc.subtitle}</TD>
                            <TD>{desc.score !== undefined ? `${Math.round(desc.score * 100)}%` : "—"}</TD>
                            <TD>
                              <Badge tone={riskTone(desc.risk)}>{(desc.risk ?? "context_only").replace("_", " ")}</Badge>
                            </TD>
                          </TR>
                        );
                      })}
                    </tbody>
                  </Table>
                )}
              </Card>
            );
          })}

          {/* ---------- Hidden, print-only report layout for PDF export ----------
              Always renders ALL details (no collapse state), fixed light colors
              regardless of app theme, laid out like a formal document. Positioned
              off-screen rather than display:none so html2canvas can still render it. */}
          <div style={{ position: "fixed", left: -99999, top: 0, width: 794 }} aria-hidden="true">
            <div
              ref={reportRef}
              style={{
                width: 794,
                boxSizing: "border-box",
                background: "#ffffff",
                color: "#1A1A1A",
                fontFamily: "Georgia, 'Times New Roman', serif",
                padding: "40px 44px",
              }}
            >
              {/* Letterhead */}
              <div style={{ borderBottom: "3px solid #2440B8", paddingBottom: 14, marginBottom: 18 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div>
                    <div style={{ fontSize: 12, letterSpacing: 1.2, color: "#2440B8", fontWeight: 700, fontFamily: "Arial, sans-serif" }}>
                      AEGIS LEGAL CLM
                    </div>
                    <div style={{ fontSize: 27, fontWeight: 700, marginTop: 4 }}>Trademark Similarity Search Report</div>
                  </div>
                  <div
                    style={{
                      textAlign: "right",
                      fontSize: 11,
                      color: "#555",
                      fontFamily: "Arial, sans-serif",
                      lineHeight: 1.7,
                      flexShrink: 0,
                      marginLeft: 16,
                    }}
                  >
                    <div>Generated: {generatedAt.toLocaleString()}</div>
                    <div>Query ID: {response.query_id}</div>
                  </div>
                </div>
              </div>

              {/* Run metadata table */}
              <table style={{ width: "100%", tableLayout: "fixed", borderCollapse: "collapse", marginBottom: 24, fontFamily: "Arial, sans-serif", fontSize: 11 }}>
                <tbody>
                  {[
                    ["Trademark name", request.trademark_name],
                    ["Trademark type", (request.trademark_type ?? "word_mark").replace("_", " ")],
                    ["Description / goods & services", request.description || "—"],
                    ["Jurisdictions searched", (request.jurisdictions ?? []).join(", ") || "—"],
                    ["Total results returned", String(totalResults)],
                  ].map(([label, value]) => (
                    <tr key={label} style={{ borderBottom: "1px solid #E2DED4" }}>
                      <td style={{ padding: "8px 10px", background: "#F7F5F0", fontWeight: 700, width: 170, verticalAlign: "top", wordBreak: "break-word" }}>
                        {label}
                      </td>
                      <td style={{ padding: "8px 10px", verticalAlign: "top", wordBreak: "break-word" }}>{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Full per-source sections, always fully expanded */}
              {SOURCE_ORDER.map((key) => {
                const source = response.sources[key];
                const color = REPORT_COLORS[key];
                if (!source) return null;

                return (
                  <div key={key} style={{ marginBottom: 24, breakInside: "avoid" }}>
                    <div style={{ borderLeft: `5px solid ${color}`, paddingLeft: 12, marginBottom: 10 }}>
                      <div style={{ fontSize: 16, fontWeight: 700, color, fontFamily: "Arial, sans-serif" }}>
                        {SOURCE_LABELS[key]}
                      </div>
                      <div style={{ fontSize: 10, color: "#777", fontFamily: "Arial, sans-serif" }}>
                        Status: {source.status.replace("_", " ")}
                        {source.status === "complete" && ` · ${source.results.length} result(s)`}
                      </div>
                    </div>

                    {source.status !== "complete" && (
                      <div style={{ fontSize: 10.5, color: "#900", fontFamily: "Arial, sans-serif", paddingLeft: 12, wordBreak: "break-word" }}>
                        {source.error_message || "No further detail available."}
                      </div>
                    )}

                    {source.status === "complete" && source.results.length === 0 && (
                      <div style={{ fontSize: 10.5, color: "#999", fontFamily: "Arial, sans-serif", paddingLeft: 12 }}>
                        No matches found.
                      </div>
                    )}

                    {source.status === "complete" && source.results.length > 0 && (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, paddingLeft: 12 }}>
                        {source.results.map((item, i) => {
                          const info = describeItem(key, item);
                          return (
                            <div
                              key={i}
                              style={{
                                border: "1px solid #E2DED4",
                                borderRadius: 6,
                                padding: "10px 12px",
                                breakInside: "avoid",
                                minWidth: 0,
                                overflow: "hidden",
                              }}
                            >
                              <div style={{ display: "flex", justifyContent: "space-between", gap: 6, fontFamily: "Arial, sans-serif" }}>
                                <div style={{ fontWeight: 700, fontSize: 11.5, wordBreak: "break-word", minWidth: 0 }}>
                                  {info.title || "(untitled)"}
                                </div>
                                {info.score !== undefined && (
                                  <div style={{ fontSize: 9.5, fontWeight: 700, color, whiteSpace: "nowrap", flexShrink: 0 }}>
                                    {Math.round(info.score * 100)}%
                                  </div>
                                )}
                              </div>
                              <div style={{ fontSize: 9, color: "#666", fontFamily: "Arial, sans-serif", marginBottom: 6, wordBreak: "break-word" }}>
                                {info.subtitle}
                              </div>
                              <table style={{ width: "100%", tableLayout: "fixed", borderCollapse: "collapse", fontFamily: "Arial, sans-serif" }}>
                                <tbody>
                                  {info.details.map((d, di) => (
                                    <tr key={di}>
                                      <td style={{ fontSize: 8.5, color: "#888", padding: "2px 6px 2px 0", width: 78, verticalAlign: "top" }}>
                                        {d.label}
                                      </td>
                                      <td style={{ fontSize: 9, padding: "2px 0", verticalAlign: "top", wordBreak: "break-word" }}>
                                        {d.value || "—"}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}

              {/* Summary */}
              <div style={{ background: "#F7F5F0", border: "1px solid #E2DED4", borderRadius: 8, padding: 16, marginTop: 10, fontFamily: "Arial, sans-serif" }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>Risk Summary</div>
                <table style={{ width: "100%", fontSize: 11, tableLayout: "fixed" }}>
                  <tbody>
                    <tr>
                      <td style={{ padding: "3px 0" }}>High-risk conflicts</td>
                      <td style={{ padding: "3px 0", textAlign: "right", fontWeight: 700, color: "#B3261E" }}>
                        {response.summary.high_risk_count}
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "3px 0" }}>Medium-risk conflicts</td>
                      <td style={{ padding: "3px 0", textAlign: "right", fontWeight: 700, color: "#B8722E" }}>
                        {response.summary.medium_risk_count}
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "3px 0" }}>Low-risk conflicts</td>
                      <td style={{ padding: "3px 0", textAlign: "right", fontWeight: 700, color: "#1B7A43" }}>
                        {response.summary.low_risk_count}
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "7px 0 0", fontWeight: 700 }}>Recommendation</td>
                      <td style={{ padding: "7px 0 0", textAlign: "right", fontWeight: 700, wordBreak: "break-word" }}>
                        {response.summary.recommendation.replace(/_/g, " ")}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div style={{ fontSize: 9, color: "#AAA", marginTop: 16, fontFamily: "Arial, sans-serif" }}>
                This report is advisory only and does not constitute legal clearance. Confirm findings with qualified IP counsel before filing.
              </div>
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
