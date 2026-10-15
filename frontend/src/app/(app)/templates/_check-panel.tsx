"use client";

// "Check against playbook": runs the playbook's real AI review on the template text, the same review a
// drafted contract gets. It exists because a template and its playbook can disagree in meaning while
// still sharing the words a phrase test looks for, and the first anyone knew was a risk on a fresh draft.

import type { TemplateCheckFinding, TemplateCheckResult } from "@/lib/types";

export type CheckState =
  | { status: "idle" }
  | { status: "running" }
  | { status: "done"; result: TemplateCheckResult; body: string } // body = the text that was checked
  | { status: "error"; message: string };

const SEVERITY_LABEL: Record<TemplateCheckFinding["severity"], string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  critical: "Critical",
};

const clauseLabel = (c: string) => {
  const t = c.replace(/_/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : "Other";
};

export function CheckPanel({
  state,
  playbook,
  dirty,
  stale,
  onRun,
}: {
  state: CheckState;
  playbook: string | null; // the playbook that reviews contracts drafted from this template
  dirty: boolean; // the editor holds changes that are not saved yet
  stale: boolean; // the text changed after the last check
  onRun: () => void;
}) {
  const running = state.status === "running";
  return (
    <div className="chk">
      <div className="chkhd">
        <div>
          <h3>Check against the playbook</h3>
          <p className="muted">
            {playbook ? (
              <>
                Runs the AI review of <b>{playbook}</b> on this template, the same review every contract drafted from
                it gets. Anything it reports is what a fresh draft would come back with.
              </>
            ) : (
              "No published playbook reviews this template, so there is nothing to check it against."
            )}
          </p>
          <p className="muted">
            {dirty ? "Checks the text in the editor, including changes you have not saved. " : "Checks the saved template. "}
            It is one real AI review: it uses AI credits, and nothing is saved.
          </p>
        </div>
        <button className="btn pri" onClick={onRun} disabled={!playbook || running}>
          {running ? "Checking…" : state.status === "done" ? "Check again" : "Check against playbook"}
        </button>
      </div>

      {running && (
        <div className="chkmsg" role="status">
          The AI is reading the template against the playbook&apos;s rules. This can take a little while.
        </div>
      )}

      {state.status === "error" && (
        <div className="err" role="alert">
          {state.message}
          <div>
            <button className="btn" onClick={onRun} disabled={!playbook}>
              Try again
            </button>
          </div>
        </div>
      )}

      {state.status === "done" && (
        <>
          {stale && (
            <div className="chkwarn" role="status">
              You changed the text after this check. Run it again to check the new wording.
            </div>
          )}
          <CheckResult result={state.result} />
        </>
      )}
    </div>
  );
}

function CheckResult({ result }: { result: TemplateCheckResult }) {
  const n = result.findings.length;
  return (
    <div className="chkres" data-testid="check-result">
      {n === 0 ? (
        <div className="chkok" role="status">
          <b>No deviations found.</b> The AI review of {result.playbook_name} found nothing to flag in this wording
          ({result.rule_count} rules checked).
          {result.summary ? <div className="muted">{result.summary}</div> : null}
        </div>
      ) : (
        <>
          <div className="chkbad" role="status">
            <b>
              {n} {n === 1 ? "deviation" : "deviations"} found.
            </b>{" "}
            A contract drafted from this wording would come back with {n === 1 ? "this risk" : "these risks"} (
            {result.rule_count} rules checked).
            {result.summary ? <div className="muted">{result.summary}</div> : null}
          </div>
          <ul className="chkl">
            {result.findings.map((f, i) => (
              <li key={`${f.clause_type}-${i}`} className={`fi sev-${f.severity}`}>
                <div className="fihd">
                  <b>{clauseLabel(f.clause_type)}</b>
                  <span className={`sev ${f.severity}`}>{SEVERITY_LABEL[f.severity]}</span>
                  {f.approval_required && <span className="sev appr">Needs approval</span>}
                </div>
                <p>{f.issue}</p>
                {f.quote && <blockquote>{f.quote}</blockquote>}
                {f.suggested_fix && (
                  <p className="fix">
                    <b>Suggested change:</b> {f.suggested_fix}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {result.text_was_truncated && (
        <p className="muted">This template is longer than the review can read, so the end of it was not checked.</p>
      )}
      <p className="muted">
        Checked as a request with no form answers is drafted: blank answers fall back to the playbook&apos;s preferred
        positions. This judges the wording of the template; it cannot judge what a requester picks in the request form
        (their term, governing law, payment days).
      </p>
    </div>
  );
}
