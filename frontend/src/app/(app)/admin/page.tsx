"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Check,
  Copy,
  Lock,
  Mail,
  Pencil,
  Plug,
  Plus,
  Scale,
  Settings as SettingsIcon,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";
import {
  adminApi,
  authorityApi,
  contractsApi,
  debugApi,
  intakeApi,
  orgApi,
  projectsApi,
  rolesApi,
  usersApi,
  wallsApi,
} from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CardTitle,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  PageHeader,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
  Tabs,
  Textarea,
  useConfirm,
} from "@/components/ui";
import { cn, fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import { useToast } from "@/components/toast";
import type {
  ConfigStatus,
  IntakeTeam,
  PermissionInfo,
  RoleResponse,
  UserInvitationResponse,
  UserResponse,
  WallResponse,
  AuthorityGrantResponse,
} from "@/lib/types";

// Owner-routing vocabulary — shared with the triage (matter categories) and the
// intake form (departments / business units). A team is tagged with the matter
// types it handles (expertise) and the business units it serves (departments).
const MATTER_CATEGORIES = [
  "NDA", "Vendor", "Policy/FAQ", "Contract Review", "Privacy", "Litigation", "Trademark", "General",
];
const BUSINESS_UNITS = [
  "Product", "Engineering", "Sales", "HR", "Finance", "Procurement", "Marketing", "Operations", "Legal", "Executive",
];

// Phase 3 (MAC): confidentiality ladder, low → high.
const CLEARANCE_LEVELS = ["public", "internal", "confidential", "restricted"];
// Phase 4 (DoA/ABAC): gated actions + risk ceiling ladder.
const AUTHORITY_ACTIONS = [
  { value: "contract:approve", label: "Approve" },
  { value: "contract:sign", label: "Sign / send for signature" },
];
const RISK_BANDS = ["low", "medium", "high", "critical"];

export default function AdminPage() {
  const [tab, setTab] = useState("organization");
  const { user } = useAuth();

  if (!can(user, "admin_panel:access")) {
    return (
      <div className="space-y-4">
        <PageHeader title="Admin" description="Organization settings and administration." />
        <EmptyState
          icon={<Lock className="h-6 w-6" />}
          title="Access restricted"
          description="You don't have permission to view organization administration. Contact an admin if you need access."
        />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Admin"
        description="Manage your organization, members, settings and integrations."
      />
      <Tabs
        tabs={[
          { id: "organization", label: "Organization" },
          { id: "users", label: "Users & Access" },
          { id: "roles", label: "Roles & Permissions" },
          { id: "teams", label: "Teams & Routing" },
          { id: "walls", label: "Ethical Walls" },
          { id: "authority", label: "Authority" },
          { id: "settings", label: "Settings" },
          { id: "integrations", label: "Integrations" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "organization" && <OrganizationTab />}
      {tab === "users" && <UsersTab />}
      {tab === "roles" && <RolesTab />}
      {tab === "teams" && <TeamsTab />}
      {tab === "walls" && <EthicalWallsTab />}
      {tab === "authority" && <AuthorityTab />}
      {tab === "settings" && <SettingsTab />}
      {tab === "integrations" && <IntegrationsTab />}
    </div>
  );
}

// ---- Organization --------------------------------------------------------
function OrganizationTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({
    queryKey: ["organization"],
    queryFn: orgApi.current,
  });

  const [name, setName] = useState("");
  const [defaultRole, setDefaultRole] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!data) return;
    setName(data.name);
    setDefaultRole(data.default_role_name);
  }, [data]);

  async function save() {
    setBusy(true);
    try {
      await orgApi.update({
        name: name.trim(),
        default_role_name: defaultRole.trim(),
      });
      qc.invalidateQueries({ queryKey: ["organization"] });
      notify("Organization updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading organization…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <Card className="max-w-2xl">
      <CardHeader>
        <CardTitle>Organization details</CardTitle>
        <Building2 className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardBody className="space-y-4">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Default role" hint="Role assigned to newly approved members.">
          <Input
            placeholder="member"
            value={defaultRole}
            onChange={(e) => setDefaultRole(e.target.value)}
          />
        </Field>
        <div className="flex justify-end">
          <Button onClick={save} loading={busy} disabled={!name.trim()}>
            Save changes
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}

// ---- Users & Access ------------------------------------------------------
function UsersTab() {
  return (
    <div className="space-y-4">
      <PendingUsersSection />
      <InvitationsSection />
    </div>
  );
}

function PendingUsersSection() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["pending-users"],
    queryFn: () => usersApi.list({ status: "pending_approval" }),
  });

  async function decide(
    user: UserResponse,
    decision: "approve" | "reject",
  ) {
    setBusyId(user.id);
    try {
      await usersApi.decideApproval(user.id, decision);
      // Approving flips the user out of pending; refresh this list so the row
      // disappears and the count stays accurate.
      qc.invalidateQueries({ queryKey: ["pending-users"] });
      notify(
        decision === "approve" ? "User approved" : "User rejected",
        "success",
      );
    } catch (e) {
      notify(e instanceof Error ? e.message : "Decision failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pending registrations</CardTitle>
        <UserPlus className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardBody className="p-0">
        {isLoading ? (
          <CenterSpinner label="Loading pending users…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : (data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<UserPlus className="h-6 w-6" />}
              title="No pending registrations"
              description="Self-registered users awaiting approval appear here."
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Email</TH>
                <TH>Full name</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((u) => (
                <TR key={u.id}>
                  <TD className="font-medium text-slate-900">{u.email}</TD>
                  <TD>{u.full_name}</TD>
                  <TD>
                    <Badge tone={statusTone(String(u.status))}>
                      {titleCase(String(u.status))}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-2">
                      <Button
                        size="sm"
                        loading={busyId === u.id}
                        onClick={() => decide(u, "approve")}
                      >
                        Approve
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        loading={busyId === u.id}
                        onClick={() => decide(u, "reject")}
                      >
                        Reject
                      </Button>
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>
    </Card>
  );
}

function InvitationsSection() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  // The just-created invitation (the only time we get its one-time token).
  // We surface a copyable accept link so the inviter can share it directly,
  // whether or not the email went out.
  const [linkInvite, setLinkInvite] = useState<UserInvitationResponse | null>(
    null,
  );
  const [copied, setCopied] = useState(false);

  const inviteLink =
    linkInvite?.token && typeof window !== "undefined"
      ? `${window.location.origin}/invitations/accept?token=${linkInvite.token}`
      : "";

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(inviteLink);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      notify("Couldn't copy — select the link and copy manually", "error");
    }
  }

  const { data, isLoading, error } = useQuery({
    queryKey: ["invitations"],
    queryFn: usersApi.listInvitations,
  });

  function statusOf(inv: UserInvitationResponse): string {
    if (inv.revoked_at) return "revoked";
    if (inv.accepted_at) return "accepted";
    return "pending";
  }

  async function revoke(id: string) {
    setBusyId(id);
    try {
      await usersApi.revokeInvitation(id);
      qc.invalidateQueries({ queryKey: ["invitations"] });
      notify("Invitation revoked", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Revoke failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Invitations</CardTitle>
        <Button size="sm" onClick={() => setInviteOpen(true)}>
          <UserPlus className="h-3.5 w-3.5" />
          Invite user
        </Button>
      </CardHeader>
      {linkInvite && (
        <div className="border-b border-brand-200 bg-brand-50 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-900">
                Invitation created for {linkInvite.email}
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                {linkInvite.email_sent
                  ? "We emailed them the invite link. You can also share it directly:"
                  : "Email wasn't sent (no email service is configured). Share this link with them so they can join:"}
              </p>
            </div>
            <button
              onClick={() => setLinkInvite(null)}
              className="shrink-0 rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              aria-label="Dismiss"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-2.5 flex items-center gap-2">
            <Input
              readOnly
              value={inviteLink}
              className="flex-1 font-mono text-xs"
              onFocus={(e) => e.currentTarget.select()}
            />
            <Button size="sm" variant="outline" onClick={copyLink}>
              {copied ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="mt-2 text-[11px] text-slate-400">
            This link contains a one-time token and is shown only now — it
            expires on {fmtDateTime(linkInvite.expires_at)}.
          </p>
        </div>
      )}
      <CardBody className="p-0">
        {isLoading ? (
          <CenterSpinner label="Loading invitations…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : (data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<Mail className="h-6 w-6" />}
              title="No invitations"
              description="Invite a colleague to join your organization."
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Email</TH>
                <TH>Role</TH>
                <TH>Expires</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((inv) => {
                const status = statusOf(inv);
                return (
                  <TR key={inv.id}>
                    <TD className="font-medium text-slate-900">
                      {inv.email}
                    </TD>
                    <TD>{titleCase(inv.role_name)}</TD>
                    <TD>{fmtDateTime(inv.expires_at)}</TD>
                    <TD>
                      <Badge tone={statusTone(status)}>
                        {titleCase(status)}
                      </Badge>
                    </TD>
                    <TD className="text-right">
                      {status === "pending" ? (
                        <Button
                          size="sm"
                          variant="danger"
                          loading={busyId === inv.id}
                          onClick={() => revoke(inv.id)}
                        >
                          Revoke
                        </Button>
                      ) : (
                        <span className="text-xs text-slate-400">—</span>
                      )}
                    </TD>
                  </TR>
                );
              })}
            </tbody>
          </Table>
        )}
      </CardBody>

      <InviteModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onInvited={(inv) => {
          qc.invalidateQueries({ queryKey: ["invitations"] });
          notify(
            inv.email_sent ? "Invitation emailed" : "Invitation created",
            "success",
          );
          setCopied(false);
          setLinkInvite(inv);
          setInviteOpen(false);
        }}
      />
    </Card>
  );
}

function InviteModal({
  open,
  onClose,
  onInvited,
}: {
  open: boolean;
  onClose: () => void;
  onInvited: (inv: UserInvitationResponse) => void;
}) {
  const { notify } = useToast();
  const [email, setEmail] = useState("");
  const [roleName, setRoleName] = useState("member");
  const [expiresInDays, setExpiresInDays] = useState("7");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!email.trim()) return;
    setBusy(true);
    try {
      const inv = await usersApi.createInvitation(
        email.trim(),
        roleName.trim() || "member",
        Number(expiresInDays) || 7,
      );
      setEmail("");
      setRoleName("member");
      setExpiresInDays("7");
      onInvited(inv);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Invite failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Invite user"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!email.trim()}>
            Send invitation
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Email">
          <Input
            autoFocus
            type="email"
            placeholder="colleague@acme.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field label="Role">
          <Input
            value={roleName}
            onChange={(e) => setRoleName(e.target.value)}
          />
        </Field>
        <Field label="Expires in (days)">
          <Input
            type="number"
            value={expiresInDays}
            onChange={(e) => setExpiresInDays(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

// ---- Settings ------------------------------------------------------------
function SettingsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [upsertOpen, setUpsertOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["admin-settings"],
    queryFn: adminApi.settings,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Settings</CardTitle>
        <Button size="sm" onClick={() => setUpsertOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          Upsert setting
        </Button>
      </CardHeader>
      <CardBody className="p-0">
        {isLoading ? (
          <CenterSpinner label="Loading settings…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : (data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<SettingsIcon className="h-6 w-6" />}
              title="No settings"
              description="Add an organization-level configuration value."
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Key</TH>
                <TH>Value</TH>
                <TH>Secret</TH>
                <TH>Updated</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((s) => (
                <TR key={s.id}>
                  <TD className="font-mono text-xs font-medium text-slate-900">
                    {s.key}
                  </TD>
                  <TD className="max-w-md">
                    <code className="text-xs text-slate-600">
                      {s.is_secret ? "••••••••" : JSON.stringify(s.value)}
                    </code>
                  </TD>
                  <TD>
                    <Badge tone={s.is_secret ? "amber" : "slate"}>
                      {s.is_secret ? "Secret" : "Plain"}
                    </Badge>
                  </TD>
                  <TD>{fmtDateTime(s.updated_at)}</TD>
                </TR>
              ))}
            </tbody>
          </Table>
        )}
      </CardBody>

      <UpsertSettingModal
        open={upsertOpen}
        onClose={() => setUpsertOpen(false)}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["admin-settings"] });
          notify("Setting saved", "success");
          setUpsertOpen(false);
        }}
      />
    </Card>
  );
}

function UpsertSettingModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [isSecret, setIsSecret] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!key.trim()) return;
    setBusy(true);
    let parsed: unknown = value;
    try {
      parsed = JSON.parse(value);
    } catch {
      parsed = value;
    }
    try {
      await adminApi.upsert(key.trim(), parsed, isSecret);
      setKey("");
      setValue("");
      setIsSecret(false);
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Upsert setting"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!key.trim()}>
            Save setting
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Key">
          <Input
            autoFocus
            placeholder="e.g. feature.auto_renewals"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
        </Field>
        <Field
          label="Value"
          hint="Parsed as JSON when valid (e.g. true, 42, [&quot;a&quot;]), otherwise stored as text."
        >
          <Textarea
            rows={3}
            placeholder='true  /  "some string"  /  {"k": 1}'
            value={value}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-200"
            checked={isSecret}
            onChange={(e) => setIsSecret(e.target.checked)}
          />
          Store as secret
        </label>
      </div>
    </Modal>
  );
}

// ---- Integrations --------------------------------------------------------
function IntegrationsTab() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["config-status"],
    queryFn: debugApi.configStatus,
  });

  if (isLoading) return <CenterSpinner label="Loading integrations…" />;
  if (error) return <ErrorState error={error} />;
  if (!data) return null;

  const providers: { key: keyof ConfigStatus; label: string }[] = [
    { key: "claude", label: "Claude (Anthropic)" },
    { key: "reducto", label: "Reducto (OCR)" },
    { key: "resend", label: "Resend (Email)" },
    { key: "docusign", label: "DocuSign (E-sign)" },
  ];

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        {providers.map(({ key, label }) => {
          const cfg = data[key] as { configured: boolean; mock: boolean };
          return (
            <Card key={key}>
              <CardHeader>
                <CardTitle>{label}</CardTitle>
                <Plug className="h-4 w-4 text-slate-400" />
              </CardHeader>
              <CardBody className="flex items-center gap-2">
                <Badge tone={cfg.configured ? "green" : "red"}>
                  {cfg.configured ? "Configured" : "Not configured"}
                </Badge>
                {cfg.mock && <Badge tone="amber">Mock mode</Badge>}
              </CardBody>
            </Card>
          );
        })}
      </div>

      <Card className="max-w-2xl">
        <CardHeader>
          <CardTitle>Environment</CardTitle>
        </CardHeader>
        <CardBody className="space-y-2 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">Storage root</span>
            <span className="font-mono text-xs text-slate-700">
              {data.storage_root}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-500">Debug mode</span>
            <Badge tone={data.debug ? "amber" : "slate"}>
              {data.debug ? "Enabled" : "Disabled"}
            </Badge>
          </div>
        </CardBody>
      </Card>
    </div>
  );
}

// ---- Roles & Permissions -------------------------------------------------

const ADMIN_ROLE = "admin";

function RolesTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const {
    data: roles,
    isLoading,
    error,
  } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: catalog } = useQuery({
    queryKey: ["role-permissions"],
    queryFn: rolesApi.permissions,
  });
  const [editor, setEditor] = useState<{
    mode: "create" | "edit";
    role?: RoleResponse;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  async function remove(role: RoleResponse) {
    const ok = await confirm({
      title: "Delete role",
      message: `Delete the "${titleCase(role.name)}" role? This cannot be undone.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(role.id);
    try {
      await rolesApi.remove(role.id);
      qc.invalidateQueries({ queryKey: ["roles"] });
      notify("Role deleted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Roles</CardTitle>
            <p className="mt-0.5 text-xs text-slate-400">
              Permissions only — what a person is allowed to do. Not the same as{" "}
              <b>Teams</b> (who does the work) or <b>Approver Groups</b> (who signs off).
            </p>
          </div>
          <Button onClick={() => setEditor({ mode: "create" })}>
            <Plus className="h-4 w-4" />
            New role
          </Button>
        </CardHeader>
        <CardBody className="p-0">
          <Table>
            <THead>
              <tr>
                <TH>Role</TH>
                <TH>Permissions</TH>
                <TH>Members</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(roles ?? []).map((r) => (
                <TR key={r.id}>
                  <TD>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-900">
                        {titleCase(r.name)}
                      </span>
                      {r.is_builtin && (
                        <Badge tone="slate">
                          <Lock className="h-3 w-3" />
                          Built-in
                        </Badge>
                      )}
                    </div>
                    {r.description && (
                      <p className="mt-0.5 text-xs text-slate-500">
                        {r.description}
                      </p>
                    )}
                  </TD>
                  <TD className="tabular-nums text-slate-600">
                    {r.permissions.length}
                  </TD>
                  <TD className="tabular-nums text-slate-600">{r.user_count}</TD>
                  <TD className="text-right">
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditor({ mode: "edit", role: r })}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        {r.name === ADMIN_ROLE
                          ? "View"
                          : r.is_builtin
                            ? "Edit permissions"
                            : "Edit"}
                      </Button>
                      {!r.is_builtin && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => remove(r)}
                          loading={busyId === r.id}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Delete
                        </Button>
                      )}
                    </div>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </CardBody>
      </Card>

      <AssignRolesPanel roles={roles ?? []} />

      {editor && (
        <RoleEditorModal
          mode={editor.mode}
          role={editor.role}
          catalog={catalog ?? []}
          onClose={() => setEditor(null)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["roles"] });
            setEditor(null);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}

function RoleEditorModal({
  mode,
  role,
  catalog,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  role?: RoleResponse;
  catalog: PermissionInfo[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [selected, setSelected] = useState<Set<string>>(
    new Set(role?.permissions ?? []),
  );
  const [busy, setBusy] = useState(false);

  const readOnly = role?.name === ADMIN_ROLE; // admin is fully locked
  const nameLocked = !!role?.is_builtin; // built-ins can't be renamed

  const groups = Array.from(
    catalog.reduce((m, p) => {
      (m.get(p.group) ?? m.set(p.group, []).get(p.group)!).push(p);
      return m;
    }, new Map<string, PermissionInfo[]>()),
  );

  function toggle(value: string) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(value) ? next.delete(value) : next.add(value);
      return next;
    });
  }
  function toggleGroup(perms: PermissionInfo[], on: boolean) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of perms) on ? next.add(p.value) : next.delete(p.value);
      return next;
    });
  }

  async function save() {
    if (!name.trim() || readOnly) return;
    setBusy(true);
    try {
      const permissions = [...selected];
      if (mode === "create") {
        await rolesApi.create({
          name: name.trim(),
          description: description.trim() || null,
          permissions,
        });
        notify("Role created", "success");
      } else if (role) {
        await rolesApi.update(role.id, {
          name: nameLocked ? undefined : name.trim(),
          description: description.trim() || null,
          permissions,
        });
        notify("Role updated", "success");
      }
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={
        mode === "create"
          ? "New role"
          : readOnly
            ? `${titleCase(role!.name)} (built-in)`
            : `Edit ${titleCase(role!.name)}`
      }
      size="lg"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            {readOnly ? "Close" : "Cancel"}
          </Button>
          {!readOnly && (
            <Button onClick={save} loading={busy} disabled={!name.trim()}>
              {mode === "create" ? "Create role" : "Save changes"}
            </Button>
          )}
        </>
      }
    >
      <div className="space-y-4">
        {readOnly && (
          <p className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
            The built-in <strong>admin</strong> role always has every permission
            and can’t be changed — it’s your break-glass access.
          </p>
        )}
        <Field label="Name">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={nameLocked || readOnly}
            placeholder="e.g. Paralegal, Outside Counsel"
          />
        </Field>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={readOnly}
          />
        </Field>
        <div>
          <p className="mb-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            Permissions ({selected.size})
          </p>
          <div className="max-h-[46vh] space-y-4 overflow-y-auto rounded-md border border-slate-200 p-3">
            {groups.map(([group, perms]) => {
              const all = perms.every((p) => selected.has(p.value));
              return (
                <div key={group}>
                  <div className="mb-1.5 flex items-center justify-between">
                    <span className="text-sm font-semibold capitalize text-slate-800">
                      {group.replace(/_/g, " ")}
                    </span>
                    {!readOnly && (
                      <button
                        type="button"
                        onClick={() => toggleGroup(perms, !all)}
                        className="text-xs font-medium text-brand-600 hover:text-brand-700"
                      >
                        {all ? "Clear" : "Select all"}
                      </button>
                    )}
                  </div>
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {perms.map((p) => (
                      <label
                        key={p.value}
                        className={cn(
                          "flex items-start gap-2 rounded-md px-2 py-1.5 text-sm",
                          !readOnly && "cursor-pointer hover:bg-slate-50",
                        )}
                      >
                        <input
                          type="checkbox"
                          className="mt-0.5 h-4 w-4 accent-brand-600"
                          checked={selected.has(p.value)}
                          onChange={() => toggle(p.value)}
                          disabled={readOnly}
                        />
                        <span>
                          <span className="font-mono text-xs text-slate-700">
                            {p.value}
                          </span>
                          {p.description && (
                            <span className="block text-xs text-slate-400">
                              {p.description}
                            </span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function AssignRolesPanel({ roles }: { roles: RoleResponse[] }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const [userId, setUserId] = useState("");
  const [roleIds, setRoleIds] = useState<Set<string>>(new Set());
  const [activeRoleId, setActiveRoleId] = useState<string>("");
  const [clearance, setClearance] = useState("confidential");
  const [busy, setBusy] = useState(false);

  const roleByName = new Map(roles.map((r) => [r.name, r]));
  const selectedUser = (users ?? []).find((u) => u.id === userId);

  function pickUser(id: string) {
    setUserId(id);
    const u = (users ?? []).find((x) => x.id === id);
    const ids = new Set(
      (u?.roles ?? []).map((n) => roleByName.get(n)?.id).filter(Boolean) as string[],
    );
    setRoleIds(ids);
    setActiveRoleId(u?.active_role_id ?? "");
    setClearance(u?.clearance ?? "confidential");
  }
  function toggleRole(id: string) {
    setRoleIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      if (!next.has(activeRoleId)) setActiveRoleId([...next][0] ?? "");
      return next;
    });
  }

  async function save() {
    if (!userId || roleIds.size === 0) return;
    setBusy(true);
    try {
      await rolesApi.setUserRoles(userId, {
        role_ids: [...roleIds],
        active_role_id: activeRoleId || null,
      });
      if (selectedUser && clearance !== selectedUser.clearance) {
        await rolesApi.setUserClearance(userId, clearance);
      }
      qc.invalidateQueries({ queryKey: ["roles"] });
      qc.invalidateQueries({ queryKey: ["org-users-all"] });
      notify("Roles updated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Assign roles to a user</CardTitle>
      </CardHeader>
      <CardBody className="space-y-4">
        <Field label="User">
          <Select value={userId} onChange={(e) => pickUser(e.target.value)}>
            <option value="">Select a user…</option>
            {(users ?? []).map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name} · {u.email}
              </option>
            ))}
          </Select>
        </Field>
        {selectedUser && (
          <>
            <div>
              <p className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                Roles
              </p>
              <div className="grid gap-1.5 sm:grid-cols-2">
                {roles.map((r) => (
                  <label
                    key={r.id}
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-brand-600"
                      checked={roleIds.has(r.id)}
                      onChange={() => toggleRole(r.id)}
                    />
                    <span className="text-slate-700">{titleCase(r.name)}</span>
                    {r.is_builtin && (
                      <ShieldCheck className="h-3.5 w-3.5 text-slate-300" />
                    )}
                  </label>
                ))}
              </div>
            </div>
            {roleIds.size > 1 && (
              <Field label="Primary (active) role">
                <Select
                  value={activeRoleId}
                  onChange={(e) => setActiveRoleId(e.target.value)}
                >
                  {[...roleIds].map((id) => {
                    const r = roles.find((x) => x.id === id);
                    return (
                      <option key={id} value={id}>
                        {r ? titleCase(r.name) : id}
                      </option>
                    );
                  })}
                </Select>
              </Field>
            )}
            <Field
              label="Confidentiality clearance"
              hint="Highest classification this user may read. Contracts above it are hidden — including from the assistant."
            >
              <Select
                value={clearance}
                onChange={(e) => setClearance(e.target.value)}
              >
                {CLEARANCE_LEVELS.map((lvl) => (
                  <option key={lvl} value={lvl}>
                    {titleCase(lvl)}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex justify-end">
              <Button onClick={save} loading={busy} disabled={roleIds.size === 0}>
                Save roles
              </Button>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

// ---- Ethical walls -------------------------------------------------------
function EthicalWallsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const {
    data: walls,
    isLoading,
    error,
  } = useQuery({ queryKey: ["ethical-walls"], queryFn: wallsApi.list });
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  function refresh() {
    qc.invalidateQueries({ queryKey: ["ethical-walls"] });
  }

  async function toggleActive(w: WallResponse) {
    setBusyId(w.id);
    try {
      await wallsApi.update(w.id, { active: !w.active });
      refresh();
      notify(w.active ? "Wall lifted" : "Wall re-activated", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(w: WallResponse) {
    const ok = await confirm({
      title: "Delete ethical wall",
      message: `Delete the "${w.name}" ethical wall? This cannot be undone.`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(w.id);
    try {
      await wallsApi.remove(w.id);
      refresh();
      notify("Wall deleted", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Ethical walls</CardTitle>
            <p className="mt-0.5 text-xs text-slate-500">
              Conflict-of-interest screens. A wall hard-blocks the named people
              from a contract or matter — overriding ownership, sharing and admin
              alike, including in search and the assistant.
            </p>
          </div>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New wall
          </Button>
        </CardHeader>
        <CardBody className="p-0">
          {(walls ?? []).length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<ShieldAlert className="h-5 w-5" />}
                title="No ethical walls"
                description="Screens you create to seal conflicted people off from a matter will appear here."
              />
            </div>
          ) : (
            <Table>
              <THead>
                <tr>
                  <TH>Wall</TH>
                  <TH>Scope</TH>
                  <TH>Barred</TH>
                  <TH>Status</TH>
                  <TH className="text-right">Actions</TH>
                </tr>
              </THead>
              <tbody>
                {(walls ?? []).map((w) => (
                  <TR key={w.id}>
                    <TD>
                      <div className="flex items-center gap-2">
                        <ShieldAlert className="h-4 w-4 text-danger" />
                        <span className="font-medium text-slate-900">{w.name}</span>
                      </div>
                      {w.reason && (
                        <p className="mt-0.5 text-xs text-slate-500">{w.reason}</p>
                      )}
                    </TD>
                    <TD>
                      <Badge tone="slate">{titleCase(w.scope_type)}</Badge>
                      <span className="ml-2 text-slate-600">
                        {w.scope_label ?? w.scope_id}
                      </span>
                    </TD>
                    <TD>
                      <div className="flex flex-wrap gap-1">
                        {w.principals.map((p) => (
                          <Badge key={p.id ?? p.principal_id} tone="red">
                            {p.principal_type === "role" ? "Role: " : ""}
                            {p.principal_label ?? p.principal_id}
                          </Badge>
                        ))}
                      </div>
                    </TD>
                    <TD>
                      <Badge tone={w.active ? "green" : "slate"}>
                        {w.active ? "Active" : "Lifted"}
                      </Badge>
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => toggleActive(w)}
                          loading={busyId === w.id}
                        >
                          {w.active ? "Lift" : "Re-activate"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => remove(w)}
                          loading={busyId === w.id}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          Delete
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </tbody>
            </Table>
          )}
        </CardBody>
      </Card>

      {creating && (
        <WallEditorModal
          onClose={() => setCreating(false)}
          onSaved={() => {
            refresh();
            setCreating(false);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}

function WallEditorModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
  const { data: contracts } = useQuery({
    queryKey: ["contracts-all"],
    queryFn: contractsApi.list,
  });
  const { data: projects } = useQuery({
    queryKey: ["projects-all"],
    queryFn: projectsApi.list,
  });

  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [scopeType, setScopeType] = useState<"contract" | "project">("contract");
  const [scopeId, setScopeId] = useState("");
  const [barred, setBarred] = useState<Set<string>>(new Set()); // "user:<id>" | "role:<id>"
  const [busy, setBusy] = useState(false);

  const scopeOptions =
    scopeType === "contract"
      ? (contracts ?? []).map((c) => ({ id: c.id, label: c.title }))
      : (projects ?? []).map((p) => ({ id: p.id, label: p.name }));

  function toggle(key: string) {
    setBarred((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  }

  const canSave = Boolean(name.trim() && scopeId && barred.size > 0);

  async function save() {
    if (!canSave) return;
    setBusy(true);
    try {
      await wallsApi.create({
        name: name.trim(),
        reason: reason.trim() || null,
        scope_type: scopeType,
        scope_id: scopeId,
        principals: [...barred].map((k) => {
          const [t, id] = k.split(":");
          return { principal_type: t as "user" | "role", principal_id: id };
        }),
      });
      notify("Ethical wall created", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="New ethical wall" size="lg">
      <div className="space-y-4">
        <Field label="Name">
          <Input
            placeholder="e.g. Acme v. Globex conflict screen"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Reason" hint="Optional — recorded on the audit trail.">
          <Textarea
            rows={2}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Scope">
            <Select
              value={scopeType}
              onChange={(e) => {
                setScopeType(e.target.value as "contract" | "project");
                setScopeId("");
              }}
            >
              <option value="contract">A single contract</option>
              <option value="project">An entire matter / project</option>
            </Select>
          </Field>
          <Field label={scopeType === "contract" ? "Contract" : "Project"}>
            <Select value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
              <option value="">Select…</option>
              {scopeOptions.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.label}
                </option>
              ))}
            </Select>
          </Field>
        </div>
        <div>
          <p className="mb-1.5 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
            Bar these people
          </p>
          <div className="max-h-52 overflow-y-auto rounded-md border border-slate-200 p-2">
            <div className="grid gap-1 sm:grid-cols-2">
              {(users ?? []).map((u) => {
                const key = `user:${u.id}`;
                return (
                  <label
                    key={key}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-brand-600"
                      checked={barred.has(key)}
                      onChange={() => toggle(key)}
                    />
                    <span className="text-slate-700">{u.full_name}</span>
                  </label>
                );
              })}
            </div>
            {(roles ?? []).length > 0 && (
              <>
                <p className="mb-1 mt-2 px-2 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                  Or whole roles
                </p>
                <div className="grid gap-1 sm:grid-cols-2">
                  {(roles ?? []).map((r) => {
                    const key = `role:${r.id}`;
                    return (
                      <label
                        key={key}
                        className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-sm hover:bg-slate-50"
                      >
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-brand-600"
                          checked={barred.has(key)}
                          onChange={() => toggle(key)}
                        />
                        <span className="text-slate-700">{titleCase(r.name)}</span>
                      </label>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>
            Create wall
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ---- Authority (Delegation of Authority / ABAC) --------------------------
function fmtLimit(g: AuthorityGrantResponse): string {
  if (g.max_value == null) return "Unlimited value";
  const cur = g.currency ? `${g.currency} ` : "";
  return `≤ ${cur}${g.max_value.toLocaleString()}`;
}

function AuthorityTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const {
    data: grants,
    isLoading,
    error,
  } = useQuery({ queryKey: ["authority-grants"], queryFn: authorityApi.list });
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  function refresh() {
    qc.invalidateQueries({ queryKey: ["authority-grants"] });
  }

  async function revoke(g: AuthorityGrantResponse) {
    const ok = await confirm({
      title: "Revoke authority",
      message: `Revoke this authority for ${g.principal_label}?`,
      confirmLabel: "Revoke",
      tone: "danger",
    });
    if (!ok) return;
    setBusyId(g.id);
    try {
      await authorityApi.revoke(g.id);
      refresh();
      notify("Authority revoked", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Revoke failed", "error");
    } finally {
      setBusyId(null);
    }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  const byAction = AUTHORITY_ACTIONS.map((a) => ({
    ...a,
    rows: (grants ?? []).filter((g) => g.action === a.value),
  }));

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Delegation of authority</CardTitle>
            <p className="mt-0.5 text-xs text-slate-500">
              Who may commit the company, and up to what limit. Approving or
              sending for signature is blocked when a contract’s value, type,
              jurisdiction or risk exceeds the actor’s authority. An action is
              only enforced once you define a policy for it.
            </p>
          </div>
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New authority
          </Button>
        </CardHeader>
        <CardBody className="space-y-5">
          {byAction.map((a) => (
            <div key={a.value}>
              <div className="mb-2 flex items-center gap-2">
                <Scale className="h-4 w-4 text-slate-400" />
                <span className="text-sm font-semibold text-slate-800">
                  {a.label}
                </span>
                <Badge tone={a.rows.length ? "blue" : "slate"}>
                  {a.rows.length ? "Gated" : "Not gated"}
                </Badge>
              </div>
              {a.rows.length === 0 ? (
                <p className="rounded-md border border-dashed border-slate-200 px-3 py-2 text-xs text-slate-500">
                  No policy — this action is governed by role permissions alone.
                </p>
              ) : (
                <Table>
                  <THead>
                    <tr>
                      <TH>Who</TH>
                      <TH>Limit</TH>
                      <TH>Scope</TH>
                      <TH className="text-right">Actions</TH>
                    </tr>
                  </THead>
                  <tbody>
                    {a.rows.map((g) => (
                      <TR key={g.id}>
                        <TD>
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-slate-900">
                              {g.principal_label}
                            </span>
                            <Badge tone="slate">
                              {g.principal_type === "role" ? "Role" : "Person"}
                            </Badge>
                            {g.delegated_by_label && (
                              <Badge tone="violet">
                                Delegated by {g.delegated_by_label}
                              </Badge>
                            )}
                          </div>
                        </TD>
                        <TD className="text-slate-700">{fmtLimit(g)}</TD>
                        <TD className="text-xs text-slate-600">
                          {[
                            g.allowed_contract_types.length
                              ? `Types: ${g.allowed_contract_types.join(", ")}`
                              : null,
                            g.allowed_jurisdictions.length
                              ? `Juris: ${g.allowed_jurisdictions.join(", ")}`
                              : null,
                            g.max_risk_band ? `Risk ≤ ${g.max_risk_band}` : null,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "Any type / jurisdiction / risk"}
                        </TD>
                        <TD className="text-right">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => revoke(g)}
                            loading={busyId === g.id}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Revoke
                          </Button>
                        </TD>
                      </TR>
                    ))}
                  </tbody>
                </Table>
              )}
            </div>
          ))}
        </CardBody>
      </Card>

      {creating && (
        <AuthorityEditorModal
          onClose={() => setCreating(false)}
          onSaved={() => {
            refresh();
            setCreating(false);
          }}
        />
      )}
      {confirmDialog}
    </div>
  );
}

function AuthorityEditorModal({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });

  const [action, setAction] = useState("contract:approve");
  const [principalType, setPrincipalType] = useState<"user" | "role">("role");
  const [principalId, setPrincipalId] = useState("");
  const [maxValue, setMaxValue] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [types, setTypes] = useState("");
  const [jurisdictions, setJurisdictions] = useState("");
  const [maxRisk, setMaxRisk] = useState("");
  const [busy, setBusy] = useState(false);

  const principals =
    principalType === "user"
      ? (users ?? []).map((u) => ({ id: u.id, label: `${u.full_name} · ${u.email}` }))
      : (roles ?? []).map((r) => ({ id: r.id, label: titleCase(r.name) }));

  const canSave = Boolean(principalId);

  function splitList(s: string): string[] {
    return s
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
  }

  async function save() {
    if (!canSave) return;
    setBusy(true);
    try {
      await authorityApi.create({
        principal_type: principalType,
        principal_id: principalId,
        action: action as "contract:approve" | "contract:sign",
        max_value: maxValue.trim() ? Number(maxValue) : null,
        currency: currency.trim() || null,
        allowed_contract_types: splitList(types),
        allowed_jurisdictions: splitList(jurisdictions),
        max_risk_band: maxRisk || null,
      });
      notify("Authority granted", "success");
      onSaved();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open onClose={onClose} title="New delegation of authority" size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Action">
            <Select value={action} onChange={(e) => setAction(e.target.value)}>
              {AUTHORITY_ACTIONS.map((a) => (
                <option key={a.value} value={a.value}>
                  {a.label}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Grant to">
            <Select
              value={principalType}
              onChange={(e) => {
                setPrincipalType(e.target.value as "user" | "role");
                setPrincipalId("");
              }}
            >
              <option value="role">A role</option>
              <option value="user">A person</option>
            </Select>
          </Field>
        </div>
        <Field label={principalType === "role" ? "Role" : "Person"}>
          <Select value={principalId} onChange={(e) => setPrincipalId(e.target.value)}>
            <option value="">Select…</option>
            {principals.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field
            label="Max value"
            hint="Blank = unlimited"
            className="sm:col-span-2"
          >
            <Input
              type="number"
              placeholder="e.g. 100000"
              value={maxValue}
              onChange={(e) => setMaxValue(e.target.value)}
            />
          </Field>
          <Field label="Currency">
            <Input
              maxLength={3}
              value={currency}
              onChange={(e) => setCurrency(e.target.value.toUpperCase())}
            />
          </Field>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Contract types" hint="Comma-separated. Blank = any.">
            <Input
              placeholder="NDA, MSA"
              value={types}
              onChange={(e) => setTypes(e.target.value)}
            />
          </Field>
          <Field label="Jurisdictions" hint="Comma-separated. Blank = any.">
            <Input
              placeholder="US, UK"
              value={jurisdictions}
              onChange={(e) => setJurisdictions(e.target.value)}
            />
          </Field>
        </div>
        <Field label="Max risk band" hint="Blank = any risk.">
          <Select value={maxRisk} onChange={(e) => setMaxRisk(e.target.value)}>
            <option value="">Any</option>
            {RISK_BANDS.map((r) => (
              <option key={r} value={r}>
                {titleCase(r)}
              </option>
            ))}
          </Select>
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={save} loading={busy} disabled={!canSave}>
            Grant authority
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ---- Teams & Routing (intake owner assignment) ---------------------------

function Chips({ options, selected, onToggle }: {
  options: string[]; selected: string[]; onToggle: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => {
        const on = selected.includes(o);
        return (
          <button
            key={o}
            type="button"
            onClick={() => onToggle(o)}
            className={cn(
              "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
              on
                ? "border-brand-500 bg-brand-50 text-brand-700"
                : "border-slate-200 bg-white text-slate-500 hover:border-slate-300",
            )}
          >
            {o}
          </button>
        );
      })}
    </div>
  );
}

function TeamsTab() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { confirm, dialog: confirmDialog } = useConfirm();
  const { data: teams, isLoading, error } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => usersApi.list() });
  const [editing, setEditing] = useState<IntakeTeam | "new" | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["intake-teams"] });
  const del = useMutation({
    mutationFn: (id: string) => intakeApi.deleteTeam(id),
    onSuccess: () => { invalidate(); notify("Team deleted", "success"); },
    onError: (e) => notify(e instanceof Error ? e.message : "Delete failed", "error"),
  });

  if (isLoading) return <CenterSpinner label="Loading teams…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Teams &amp; Routing</CardTitle>
              <p className="mt-1 max-w-2xl text-sm text-slate-500">
                When a request is raised, the triage assigns its owner to the team whose{" "}
                <b>expertise</b> covers the matter type and that <b>serves the requester&apos;s business
                unit</b>, load-balanced by member capacity. Untagged teams act as the fallback.
              </p>
            </div>
            <Button onClick={() => setEditing("new")}>
              <Plus className="h-4 w-4" /> New team
            </Button>
          </div>
        </CardHeader>
        <CardBody className="space-y-3">
          {/* Orientation: the three people-lists are easy to confuse — spell out
              what each is FOR and where it lives, so this screen is unambiguous. */}
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs">
            <div className="mb-2 font-semibold text-slate-600">Three separate lists — each answers one question:</div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <div className="font-medium text-slate-700">Roles <span className="font-normal text-slate-400">· Admin → Roles</span></div>
                <div className="text-slate-500">What a person is <b>allowed to do</b> (permissions).</div>
              </div>
              <div>
                <div className="font-medium text-slate-700">Approver Groups <span className="font-normal text-slate-400">· Approvals</span></div>
                <div className="text-slate-500">Who <b>signs off</b> on an approval step.</div>
              </div>
              <div>
                <div className="font-medium text-brand-700">Teams <span className="font-normal text-slate-400">· you are here</span></div>
                <div className="text-slate-500">Who gets <b>assigned the work</b>. Only this list drives triage routing.</div>
              </div>
            </div>
          </div>
          {(teams ?? []).length === 0 ? (
            <EmptyState
              title="No teams yet"
              description="Create a team and tag it with the matter types it handles and the business units it serves."
            />
          ) : (
            (teams ?? []).map((t) => (
              <div key={t.id} className="rounded-lg border border-slate-200 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-slate-900">{t.name}</span>
                      <Badge tone="slate">{t.strategy === "round_robin" ? "round-robin" : "least-loaded"}</Badge>
                      {!t.active && <Badge tone="amber">inactive</Badge>}
                    </div>
                    <div className="flex flex-wrap gap-x-8 gap-y-2 text-xs">
                      <div>
                        <div className="mb-1 font-medium uppercase tracking-wide text-slate-400">Expertise</div>
                        <div className="flex flex-wrap gap-1">
                          {t.expertise.length
                            ? t.expertise.map((e) => <Badge key={e} tone="green">{e}</Badge>)
                            : <span className="text-slate-400">—</span>}
                        </div>
                      </div>
                      <div>
                        <div className="mb-1 font-medium uppercase tracking-wide text-slate-400">Serves (business units)</div>
                        <div className="flex flex-wrap gap-1">
                          {t.departments.length
                            ? t.departments.map((d) => <Badge key={d} tone="blue">{d}</Badge>)
                            : <span className="text-slate-400">all</span>}
                        </div>
                      </div>
                    </div>
                    <div className="text-xs text-slate-500">
                      {t.members.length} member{t.members.length === 1 ? "" : "s"}
                      {t.members.length > 0 && (
                        <>: {t.members.map((m) => `${m.name ?? m.user_id} (${m.open_count ?? 0}/${m.capacity})`).join(", ")}</>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(t)}>
                      <Pencil className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={async () => {
                        const ok = await confirm({
                          title: `Delete ${t.name}?`,
                          message: "Requests already assigned stay put; this only removes the pool.",
                          confirmLabel: "Delete",
                          tone: "danger",
                        });
                        if (ok) del.mutate(t.id);
                      }}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              </div>
            ))
          )}
        </CardBody>
      </Card>
      {editing && (
        <TeamModal
          team={editing === "new" ? null : editing}
          users={users ?? []}
          teams={teams ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); invalidate(); }}
        />
      )}
      {confirmDialog}
    </div>
  );
}

function TeamModal({ team, users, teams, onClose, onSaved }: {
  team: IntakeTeam | null;
  users: UserResponse[];
  teams: IntakeTeam[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState(team?.name ?? "");
  const [key, setKey] = useState(team?.key ?? "");
  const [strategy, setStrategy] = useState(team?.strategy ?? "least_loaded");
  const [expertise, setExpertise] = useState<string[]>(team?.expertise ?? []);
  const [departments, setDepartments] = useState<string[]>(team?.departments ?? []);
  const [overflow, setOverflow] = useState(team?.overflow_team_id ?? "");
  const [members, setMembers] = useState<{ user_id: string; capacity: number }[]>(
    (team?.members ?? []).map((m) => ({ user_id: m.user_id, capacity: m.capacity })),
  );

  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  const memberIds = new Set(members.map((m) => m.user_id));
  const addable = users.filter((u) => !memberIds.has(u.id));

  const save = useMutation({
    mutationFn: () => {
      const payload = {
        name: name.trim(),
        strategy,
        expertise,
        departments,
        overflow_team_id: overflow || null,
        members: members.filter((m) => m.user_id),
      };
      return team
        ? intakeApi.updateTeam(team.id, payload)
        : intakeApi.createTeam({ ...payload, key: key.trim().toLowerCase() });
    },
    onSuccess: () => { notify(team ? "Team updated" : "Team created", "success"); onSaved(); },
    onError: (e) => notify(e instanceof Error ? e.message : "Save failed", "error"),
  });

  const canSave = name.trim() && (team || key.trim());

  return (
    <Modal open onClose={onClose} title={team ? `Edit ${team.name}` : "New team"} size="lg">
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Commercial Legal" />
          </Field>
          {team ? (
            <Field label="Key"><Input value={team.key} disabled /></Field>
          ) : (
            <Field label="Key" hint="lowercase id, e.g. commercial">
              <Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="commercial" />
            </Field>
          )}
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Balancing">
            <Select value={strategy} onChange={(e) => setStrategy(e.target.value as IntakeTeam["strategy"])}>
              <option value="least_loaded">Least loaded</option>
              <option value="round_robin">Round robin</option>
            </Select>
          </Field>
          <Field label="Overflow team" hint="Where work spills when everyone's full.">
            <Select value={overflow} onChange={(e) => setOverflow(e.target.value)}>
              <option value="">None</option>
              {teams.filter((t) => t.id !== team?.id).map((t) => (
                <option key={t.id} value={t.id}>{t.name}</option>
              ))}
            </Select>
          </Field>
        </div>
        <Field label="Expertise" hint="Matter types this team handles. The triage routes a request here when its category matches.">
          <Chips options={MATTER_CATEGORIES} selected={expertise} onToggle={(v) => toggle(expertise, setExpertise, v)} />
        </Field>
        <Field label="Serves (business units)" hint="Requesting departments this team supports. Blank = serves all.">
          <Chips options={BUSINESS_UNITS} selected={departments} onToggle={(v) => toggle(departments, setDepartments, v)} />
        </Field>
        <Field label="Members">
          <div className="space-y-2">
            {members.map((m, i) => {
              const u = users.find((x) => x.id === m.user_id);
              return (
                <div key={m.user_id} className="flex items-center gap-2">
                  <span className="flex-1 truncate text-sm text-slate-700">{u ? u.full_name : m.user_id}</span>
                  <span className="text-xs text-slate-400">capacity</span>
                  <Input
                    type="number"
                    className="w-20"
                    value={String(m.capacity)}
                    onChange={(e) =>
                      setMembers(members.map((x, j) => (j === i ? { ...x, capacity: Number(e.target.value) || 0 } : x)))
                    }
                  />
                  <Button variant="ghost" size="sm" onClick={() => setMembers(members.filter((_, j) => j !== i))}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              );
            })}
            {addable.length > 0 && (
              <Select
                value=""
                onChange={(e) => {
                  if (e.target.value) setMembers([...members, { user_id: e.target.value, capacity: 8 }]);
                }}
              >
                <option value="">+ Add member…</option>
                {addable.map((u) => <option key={u.id} value={u.id}>{u.full_name}</option>)}
              </Select>
            )}
          </div>
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!canSave}>
            {team ? "Save" : "Create team"}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
