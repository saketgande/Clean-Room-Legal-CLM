"use client";

// Standalone SLA dashboard. The SLA view used to be a tab inside Legal Intake;
// it now has its own screen so queue-health / custody / workload / routing
// effectiveness read as a first-class operations surface. Body is the existing
// SlaDashboardTab, reused as-is.

import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { EmptyState, PageHeader } from "@/components/ui";
import { SlaDashboardTab } from "../intake/_phase1";

export default function SlaPage() {
  const { user } = useAuth();
  const isStaff = can(user, "intake:read");
  const isAdmin = can(user, "admin_panel:access");

  if (!isStaff) {
    return (
      <div className="space-y-4">
        <PageHeader title="SLA" description="Service-level tracking for the legal intake queue." />
        <EmptyState title="Access restricted" description="SLA tracking is available to legal operations staff." />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="SLA" description="Queue health, custody legs, attorney workload, and routing-rule effectiveness." />
      <SlaDashboardTab isAdmin={isAdmin} />
    </div>
  );
}
