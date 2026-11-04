// A trademark clearance, and an examination / opposition response, are worked in the Trademarks module, so
// Legal Intake opens them there instead of drawing a second page for the same case.
//
// Such a request is recognised by the answer its workflow is chosen by (the workflow's criteria test this
// field), so there is one definition of "this request is worked in Trademarks". They must equal the
// backend's CLEARANCE_MARKER_FIELD and RESPONSE_MARKER_FIELD (app/trademarks/clearance_service.py).

export const CLEARANCE_MARKER_FIELD = "tm_clearance";
export const RESPONSE_MARKER_FIELD = "tm_response";

export function isClearanceRequest(fieldValues: Record<string, unknown> | null | undefined): boolean {
  return fieldValues?.[CLEARANCE_MARKER_FIELD] === true || fieldValues?.[RESPONSE_MARKER_FIELD] === true;
}
