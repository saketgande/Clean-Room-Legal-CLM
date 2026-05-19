"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  KeyRound,
  Mail,
  Plug,
  Plus,
  Settings as SettingsIcon,
  UserPlus,
} from "lucide-react";
import { adminApi, debugApi, orgApi, usersApi } from "@/lib/endpoints";
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
  Table,
  TD,
  TH,
  THead,
  TR,
  Tabs,
  Textarea,
} from "@/components/ui";
import { fmtDateTime, statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type {
  ConfigStatus,
  OrgJoinRequestResponse,
  UserInvitationResponse,
} from "@/lib/types";

export default function AdminPage() {
  const [tab, setTab] = useState("organization");

  return (
    <div className="space-y-6">
      <PageHeader
        title="Admin"
        description="Manage your organization, members, settings and integrations."
      />
      <Tabs
        tabs={[
          { id: "organization", label: "Organization" },
          { id: "users", label: "Users & Access" },
          { id: "settings", label: "Settings" },
          { id: "integrations", label: "Integrations" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "organization" && <OrganizationTab />}
      {tab === "users" && <UsersTab />}
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
  const [domains, setDomains] = useState("");
  const [defaultRole, setDefaultRole] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!data) return;
    setName(data.name);
    setDomains(data.allowed_domains.join(", "));
    setDefaultRole(data.default_role_name);
  }, [data]);

  async function save() {
    setBusy(true);
    try {
      await orgApi.update({
        name: name.trim(),
        allowed_domains: domains
          .split(",")
          .map((d) => d.trim())
          .filter(Boolean),
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
        <Field
          label="Allowed domains"
          hint="Comma-separated — users with these email domains can request to join."
        >
          <Input
            placeholder="acme.com, acme.co.uk"
            value={domains}
            onChange={(e) => setDomains(e.target.value)}
          />
        </Field>
        <Field label="Default role" hint="Role assigned to newly approved members.">
          <Input
            placeholder="member"
            value={defaultRole}
            onChange={(e) => setDefaultRole(e.target.value)}
          />
        </Field>
        {data && (
          <p className="text-xs text-slate-400">
            Slug: <span className="font-mono">{data.slug}</span>
          </p>
        )}
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
    <div className="space-y-6">
      <InvitationsSection />
      <JoinRequestsSection />
    </div>
  );
}

function InvitationsSection() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

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
        onInvited={() => {
          qc.invalidateQueries({ queryKey: ["invitations"] });
          notify("Invitation sent", "success");
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
  onInvited: () => void;
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
      await usersApi.createInvitation(
        email.trim(),
        roleName.trim() || "member",
        Number(expiresInDays) || 7,
      );
      setEmail("");
      setRoleName("member");
      setExpiresInDays("7");
      onInvited();
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

function JoinRequestsSection() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [busyId, setBusyId] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["join-requests"],
    queryFn: usersApi.listJoinRequests,
  });

  async function decide(
    req: OrgJoinRequestResponse,
    decision: "approve" | "reject",
  ) {
    setBusyId(req.id);
    try {
      await usersApi.decideJoinRequest(req.id, decision);
      qc.invalidateQueries({ queryKey: ["join-requests"] });
      notify(
        decision === "approve" ? "Request approved" : "Request rejected",
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
        <CardTitle>Join requests</CardTitle>
        <KeyRound className="h-4 w-4 text-slate-400" />
      </CardHeader>
      <CardBody className="p-0">
        {isLoading ? (
          <CenterSpinner label="Loading join requests…" />
        ) : error ? (
          <div className="p-5">
            <ErrorState error={error} />
          </div>
        ) : (data ?? []).length === 0 ? (
          <div className="p-5">
            <EmptyState
              icon={<KeyRound className="h-6 w-6" />}
              title="No join requests"
              description="Pending requests to join your organization appear here."
            />
          </div>
        ) : (
          <Table>
            <THead>
              <tr>
                <TH>Email</TH>
                <TH>Full name</TH>
                <TH>Requested domain</TH>
                <TH>Status</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((req) => (
                <TR key={req.id}>
                  <TD className="font-medium text-slate-900">{req.email}</TD>
                  <TD>{req.full_name}</TD>
                  <TD>{req.requested_domain ?? "—"}</TD>
                  <TD>
                    <Badge tone={statusTone(req.status)}>
                      {titleCase(req.status)}
                    </Badge>
                  </TD>
                  <TD className="text-right">
                    {req.status === "pending" ? (
                      <div className="flex justify-end gap-2">
                        <Button
                          size="sm"
                          loading={busyId === req.id}
                          onClick={() => decide(req, "approve")}
                        >
                          Approve
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          loading={busyId === req.id}
                          onClick={() => decide(req, "reject")}
                        >
                          Reject
                        </Button>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
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
            className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
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
