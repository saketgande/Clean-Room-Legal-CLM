"use client";

// Check all (spec 006 FR-17): one paid AI review for every template that has a playbook and is not already
// being checked.
//
//   <CheckAll templates onStarted />      the button and its confirm. Nothing is called before the person
//                                         confirms, and the confirm says how many paid reviews it will run.
//   <CheckAllProgress run templates />   "Check all: k of N finished", computed from the list's own polled
//                                         statuses of the started templates, with failures named.
//
// The server decides what is left out (`skipped`); the count in the confirm is the page's own reading of the
// same rule from the list it is showing.

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { Button, MessageBar, Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import { draftingTemplatesApi } from "@/lib/endpoints";
import type { DraftingTemplateSummary } from "@/lib/types";
import { plural } from "@/lib/utils";

export interface CheckAllRun {
  keys: string[]; // the templates the server started a check for
  leftOut: number; // already being checked, or no playbook
}

const checkable = (t: DraftingTemplateSummary) => t.status.state !== "checking" && t.status.state !== "no_playbook";

export function CheckAll({
  templates,
  onStarted,
}: {
  templates: DraftingTemplateSummary[];
  onStarted: (run: CheckAllRun) => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [open, setOpen] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eligible = templates.filter(checkable);

  async function start() {
    setStarting(true);
    setError(null);
    try {
      const result = await draftingTemplatesApi.checkAll();
      // Read the list again before showing progress: a stale list would still say "passes" for a template
      // whose check was just queued, and count it as finished.
      await qc.refetchQueries({ queryKey: ["drafting-templates"] });
      setOpen(false);
      if (result.started.length === 0) {
        notify("Nothing to check: every template is already being checked or has no playbook.", "info");
      } else {
        onStarted({ keys: result.started.map((s) => s.key), leftOut: result.skipped.length });
        notify(`Check all started: ${plural(result.started.length, "paid AI review")} running in the background.`, "info");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Check all could not be started.");
    } finally {
      setStarting(false);
    }
  }

  function close() {
    if (starting) return;
    setOpen(false);
    setError(null);
  }

  return (
    <>
      <Button type="button" variant="outline" onClick={() => setOpen(true)}>
        <ListChecks className="h-4 w-4" aria-hidden="true" />
        Check all
      </Button>
      <Modal
        open={open}
        onClose={close}
        title="Check all templates?"
        footer={
          <>
            <Button type="button" variant="outline" onClick={close} disabled={starting}>
              Cancel
            </Button>
            <Button type="button" loading={starting} disabled={eligible.length === 0 || starting} onClick={start}>
              Run {plural(eligible.length, "paid AI review")}
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm text-slate-600">
          {eligible.length > 0 ? (
            <>
              <p>
                This runs {plural(eligible.length, "paid AI review")}, one for each of these templates:
              </p>
              <ul className="list-disc space-y-0.5 pl-5 text-slate-900">
                {eligible.map((t) => (
                  <li key={t.key}>{t.name}</li>
                ))}
              </ul>
            </>
          ) : (
            <p>
              No template can be checked right now: each one is already being checked or has no playbook, so no paid
              review would run.
            </p>
          )}
          <p>Templates already being checked or with no playbook are left out.</p>
          {error && <MessageBar intent="error">{error}</MessageBar>}
        </div>
      </Modal>
    </>
  );
}

export function CheckAllProgress({
  run,
  templates,
  onDismiss,
}: {
  run: CheckAllRun;
  templates: DraftingTemplateSummary[];
  onDismiss: () => void;
}) {
  const byKey = new Map(templates.map((t) => [t.key, t]));
  const finished = run.keys.filter((k) => byKey.get(k)?.status.state !== "checking");
  const failed = run.keys.filter((k) => byKey.get(k)?.status.state === "couldnt_check");
  const done = finished.length === run.keys.length;
  return (
    <MessageBar intent={!done ? "info" : failed.length > 0 ? "warning" : "success"}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-0.5">
          <p className="font-medium">
            Check all: {finished.length} of {run.keys.length} finished
          </p>
          {failed.length > 0 && (
            <p>Couldn't check: {failed.map((k) => byKey.get(k)?.name ?? k).join(", ")}. The others carried on.</p>
          )}
          {run.leftOut > 0 && (
            <p>{plural(run.leftOut, "template")} left out: already being checked, or no playbook.</p>
          )}
        </div>
        {done && (
          <Button type="button" size="sm" variant="outline" onClick={onDismiss}>
            Dismiss
          </Button>
        )}
      </div>
    </MessageBar>
  );
}
