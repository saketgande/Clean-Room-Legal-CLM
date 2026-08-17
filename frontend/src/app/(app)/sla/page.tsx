"use client";

// Standalone SLA dashboard — indigo mockup style (scoped `.slad`), matching the
// pattern set by workflow-builder/page.tsx. The SLA view used to be a tab inside
// Legal Intake; it now has its own screen so queue-health / custody / workload /
// routing effectiveness read as a first-class operations surface. Body is the
// existing SlaDashboardTab (in ../intake/_phase1), reused as-is — only its
// presentation was reskinned, not its logic/queries/props.

import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { SlaDashboardTab } from "../intake/_phase1";

export default function SlaPage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  const isAdmin = can(user, "admin_panel:access");

  return (
    <div className="slad">
      <style dangerouslySetInnerHTML={{ __html: SLAD_CSS }} />
      <div className="hd">
        <div>
          <h1>SLA</h1>
          <p className="sub">
            {isStaff
              ? "Queue health, custody legs, attorney workload, and routing-rule effectiveness."
              : "Service-level tracking for the legal intake queue."}
          </p>
        </div>
      </div>

      {!isStaff ? (
        <div className="empty">Access restricted — SLA tracking is available to legal operations staff.</div>
      ) : (
        <SlaDashboardTab isAdmin={isAdmin} />
      )}
    </div>
  );
}

const SLAD_CSS = `
.slad{--surface:#fff;--surface-2:#eef1f6;--inset:#f8fafc;--ink:#18213a;--ink-2:#586178;--ink-3:#8a92a6;--border:#e4e8f0;--border-strong:#ccd3e0;--accent:#3b4aa0;--accent-ink:#fff;--accent-soft:#eaecf8;--good:#2f875f;--good-soft:#e4f1ea;--warn:#a9772b;--warn-soft:#f6edd9;--crit:#bb4835;--crit-soft:#f7e4df;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .slad{--surface:#141922;--surface-2:#1b2130;--inset:#10141d;--ink:#e8ebf3;--ink-2:#9aa3b8;--ink-3:#6b7488;--border:#242b39;--border-strong:#333c4e;--accent:#8290e6;--accent-ink:#0c0f16;--accent-soft:#1f2740;--good:#5cbf90;--good-soft:#15271f;--warn:#d3a24e;--warn-soft:#2a2213;--crit:#e2705c;--crit-soft:#2c1a17;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.slad *{box-sizing:border-box}
.slad .dim{color:var(--ink-3)}
.slad .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.slad .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.slad .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.slad .empty{border:1px dashed var(--border-strong);border-radius:12px;background:var(--inset);padding:26px;text-align:center;font-size:13px;color:var(--ink-2)}

.slad .toprow{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap}
.slad .btn{display:inline-flex;align-items:center;gap:6px;padding:7px 13px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer}
.slad .btn:hover{background:var(--surface-2)}
.slad .btn[disabled]{opacity:.6;pointer-events:none}

.slad .statcard{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px;margin-bottom:16px}
.slad .tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}
.slad .tile{border:1px solid var(--border);border-radius:10px;padding:12px 14px;background:var(--inset)}
.slad .tile .tn{font:700 24px/1 var(--mono);letter-spacing:-.02em;color:var(--ink)}
.slad .tile .tl{margin-top:5px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3)}
.slad .tile.a .tn{color:var(--accent)} .slad .tile.w .tn{color:var(--warn)} .slad .tile.c .tn{color:var(--crit)}

.slad .card{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);overflow:hidden;margin-bottom:14px}
.slad .cardhd{display:flex;align-items:center;gap:10px;padding:12px 15px;border-bottom:1px solid var(--border)}
.slad .cardhd h3{margin:0;font-size:13px;font-weight:660}
.slad .cardhd .ref{margin-left:auto;font:600 11px var(--mono);color:var(--ink-3)}
.slad .cardbd{padding:14px 15px}
.slad .cardbd.nopad{padding:0}

.slad .cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media (max-width:900px){.slad .tiles{grid-template-columns:repeat(2,1fr)}.slad .cols{grid-template-columns:1fr}}

.slad table{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.slad tbody td{padding:9px 15px;border-bottom:1px solid var(--border);vertical-align:middle;color:var(--ink)}
.slad tbody tr:last-child td{border-bottom:0}
.slad tbody tr:hover td{background:var(--inset)}
.slad .num{font-variant-numeric:tabular-nums;color:var(--ink-2)}
.slad .fw{font-weight:600}
.slad .emptyline{padding:14px 15px;font-size:12.5px;color:var(--ink-3)}
.slad .badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:99px;font:600 11px var(--sans)}
.slad .badge.c{background:var(--crit-soft);color:var(--crit)}
`;
