"use client";

import { Lock } from "lucide-react";
import { EmptyState, PageHeader } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { ScreenGrantsPanel } from "./_screen-grants-panel";

// FR-23: admin screen to view/assign/revoke role -> screen action-level
// grants, scoped to the administrator's own organization.
export default function ScreenAccessPage() {
  const { user } = useAuth();

  if (!can(user, "screen_access:read")) {
    return (
      <div className="mx-auto max-w-[1280px] space-y-4">
        <PageHeader
          title="Screen access"
          description="Control which roles can view, add, edit and delete on each screen."
        />
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="Access restricted"
          description="You don't have permission to view screen-access administration. Contact an admin if you need access."
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-4">
      <PageHeader
        title="Screen access"
        description="Control which roles can view, add, edit and delete on each screen."
      />
      <ScreenGrantsPanel />
    </div>
  );
}
