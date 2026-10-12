/** A job's progress as a whole percent for display.
 *
 *  The worker stores progress as a whole percent (5 when it starts, 100 when it is done). The Jobs
 *  page used to treat it as a 0-1 fraction, clamped it to 1 and multiplied by 100, so a job at 5%
 *  was drawn as 100% complete. */
export function jobProgressPercent(progress: number | null | undefined): number {
  if (typeof progress !== "number" || !Number.isFinite(progress)) return 0;
  return Math.round(Math.max(0, Math.min(100, progress)));
}
