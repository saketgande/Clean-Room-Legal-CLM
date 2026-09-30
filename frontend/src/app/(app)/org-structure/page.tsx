"use client";

import { useState } from "react";
import { Lock } from "lucide-react";
import { EmptyState, PageHeader, Tabs } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { OrgUnitTree } from "./_org-unit-tree";
import { RoleGrantsPanel } from "./_role-grants-panel";

// FR-26/FR-27: admin screen to manage the org-unit tree and role grants,
// scoped to the administrator's own organization.
export default function OrgStructurePage() {
  const [tab, setTab] = useState("org-units");
  const { user } = useAuth();

  if (!can(user, "admin_panel:access")) {
    return (
      <div className="mx-auto max-w-[1280px] space-y-4">
        <PageHeader
          title="Org structure"
          description="Manage your organization's org-unit hierarchy and role grants."
        />
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="Access restricted"
          description="You don't have permission to view org structure administration. Contact an admin if you need access."
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1280px] space-y-4">
      <PageHeader
        title="Org structure"
        description="Manage your organization's org-unit hierarchy and role grants."
      />
      <Tabs
        tabs={[
          { id: "org-units", label: "Org units" },
          { id: "role-grants", label: "Role grants" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "org-units" && <OrgUnitTree />}
      {tab === "role-grants" && <RoleGrantsPanel />}
    </div>
  );
}
