import type { IntakeRequest } from "./types";

type Shape = Pick<IntakeRequest, "status"> & Partial<Pick<IntakeRequest, "workflow" | "contract_id">>;

/** Still being worked on: not approved and not closed. The one definition the intake header,
 *  the board's briefing and its filters all use, so they cannot disagree. */
export const isOpenRequest = (r: Shape): boolean => r.status !== "closed" && r.status !== "approved";

/** Waiting for someone to pick it up: open, no workflow has started on it, and nobody has drafted its
 *  contract by hand. A request that already has a workflow is "in workflow", not "awaiting triage", so the
 *  two tiles never count it twice; one with a drafted contract has been picked up (round 3). */
export const isAwaitingTriage = (r: Shape): boolean =>
  r.status === "open" && (r.workflow ?? []).length === 0 && !r.contract_id;
