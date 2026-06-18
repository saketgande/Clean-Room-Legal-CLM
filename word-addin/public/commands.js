/* Function file for ribbon commands.
   The ribbon button uses a ShowTaskpane action, which needs no code here, but
   Office requires a valid FunctionFile to be present and loaded. This also
   gives you a place to add custom ribbon actions later. */
/* global Office */

Office.onReady(() => {});

// Example custom action (not wired to any button yet). Custom actions must call
// event.completed() so Office knows the command finished.
function ping(event) {
  event.completed();
}

if (Office.actions && Office.actions.associate) {
  Office.actions.associate("ping", ping);
}
