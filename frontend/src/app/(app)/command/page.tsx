import { redirect } from "next/navigation";

// Command was absorbed into Legal Intake — the hub now carries the operations
// wall (triage, pipeline, risk, renewals, engine) for requests and contracts.
// This route is kept only so old links/bookmarks land in the right place.
export default function CommandRedirect() {
  redirect("/intake");
}
