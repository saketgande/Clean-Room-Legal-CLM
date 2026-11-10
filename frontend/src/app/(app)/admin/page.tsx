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
  Contact,
  KeyRound,
  Users,
  UsersRound,
} from "lucide-react";
import {
  adminApi,
  authorityApi,
  contractsApi,
  debugApi,
  intakeApi,
  orgApi,
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
import { RolesView, TeamsView } from "./_roles-teams";
import { AdBtn, AdminShell, Empty, Pill, Search, SecHead, Seg, Stats, type NavGroup } from "./_admin-kit";
import { Switch, Tile } from "./_atoms";
import { AssignUI, PeopleList, RoleEditorUI, TeamEditorUI } from "./_roles-teams-forms";
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
import { PartiesRegisterTab } from "./_parties-register";

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

const NAV: NavGroup[] = [
  {
    title: "People & access",
    items: [
      { id: "users", label: "Users & Access", hint: "Who is in, invitations and approvals", icon: Users },
      { id: "roles", label: "Roles & Permissions", hint: "What each role may do", icon: KeyRound },
      { id: "teams", label: "Teams", hint: "Who does the work", icon: UsersRound },
      { id: "authority", label: "Authority", hint: "How much someone may approve or sign", icon: Scale },
      { id: "walls", label: "Ethical Walls", hint: "Conflict-of-interest screens", icon: ShieldAlert },
    ],
  },
  {
    title: "Organization",
    items: [
      { id: "organization", label: "Organization", hint: "Name and defaults", icon: Building2 },
      { id: "parties", label: "Entities & counterparties", hint: "Our entities and the other side", icon: Contact },
    ],
  },
  {
    title: "System",
    items: [
      { id: "settings", label: "Settings", hint: "Organization-level values", icon: SettingsIcon },
      { id: "integrations", label: "Integrations", hint: "Connected services and environment", icon: Plug },
    ],
  },
];

export default function AdminPage() {
  const [tab, setTab] = useState("organization");
  const { user } = useAuth();

  if (!can(user, "admin_panel:access")) {
    return (
      <div className="mx-auto max-w-[1280px] space-y-4">
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
    <AdminShell groups={NAV} active={tab} onChange={setTab}>
      {tab === "organization" && <OrganizationTab />}
      {tab === "users" && <UsersTab />}
      {tab === "roles" && <RolesTab />}
      {tab === "teams" && <TeamsTab />}
      {tab === "parties" && <PartiesRegisterTab />}
      {tab === "walls" && <EthicalWallsTab />}
      {tab === "authority" && <AuthorityTab />}
      {tab === "settings" && <SettingsTab />}
      {tab === "integrations" && <IntegrationsTab />}
    </AdminShell>
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

  const { data: roles } = useQuery({ queryKey: ["roles"], queryFn: rolesApi.list });
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

  if (isLoading) return <div className="ad-load"><span className="ad-spin" /></div>;
  if (error) return <ErrorState error={error} />;

  const dirty = !!data && (name.trim() !== data.name || defaultRole.trim() !== data.default_role_name);
  return (
    <>
      <SecHead title="Organization" desc="Your organization's name and what newly approved members start with." />
      <section className="ad-card" style={{ maxWidth: 680 }}>
        <div className="ad-org">
          <Tile name={name || "Org"} size={40} />
          <div><b>{name || "Unnamed organization"}</b><small>New members start as <em>{defaultRole || "member"}</em></small></div>
        </div>
        <label className="ad-field">Name
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="ad-field">Default role
          <small>The role given to people when you approve their registration.</small>
          <div className="ad-chips" role="group" aria-label="Default role">
            {(roles ?? []).map((r) => (
              <button key={r.id} type="button" className={defaultRole === r.name ? "on" : ""} aria-pressed={defaultRole === r.name} onClick={() => setDefaultRole(r.name)}>{titleCase(r.name)}</button>
            ))}
            {defaultRole && !(roles ?? []).some((r) => r.name === defaultRole) && <button type="button" className="on" aria-pressed>{defaultRole}</button>}
          </div>
        </div>
        <div className="ad-actions">
          {dirty && <span className="ad-dirty">Unsaved changes</span>}
          <AdBtn kind="primary" onClick={save} disabled={busy || !dirty || !name.trim()}>{busy ? "Saving…" : "Save changes"}</AdBtn>
        </div>
      </section>
    </>
  );
}

// ---- Users & Access ------------------------------------------------------
function UsersTab() {
  const { data: all } = useQuery({ queryKey: ["all-users"], queryFn: () => usersApi.list() });
  const { data: pending } = useQuery({ queryKey: ["pending-users"], queryFn: () => usersApi.list({ status: "pending_approval" }) });
  const { data: invites } = useQuery({ queryKey: ["invitations"], queryFn: usersApi.listInvitations });
  const open = (invites ?? []).filter((i) => !i.revoked_at && !i.accepted_at).length;
  const active = (all ?? []).filter((u) => String(u.status) === "active").length;
  return (
    <>
      <SecHead title="Users & access" desc="Who is in your organization: approve registrations, invite colleagues and see everyone's role." />
      <Stats items={[
        { n: (all ?? []).length, label: "people" },
        { n: active, label: "active" },
        { n: (pending ?? []).length, label: "waiting for approval", tone: (pending ?? []).length ? "warn" : undefined },
        { n: open, label: "open invitations" },
      ]} />
      <PendingUsersSection />
      <AllUsersSection />
      <InvitationsSection />
    </>
  );
}

function AllUsersSection() {
  const [search, setSearch] = useState("");
  const [seg, setSeg] = useState<"all" | "active" | "other">("all");
  const { data, isLoading, error } = useQuery({
    queryKey: ["all-users"],
    queryFn: () => usersApi.list(),
  });
  const q = search.trim().toLowerCase();
  const rows = (data ?? []).filter(
    (u) => !q || `${u.email} ${u.full_name}`.toLowerCase().includes(q),
  );

  const statusOf = (u: UserResponse) => String(u.status);
  const counts = { all: (data ?? []).length, active: (data ?? []).filter((u) => statusOf(u) === "active").length };
  const shownRows = rows.filter((u) => seg === "all" || (seg === "active" ? statusOf(u) === "active" : statusOf(u) !== "active"));
  return (
    <section className="adm-sec">
      <div className="ad-bar">
        <Seg<"all" | "active" | "other"> label="Filter people" value={seg} onChange={setSeg} items={[{ id: "all", label: "Everyone", n: counts.all }, { id: "active", label: "Active", n: counts.active }, { id: "other", label: "Other", n: counts.all - counts.active }]} />
        <span className="sp" />
        <Search value={search} onChange={setSearch} placeholder="Search name or email…" />
      </div>
      {isLoading ? (
        <div className="ad-load"><span className="ad-spin" /></div>
      ) : error ? (
        <ErrorState error={error} />
      ) : shownRows.length === 0 ? (
        <Empty icon={<UserPlus size={22} />} title={q ? "No one matches" : "No users"} desc={q ? "Try a different name or email." : "Registered users in your organization appear here."} />
      ) : (
        <div className="ad-t" role="table" aria-label="People" style={{ ["--cols" as string]: "minmax(0,2.2fr) minmax(0,2fr) 100px 90px" }}>
          <div className="ad-th" role="row"><span>Person</span><span>Roles</span><span>Clearance</span><span>Status</span></div>
          {shownRows.map((u, i) => (
            <div key={u.id} className="ad-tr" role="row" style={{ ["--i" as string]: Math.min(i, 14) }}>
              <span className="who"><Tile name={u.full_name} size={28} /><span style={{ minWidth: 0 }}><b>{u.full_name}</b><small>{u.email}</small></span></span>
              <span className="wrap">{(u.roles?.length ? u.roles : [u.active_role_name ?? "no role"]).map((r) => <Pill key={r} tone={r === u.active_role_name ? "blue" : "slate"}>{titleCase(r)}</Pill>)}</span>
              <span className="cell">{titleCase(u.clearance ?? "")}</span>
              <Pill tone={statusOf(u) === "active" ? "green" : statusOf(u).includes("pending") ? "amber" : "slate"}>{titleCase(statusOf(u))}</Pill>
            </div>
          ))}
        </div>
      )}
    </section>
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

  const list = data ?? [];
  if (isLoading) return <div className="ad-load"><span className="ad-spin" /></div>;
  if (error) return <ErrorState error={error} />;
  if (list.length === 0) return null;
  return (
    <section className="ad-card ad-attn flush">
      <div className="ad-th" style={{ ["--cols" as string]: "1fr" }}><span>Waiting for your approval ({list.length}). They cannot sign in until you decide.</span></div>
      <ul className="ad-rows">
        {list.map((u, i) => (
          <li key={u.id} className="ad-row" style={{ ["--i" as string]: i }}>
            <Tile name={u.full_name} size={28} />
            <span className="main"><b>{u.full_name}</b><small>{u.email}</small></span>
            <span className="acts">
              <AdBtn kind="primary" className="sm" disabled={busyId === u.id} onClick={() => decide(u, "approve")}>Approve</AdBtn>
              <AdBtn kind="danger" className="sm" disabled={busyId === u.id} onClick={() => decide(u, "reject")}>Reject</AdBtn>
            </span>
          </li>
        ))}
      </ul>
    </section>
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
    <section className="adm-sec">
      <div className="ad-bar">
        <h3 className="ad-subh">Invitations</h3>
        <span className="rt-dim-s">{(data ?? []).length} sent</span>
        <span className="sp" />
        <AdBtn kind="primary" onClick={() => setInviteOpen(true)}><UserPlus size={14} />Invite user</AdBtn>
      </div>
      {linkInvite && (
        <div className="ad-link" role="status">
          <div className="top">
            <div>
              <b>Invitation created for {linkInvite.email}</b>
              <p>{linkInvite.email_sent ? "We emailed them the invite link. You can also share it directly:" : "Email wasn't sent (no email service is configured). Share this link so they can join:"}</p>
            </div>
            <button type="button" className="x" onClick={() => setLinkInvite(null)} aria-label="Dismiss"><X size={16} /></button>
          </div>
          <div className="copy">
            <input readOnly value={inviteLink} onFocus={(e) => e.currentTarget.select()} aria-label="Invitation link" />
            <AdBtn kind="ghost" className="sm" onClick={copyLink}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? "Copied" : "Copy"}</AdBtn>
          </div>
          <small>One-time link, shown only now. It expires on {fmtDateTime(linkInvite.expires_at)}.</small>
        </div>
      )}
      {isLoading ? (
        <div className="ad-load"><span className="ad-spin" /></div>
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <div className="ad-calm"><Mail size={15} />No invitations yet. Invite a colleague to join your organization.</div>
      ) : (
        <div className="ad-t" role="table" aria-label="Invitations" style={{ ["--cols" as string]: "minmax(0,2fr) 120px minmax(0,1.2fr) 90px 80px" }}>
          <div className="ad-th" role="row"><span>Email</span><span>Role</span><span>Expires</span><span>Status</span><span /></div>
          {(data ?? []).map((inv, i) => {
            const status = statusOf(inv);
            return (
              <div key={inv.id} className="ad-tr" role="row" style={{ ["--i" as string]: Math.min(i, 14) }}>
                <span className="cell" style={{ fontWeight: 600, color: "var(--ink)" }}>{inv.email}</span>
                <span className="cell">{titleCase(inv.role_name)}</span>
                <span className="cell">{fmtDateTime(inv.expires_at)}</span>
                <Pill tone={status === "pending" ? "amber" : status === "accepted" ? "green" : "slate"}>{titleCase(status)}</Pill>
                <span className="end">{status === "pending" && <AdBtn kind="danger" className="sm" disabled={busyId === inv.id} onClick={() => revoke(inv.id)}>Revoke</AdBtn>}</span>
              </div>
            );
          })}
        </div>
      )}
      <InviteModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onInvited={(inv) => {
          qc.invalidateQueries({ queryKey: ["invitations"] });
          notify(inv.email_sent ? "Invitation emailed" : "Invitation created", "success");
          setCopied(false);
          setLinkInvite(inv);
          setInviteOpen(false);
        }}
      />
    </section>
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

  const list = data ?? [];
  return (
    <>
      <SecHead title="Settings" desc="Organization-level values the app reads. Secrets are stored hidden and never shown again."
        action={<AdBtn kind="primary" onClick={() => setUpsertOpen(true)}><Plus size={14} />Add or change</AdBtn>} />
      {isLoading ? (
        <div className="ad-load"><span className="ad-spin" /></div>
      ) : error ? (
        <ErrorState error={error} />
      ) : list.length === 0 ? (
        <Empty icon={<SettingsIcon size={22} />} title="No settings yet" desc="Add an organization-level configuration value." action={<AdBtn kind="primary" onClick={() => setUpsertOpen(true)}><Plus size={14} />Add a setting</AdBtn>} />
      ) : (
        <div className="ad-t" role="table" aria-label="Settings" style={{ ["--cols"as string]: "minmax(0,2fr) minmax(0,2fr) 80px 150px" }}>
          <div className="ad-th" role="row"><span>Key</span><span>Value</span><span>Type</span><span>Updated</span></div>
          {list.map((st, i) => (
            <div key={st.id} className="ad-tr" role="row" style={{ ["--i" as string]: Math.min(i, 14) }}>
              <span className="cell ad-mono" style={{ color: "var(--ink)", fontWeight: 600 }}>{st.key}</span>
              <code className="cell ad-mono">{st.is_secret ? "••••••••" : JSON.stringify(st.value)}</code>
              <Pill tone={st.is_secret ? "amber" : "slate"}>{st.is_secret ? "Secret" : "Plain"}</Pill>
              <span className="cell">{fmtDateTime(st.updated_at)}</span>
            </div>
          ))}
        </div>
      )}
      <UpsertSettingModal
        open={upsertOpen}
        onClose={() => setUpsertOpen(false)}
        onSaved={() => {
          qc.invalidateQueries({ queryKey: ["admin-settings"] });
          notify("Setting saved", "success");
          setUpsertOpen(false);
        }}
      />
    </>
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

  if (isLoading) return <div className="ad-load"><span className="ad-spin" /></div>;
  if (error) return <ErrorState error={error} />;
  if (!data) return null;

  const providers: { key: keyof ConfigStatus; label: string }[] = [
    { key: "claude", label: "Claude (Anthropic)" },
    { key: "reducto", label: "Reducto (OCR)" },
    { key: "resend", label: "Resend (Email)" },
    { key: "docusign", label: "DocuSign (E-sign)" },
  ];

  const done = providers.filter(({ key }) => (data[key] as { configured: boolean }).configured).length;
  return (
    <>
      <SecHead title="Integrations" desc="The outside services Aegis relies on, and whether each one is connected." />
      <Stats items={[
        { n: `${done} of ${providers.length}`, label: "services connected", tone: done < providers.length ? "warn" : undefined },
        { n: data.debug ? "On" : "Off", label: "debug mode", tone: data.debug ? "warn" : undefined },
      ]} />
      <div className="ad-grid2">
        {providers.map(({ key, label }, i) => {
          const cfg = data[key] as { configured: boolean; mock: boolean };
          return (
            <article key={key} className={`ad-int${cfg.configured ? " ok" : " bad"}`} style={{ ["--i" as string]: i }}>
              <span className="ad-ico lg"><Plug size={20} /></span>
              <div className="main">
                <b>{label}</b>
                <span className="st"><i className="dot" />{cfg.configured ? "Connected" : "Not connected"}{cfg.mock && <Pill tone="amber">Mock mode</Pill>}</span>
              </div>
            </article>
          );
        })}
      </div>
      <section className="ad-card" style={{ maxWidth: 680 }}>
        <h3>Environment</h3>
        <dl className="ad-env">
          <div><dt>Storage root</dt><dd className="ad-mono">{data.storage_root}</dd></div>
          <div><dt>Debug mode</dt><dd><Pill tone={data.debug ? "amber" : "slate"}>{data.debug ? "Enabled" : "Disabled"}</Pill></dd></div>
        </dl>
        {data.debug && <p className="ad-warn">Debug mode is on. Turn it off before real use.</p>}
      </section>
    </>
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
  const { data: users } = useQuery({
    queryKey: ["org-users-all"],
    queryFn: () => usersApi.list(),
  });
  const [editor, setEditor] = useState<{
    mode: "create" | "edit";
    role?: RoleResponse;
    seed?: RoleResponse;
  } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const { confirm, dialog: confirmDialog } = useConfirm();

  async function remove(role: RoleResponse) {
    const ok = await confirm({
      title: "Delete role",
      message: role.user_count
        ? `Delete the "${titleCase(role.name)}" role? ${role.user_count} ${role.user_count === 1 ? "person has" : "people have"} it and will lose it. This cannot be undone.`
        : `Delete the "${titleCase(role.name)}" role? This cannot be undone.`,
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
      <RolesView
        roles={roles ?? []}
        catalog={catalog ?? []}
        users={users ?? []}
        busyId={busyId}
        adminRole={ADMIN_ROLE}
        onNew={(seed) => setEditor({ mode: "create", seed })}
        onEdit={(r) => setEditor({ mode: "edit", role: r })}
        onDelete={remove}
        onSavePerms={async (r, perms, rollup) => {
          try {
            await rolesApi.update(r.id, { permissions: perms, allows_hierarchy_rollup: rollup });
            qc.invalidateQueries({ queryKey: ["roles"] });
            notify("Role updated", "success");
          } catch (e) {
            notify(e instanceof Error ? e.message : "Save failed", "error");
          }
        }}
        onSetUserRoles={async (userId, roleIds, activeId) => {
          try {
            await rolesApi.setUserRoles(userId, { role_ids: roleIds, active_role_id: activeId });
            qc.invalidateQueries({ queryKey: ["roles"] });
            qc.invalidateQueries({ queryKey: ["org-users-all"] });
          } catch (e) {
            notify(e instanceof Error ? e.message : "Update failed", "error");
          }
        }}
        peopleView={<AssignRolesPanel roles={roles ?? []} />}
      />

      {editor && (
        <RoleEditorModal
          mode={editor.mode}
          role={editor.role}
          seed={editor.seed}
          catalog={catalog ?? []}
          allRoles={roles ?? []}
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
  seed,
  catalog,
  allRoles,
  onClose,
  onSaved,
}: {
  mode: "create" | "edit";
  role?: RoleResponse;
  seed?: RoleResponse;
  catalog: PermissionInfo[];
  allRoles: RoleResponse[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const { notify } = useToast();
  const from = role ?? seed;
  const [name, setName] = useState(seed ? `${seed.name} copy` : role?.name ?? "");
  const [description, setDescription] = useState(from?.description ?? "");
  const [selected, setSelected] = useState<Set<string>>(
    new Set(from?.permissions ?? []),
  );
  const [allowsHierarchyRollup, setAllowsHierarchyRollup] = useState(
    from?.allows_hierarchy_rollup ?? true,
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
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }
  function toggleGroup(perms: PermissionInfo[], on: boolean) {
    if (readOnly) return;
    setSelected((prev) => {
      const next = new Set(prev);
      for (const p of perms) {
        if (on) next.add(p.value);
        else next.delete(p.value);
      }
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
          allows_hierarchy_rollup: allowsHierarchyRollup,
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
    <RoleEditorUI
      mode={mode}
      title={mode === "create" ? "New role" : readOnly ? `${titleCase(role!.name)} (built-in)` : `Edit ${titleCase(role!.name)}`}
      readOnly={readOnly}
      nameLocked={nameLocked}
      name={name}
      setName={setName}
      description={description}
      setDescription={setDescription}
      rollup={allowsHierarchyRollup}
      setRollup={setAllowsHierarchyRollup}
      selected={selected}
      onToggle={toggle}
      onToggleGroup={toggleGroup}
      onReplace={(perms) => setSelected(new Set(perms))}
      groups={groups}
      roles={allRoles}
      busy={busy}
      canSave={!!name.trim()}
      onSave={save}
      onClose={onClose}
    />
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
      if (next.has(id)) next.delete(id);
      else next.add(id);
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
      setUserId("");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Update failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PeopleList users={users ?? []} onManage={(u) => pickUser(u.id)} />
      {selectedUser && (
        <AssignUI
          user={selectedUser}
          roles={roles}
          picked={roleIds}
          onToggle={toggleRole}
          activeId={activeRoleId}
          setActiveId={setActiveRoleId}
          clearance={clearance}
          setClearance={setClearance}
          levels={CLEARANCE_LEVELS}
          busy={busy}
          onSave={save}
          onClose={() => setUserId("")}
        />
      )}
    </>
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

  const list = walls ?? [];
  return (
    <>
      <SecHead title="Ethical walls" desc="Conflict-of-interest screens. A wall hard-blocks the named people from a contract, overriding ownership, sharing and admin alike, including in search and the assistant."
        action={<AdBtn kind="primary" onClick={() => setCreating(true)}><Plus size={14} />New wall</AdBtn>} />
      {list.length > 0 && <Stats items={[{ n: list.filter((w) => w.active).length, label: "up" }, { n: list.filter((w) => !w.active).length, label: "lifted" }]} />}
      {list.length === 0 ? (
        <Empty icon={<ShieldAlert size={22} />} title="No ethical walls" desc="Create a wall to seal conflicted people off from a contract." action={<AdBtn kind="primary" onClick={() => setCreating(true)}><Plus size={14} />New wall</AdBtn>} />
      ) : (
        <div className="ad-t" role="table" aria-label="Ethical walls" style={{ ["--cols" as string]: "minmax(0,1.6fr) minmax(0,1.2fr) minmax(0,1.6fr) 80px 44px" }}>
          <div className="ad-th" role="row"><span>Wall</span><span>Scope</span><span>Barred</span><span>State</span><span /></div>
          {list.map((w, i) => (
            <div key={w.id} className="ad-tr" role="row" style={{ ["--i" as string]: Math.min(i, 14), opacity: w.active ? 1 : 0.6 }}>
              <span className="who"><span className="ad-ico red"><ShieldAlert size={15} /></span><span style={{ minWidth: 0 }}><b>{w.name}</b>{w.reason && <small>{w.reason}</small>}</span></span>
              <span className="cell"><Pill tone="slate">{titleCase(w.scope_type)}</Pill> {w.scope_label ?? w.scope_id}</span>
              <span className="wrap">{w.principals.map((pr) => <Pill key={pr.id ?? pr.principal_id} tone="red">{pr.principal_type === "role" ? "Role: " : ""}{pr.principal_label ?? pr.principal_id}</Pill>)}</span>
              <span className="end" style={{ justifyContent: "flex-start" }}><Switch on={w.active} onChange={() => toggleActive(w)} label={w.active ? `Lift ${w.name}` : `Re-activate ${w.name}`} disabled={busyId === w.id} /></span>
              <span className="end"><AdBtn kind="danger" className="sm" disabled={busyId === w.id} onClick={() => remove(w)} aria-label={`Delete ${w.name}`}><Trash2 size={13} /></AdBtn></span>
            </div>
          ))}
        </div>
      )}
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
    </>
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

  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [scopeId, setScopeId] = useState("");
  const [barred, setBarred] = useState<Set<string>>(new Set()); // "user:<id>" | "role:<id>"
  const [busy, setBusy] = useState(false);

  const scopeOptions = (contracts ?? []).map((c) => ({ id: c.id, label: c.title }));

  function toggle(key: string) {
    setBarred((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
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
        scope_type: "contract",
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
        <Field label="Contract">
          <Select value={scopeId} onChange={(e) => setScopeId(e.target.value)}>
            <option value="">Select…</option>
            {scopeOptions.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
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

  if (isLoading) return <div className="ad-load"><span className="ad-spin" /></div>;
  if (error) return <ErrorState error={error} />;

  const byAction = AUTHORITY_ACTIONS.map((a) => ({
    ...a,
    rows: (grants ?? []).filter((g) => g.action === a.value),
  }));

  return (
    <>
      <SecHead title="Delegation of authority" desc="Who may commit the company, and up to what limit. An action is only enforced once you define a policy for it."
        action={<AdBtn kind="primary" onClick={() => setCreating(true)}><Plus size={14} />New authority</AdBtn>} />
      {byAction.map((a) => (
        <section key={a.value} className="adm-sec">
          <div className="ad-bar">
            <span className="ad-ico"><Scale size={15} /></span>
            <h3 className="ad-subh">{a.label}</h3>
            <Pill tone={a.rows.length ? "blue" : "slate"}>{a.rows.length ? `Gated · ${a.rows.length}` : "Not gated"}</Pill>
          </div>
          {a.rows.length === 0 ? (
            <div className="ad-calm">No limit set. Anyone whose role allows this may do it. Add a policy to cap it.</div>
          ) : (
            <div className="ad-t" role="table" aria-label={a.label} style={{ ["--cols" as string]: "minmax(0,2fr) minmax(0,2fr) 140px 80px" }}>
              <div className="ad-th" role="row"><span>Who</span><span>Scope</span><span>Limit</span><span /></div>
              {a.rows.map((g, i) => (
                <div key={g.id} className="ad-tr" role="row" style={{ ["--i" as string]: Math.min(i, 14) }}>
                  <span className="who"><Tile name={g.principal_label} size={28} /><span style={{ minWidth: 0 }}><b>{g.principal_label}</b><small>{g.principal_type === "role" ? "Role" : "Person"}{g.delegated_by_label ? ` · delegated by ${g.delegated_by_label}` : ""}</small></span></span>
                  <span className="wrap">
                    {[g.allowed_contract_types.length ? `Types: ${g.allowed_contract_types.join(", ")}` : null, g.allowed_jurisdictions.length ? `Juris: ${g.allowed_jurisdictions.join(", ")}` : null, g.max_risk_band ? `Risk up to ${g.max_risk_band}` : null].filter(Boolean).map((t) => <Pill key={t} tone="slate">{t}</Pill>)}
                    {!g.allowed_contract_types.length && !g.allowed_jurisdictions.length && !g.max_risk_band && <span className="rt-dim-s">Any type, place or risk</span>}
                  </span>
                  <span className="cell" style={{ fontWeight: 700, color: "var(--ink)" }}>{fmtLimit(g)}</span>
                  <span className="end"><AdBtn kind="danger" className="sm" disabled={busyId === g.id} onClick={() => revoke(g)}>Revoke</AdBtn></span>
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
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
    </>
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

// ---- Teams (who does the work) --------------------------------------------

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
                : "border-slate-200 bg-slate-100 text-slate-500 hover:border-slate-300",
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
      <TeamsView
        teams={teams ?? []}
        users={users ?? []}
        onMembers={async (t, members) => {
          try {
            await intakeApi.updateTeam(t.id, { members });
            invalidate();
          } catch (e) {
            notify(e instanceof Error ? e.message : "Update failed", "error");
          }
        }}
        onActive={async (t, on) => {
          try {
            await intakeApi.updateTeam(t.id, { active: on });
            invalidate();
            notify(on ? `${t.name} is taking work again` : `${t.name} is paused`, "success");
          } catch (e) {
            notify(e instanceof Error ? e.message : "Update failed", "error");
          }
        }}
        onNew={() => setEditing("new")}
        onEdit={(t) => setEditing(t)}
        onDelete={async (t) => {
          const ok = await confirm({
            title: `Delete ${t.name}?`,
            message: "Requests already assigned stay put. A team still used by a workflow can't be deleted.",
            confirmLabel: "Delete",
            tone: "danger",
          });
          if (ok) del.mutate(t.id);
        }}
      />
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
  const [defaultIntake, setDefaultIntake] = useState(!!team?.is_default_intake);
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
        is_default_intake: defaultIntake || undefined,
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
    <TeamEditorUI
      team={team}
      name={name}
      setName={setName}
      keyVal={key}
      setKey={setKey}
      strategy={strategy}
      setStrategy={setStrategy}
      overflow={overflow}
      setOverflow={setOverflow}
      teams={teams}
      defaultIntake={defaultIntake}
      setDefaultIntake={setDefaultIntake}
      expertise={expertise}
      departments={departments}
      onExpertise={(v) => toggle(expertise, setExpertise, v)}
      onDepartments={(v) => toggle(departments, setDepartments, v)}
      categories={MATTER_CATEGORIES}
      units={BUSINESS_UNITS}
      members={members}
      setMembers={setMembers}
      users={users}
      busy={save.isPending}
      canSave={!!canSave}
      onSave={() => save.mutate()}
      onClose={onClose}
    />
  );
}
