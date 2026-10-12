// The server refuses a request that needs a permission the person's role lacks with a developer-style message
// ("Missing permission: intake:read"). Those codes mean nothing to the person who sees them, so they are
// rewritten once, where the error is created, and every page and toast that shows it benefits.

const MISSING_PERMISSION = /^\s*missing permissions?\s*:/i;

export const NO_PERMISSION_MESSAGE = "Your role doesn't allow this. Ask an administrator if you need access.";

/** The text to show for a failed request: the server's own message, except for a bare permission-code refusal. */
export function plainErrorMessage(status: number, message: string): string {
  if (status === 403 && MISSING_PERMISSION.test(message)) return NO_PERMISSION_MESSAGE;
  return message;
}
