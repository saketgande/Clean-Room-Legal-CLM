// Who sees the buttons that change an obligation. The server decides (it answers 403 when this is wrong):
// changing an obligation needs the role permission AND the contract rule (the contract's owner, creator, an
// admin or an update-level share), and the contract rule is not on the obligation, so this is only a hint
// from the role. A person with the permission on a contract they may only read still sees the buttons and
// gets a plain 403 ("You can read this contract but not change it").

/** Complete, edit, confirm a date, log an event date, settle a disputed date, retry an extraction. */
export const EDIT_OBLIGATIONS_PERMISSION = "obligation:update";

/** Run reminders now: an administrator's tool (it emails owners across the organisation). */
export const RUN_REMINDERS_PERMISSION = "admin_panel:access";

function holds(permissions: readonly string[] | null | undefined, wanted: string): boolean {
  if (!permissions) return false;
  return permissions.includes("*") || permissions.includes(wanted);
}

export const canEditObligations = (permissions: readonly string[] | null | undefined): boolean =>
  holds(permissions, EDIT_OBLIGATIONS_PERMISSION);

export const canRunReminders = (permissions: readonly string[] | null | undefined): boolean =>
  holds(permissions, RUN_REMINDERS_PERMISSION);
