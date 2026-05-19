"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Plus,
  Trash2,
  FolderPlus,
  UserPlus,
  Share2,
  Bot,
  Table2,
  MessageSquare,
} from "lucide-react";
import {
  assistantApi,
  contractsApi,
  projectsApi,
  tabularApi,
} from "@/lib/endpoints";
import { CreateReviewModal } from "@/components/create-review-modal";
import { fmtRelative } from "@/lib/utils";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CenterSpinner,
  EmptyState,
  ErrorState,
  Field,
  Input,
  Modal,
  Select,
  Tabs,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ProjectType } from "@/lib/types";

function projectTypeTone(
  type: ProjectType,
): "slate" | "blue" | "violet" | "amber" {
  switch (type) {
    case "contract_review":
      return "blue";
    case "due_diligence":
      return "violet";
    case "regulatory":
      return "amber";
    default:
      return "slate";
  }
}

function accessTone(
  level: string,
): "slate" | "blue" | "green" | "amber" {
  switch (level) {
    case "share":
      return "amber";
    case "update":
      return "blue";
    case "read":
      return "green";
    default:
      return "slate";
  }
}

export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [tab, setTab] = useState("contracts");

  const { data: project, isLoading, error } = useQuery({
    queryKey: ["project", id],
    queryFn: () => projectsApi.get(id),
  });

  if (isLoading) return <CenterSpinner label="Loading project…" />;
  if (error) return <ErrorState error={error} />;
  if (!project) return null;

  return (
    <div className="space-y-6">
      <div>
        <Link
          href="/projects"
          className="mb-3 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800"
        >
          <ArrowLeft className="h-4 w-4" />
          Projects
        </Link>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-slate-900">
              {project.name}
            </h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge tone={projectTypeTone(project.project_type)}>
                {titleCase(project.project_type)}
              </Badge>
              {project.description && (
                <span className="text-sm text-slate-500">
                  {project.description}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <Tabs
        active={tab}
        onChange={setTab}
        tabs={[
          { id: "contracts", label: "Contracts" },
          { id: "assistant", label: "Assistant" },
          { id: "reviews", label: "Tabular Reviews" },
          { id: "folders", label: "Folders" },
          { id: "members", label: "Members" },
          { id: "shares", label: "Shares" },
        ]}
      />

      {tab === "contracts" && <ContractsTab projectId={id} />}
      {tab === "assistant" && (
        <ProjectAssistantTab projectId={id} projectName={project.name} />
      )}
      {tab === "reviews" && (
        <ProjectReviewsTab projectId={id} projectName={project.name} />
      )}
      {tab === "folders" && <FoldersTab projectId={id} />}
      {tab === "members" && <MembersTab projectId={id} />}
      {tab === "shares" && <SharesTab projectId={id} />}
    </div>
  );
}

// ---- Contracts -----------------------------------------------------------
function ContractsTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [addOpen, setAddOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", projectId, "contracts"],
    queryFn: () => projectsApi.contracts(projectId),
  });
  const { data: allContracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const { data: folders } = useQuery({
    queryKey: ["project", projectId, "folders"],
    queryFn: () => projectsApi.folders(projectId),
  });

  const titleById = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of allContracts ?? []) m.set(c.id, c.title);
    return m;
  }, [allContracts]);

  const folderById = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of folders ?? []) m.set(f.id, f.name);
    return m;
  }, [folders]);

  async function remove(contractId: string) {
    try {
      await projectsApi.removeContract(projectId, contractId);
      qc.invalidateQueries({
        queryKey: ["project", projectId, "contracts"],
      });
      notify("Contract removed from project", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to remove", "error");
    }
  }

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setAddOpen(true)}>
          <Plus className="h-4 w-4" />
          Add contract
        </Button>
      </div>

      {(data ?? []).length === 0 ? (
        <EmptyState
          title="No contracts in this project"
          description="Add an existing contract to organize it under this project."
          action={
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="h-4 w-4" />
              Add contract
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Contract</TH>
                <TH>Folder</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((pc) => (
                <TR key={pc.id}>
                  <TD className="font-medium text-slate-900">
                    <Link
                      href={`/contracts/${pc.contract_id}`}
                      className="text-brand-700 hover:text-brand-800"
                    >
                      {titleById.get(pc.contract_id) ?? pc.contract_id}
                    </Link>
                  </TD>
                  <TD>
                    {pc.folder_id ? (
                      <Badge tone="slate">
                        {folderById.get(pc.folder_id) ?? "Folder"}
                      </Badge>
                    ) : (
                      "—"
                    )}
                  </TD>
                  <TD className="text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => remove(pc.contract_id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {addOpen && (
        <AddContractModal
          projectId={projectId}
          existing={new Set((data ?? []).map((pc) => pc.contract_id))}
          onClose={() => setAddOpen(false)}
          onDone={() => {
            qc.invalidateQueries({
              queryKey: ["project", projectId, "contracts"],
            });
            notify("Contract added to project", "success");
            setAddOpen(false);
          }}
        />
      )}
    </div>
  );
}

function AddContractModal({
  projectId,
  existing,
  onClose,
  onDone,
}: {
  projectId: string;
  existing: Set<string>;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const { data: allContracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: contractsApi.list,
  });
  const { data: folders } = useQuery({
    queryKey: ["project", projectId, "folders"],
    queryFn: () => projectsApi.folders(projectId),
  });

  const available = (allContracts ?? []).filter((c) => !existing.has(c.id));
  const [contractId, setContractId] = useState("");
  const [folderId, setFolderId] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!contractId) return;
    setBusy(true);
    try {
      await projectsApi.addContract(
        projectId,
        contractId,
        folderId || undefined,
      );
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to add", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add contract to project"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!contractId}>
            Add contract
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract">
          <Select
            value={contractId}
            onChange={(e) => setContractId(e.target.value)}
          >
            <option value="">Select a contract…</option>
            {available.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Folder" hint="Optional">
          <Select
            value={folderId}
            onChange={(e) => setFolderId(e.target.value)}
          >
            <option value="">No folder</option>
            {(folders ?? []).map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
        </Field>
        {available.length === 0 && (
          <p className="text-sm text-slate-400">
            All contracts are already in this project.
          </p>
        )}
      </div>
    </Modal>
  );
}

// ---- Assistant (project-scoped chats) ------------------------------------
function ProjectAssistantTab({
  projectId,
  projectName,
}: {
  projectId: string;
  projectName: string;
}) {
  const router = useRouter();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", projectId, "sessions"],
    queryFn: () => assistantApi.sessions({ project_id: projectId }),
  });

  function openChat(sessionId: string) {
    router.push(`/assistant?project=${projectId}&session=${sessionId}`);
  }

  async function newChat() {
    setBusy(true);
    try {
      const s = await assistantApi.createSession({
        session_type: "project",
        project_id: projectId,
        title: `${projectName} — new chat`,
      });
      openChat(s.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to start chat", "error");
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">
          AI conversations grounded in this project&apos;s contracts.
        </p>
        <Button onClick={newChat} loading={busy}>
          <Plus className="h-4 w-4" />
          New chat
        </Button>
      </div>

      {(data ?? []).length === 0 ? (
        <EmptyState
          icon={<Bot className="h-6 w-6" />}
          title="No chats in this project"
          description="Start an AI conversation scoped to this project. It can read the project's contracts, run playbooks and draft redlines."
          action={
            <Button onClick={newChat} loading={busy}>
              <Plus className="h-4 w-4" />
              New chat
            </Button>
          }
        />
      ) : (
        <div className="space-y-2">
          {(data ?? []).map((s) => (
            <button
              key={s.id}
              onClick={() => openChat(s.id)}
              className="flex w-full items-center gap-3 rounded-xl border border-slate-200 bg-white p-4 text-left transition-colors hover:border-brand-300 hover:bg-brand-50/40"
            >
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
                <MessageSquare className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-slate-900">
                  {s.title ?? "Untitled conversation"}
                </p>
                <p className="text-xs text-slate-400">
                  {titleCase(s.session_type)} · updated{" "}
                  {fmtRelative(s.updated_at)}
                </p>
              </div>
              <Badge tone={s.status === "active" ? "green" : "slate"}>
                {titleCase(s.status)}
              </Badge>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Reviews (project-scoped tabular reviews) ----------------------------
function ProjectReviewsTab({
  projectId,
  projectName,
}: {
  projectId: string;
  projectName: string;
}) {
  const router = useRouter();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["tabular-reviews"],
    queryFn: tabularApi.list,
  });

  const reviews = (data ?? []).filter((r) => r.project_id === projectId);

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">
          Column-based extraction across {projectName}&apos;s contracts.
        </p>
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New review
        </Button>
      </div>

      {reviews.length === 0 ? (
        <EmptyState
          icon={<Table2 className="h-6 w-6" />}
          title="No reviews in this project"
          description="Create a tabular review seeded from a workflow template and this project's contracts."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New review
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {reviews.map((r) => (
            <Card key={r.id} className="flex flex-col">
              <CardBody className="flex flex-1 flex-col gap-3">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="text-sm font-semibold text-slate-900">
                    {r.name}
                  </h3>
                  <Badge tone={r.status === "complete" ? "green" : "amber"}>
                    {titleCase(r.status)}
                  </Badge>
                </div>
                <p className="text-sm text-slate-500">
                  {r.source_contract_ids.length} contract
                  {r.source_contract_ids.length === 1 ? "" : "s"}
                </p>
                <Button
                  variant="outline"
                  className="mt-auto w-full"
                  onClick={() => router.push(`/tabular-reviews/${r.id}`)}
                >
                  Open review
                </Button>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <CreateReviewModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        defaultProjectId={projectId}
        lockProject
        onCreated={(id) => router.push(`/tabular-reviews/${id}`)}
      />
    </div>
  );
}

// ---- Folders -------------------------------------------------------------
function FoldersTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", projectId, "folders"],
    queryFn: () => projectsApi.folders(projectId),
  });

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setCreateOpen(true)}>
          <FolderPlus className="h-4 w-4" />
          New folder
        </Button>
      </div>

      {(data ?? []).length === 0 ? (
        <EmptyState
          title="No folders"
          description="Create folders to organize contracts within this project."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <FolderPlus className="h-4 w-4" />
              New folder
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Folder</TH>
                <TH>Parent</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((f) => (
                <TR key={f.id}>
                  <TD className="font-medium text-slate-900">{f.name}</TD>
                  <TD>
                    {f.parent_folder_id
                      ? (data ?? []).find(
                          (p) => p.id === f.parent_folder_id,
                        )?.name ?? "—"
                      : "—"}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {createOpen && (
        <CreateFolderModal
          projectId={projectId}
          folders={data ?? []}
          onClose={() => setCreateOpen(false)}
          onDone={() => {
            qc.invalidateQueries({
              queryKey: ["project", projectId, "folders"],
            });
            notify("Folder created", "success");
            setCreateOpen(false);
          }}
        />
      )}
    </div>
  );
}

function CreateFolderModal({
  projectId,
  folders,
  onClose,
  onDone,
}: {
  projectId: string;
  folders: { id: string; name: string }[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await projectsApi.createFolder(
        projectId,
        name.trim(),
        parentId || undefined,
      );
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create folder", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="New folder"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create folder
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Folder name">
          <Input
            placeholder="e.g. Diligence — HR"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Parent folder" hint="Optional">
          <Select
            value={parentId}
            onChange={(e) => setParentId(e.target.value)}
          >
            <option value="">No parent (top level)</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

// ---- Members -------------------------------------------------------------
function MembersTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [editOpen, setEditOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", projectId, "members"],
    queryFn: () => projectsApi.members(projectId),
  });

  async function remove(userId: string) {
    try {
      await projectsApi.removeMember(projectId, userId);
      qc.invalidateQueries({
        queryKey: ["project", projectId, "members"],
      });
      notify("Member removed", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to remove", "error");
    }
  }

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setEditOpen(true)}>
          <UserPlus className="h-4 w-4" />
          Add / update member
        </Button>
      </div>

      {(data ?? []).length === 0 ? (
        <EmptyState
          title="No members"
          description="Add members so collaborators can access this project."
          action={
            <Button onClick={() => setEditOpen(true)}>
              <UserPlus className="h-4 w-4" />
              Add / update member
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>User ID</TH>
                <TH>Role</TH>
                <TH className="text-right">Actions</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((m) => (
                <TR key={m.id}>
                  <TD className="font-mono text-xs text-slate-700">
                    {m.user_id}
                  </TD>
                  <TD>
                    <Badge tone="slate">{titleCase(m.role)}</Badge>
                  </TD>
                  <TD className="text-right">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => remove(m.user_id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {editOpen && (
        <MemberModal
          projectId={projectId}
          onClose={() => setEditOpen(false)}
          onDone={() => {
            qc.invalidateQueries({
              queryKey: ["project", projectId, "members"],
            });
            notify("Member saved", "success");
            setEditOpen(false);
          }}
        />
      )}
    </div>
  );
}

function MemberModal({
  projectId,
  onClose,
  onDone,
}: {
  projectId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!userId.trim()) return;
    setBusy(true);
    try {
      await projectsApi.upsertMember(
        projectId,
        userId.trim(),
        role.trim() || "member",
      );
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save member", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add / update member"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!userId.trim()}>
            Save member
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="User ID">
          <Input
            placeholder="User UUID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
        </Field>
        <Field label="Role" hint="e.g. member, editor, owner">
          <Input value={role} onChange={(e) => setRole(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}

// ---- Shares --------------------------------------------------------------
function SharesTab({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [shareOpen, setShareOpen] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", projectId, "shares"],
    queryFn: () => projectsApi.shares(projectId),
  });

  if (isLoading) return <CenterSpinner />;

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button onClick={() => setShareOpen(true)}>
          <Share2 className="h-4 w-4" />
          Share
        </Button>
      </div>

      {(data ?? []).length === 0 ? (
        <EmptyState
          title="Not shared"
          description="Share this project with another user to grant access."
          action={
            <Button onClick={() => setShareOpen(true)}>
              <Share2 className="h-4 w-4" />
              Share
            </Button>
          }
        />
      ) : (
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Shared with</TH>
                <TH>Access</TH>
                <TH>Expires</TH>
                <TH>Status</TH>
              </tr>
            </THead>
            <tbody>
              {(data ?? []).map((s) => (
                <TR key={s.id}>
                  <TD className="font-mono text-xs text-slate-700">
                    {s.shared_with_user_id}
                  </TD>
                  <TD>
                    <Badge tone={accessTone(s.access_level)}>
                      {titleCase(s.access_level)}
                    </Badge>
                  </TD>
                  <TD>{fmtDate(s.expires_at)}</TD>
                  <TD>
                    {s.revoked_at ? (
                      <Badge tone="red">Revoked</Badge>
                    ) : (
                      <Badge tone="green">Active</Badge>
                    )}
                  </TD>
                </TR>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {shareOpen && (
        <ShareModal
          projectId={projectId}
          onClose={() => setShareOpen(false)}
          onDone={() => {
            qc.invalidateQueries({
              queryKey: ["project", projectId, "shares"],
            });
            notify("Project shared", "success");
            setShareOpen(false);
          }}
        />
      )}
    </div>
  );
}

function ShareModal({
  projectId,
  onClose,
  onDone,
}: {
  projectId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [userId, setUserId] = useState("");
  const [accessLevel, setAccessLevel] = useState("read");
  const [expiresAt, setExpiresAt] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!userId.trim()) return;
    setBusy(true);
    try {
      await projectsApi.createShare(
        projectId,
        userId.trim(),
        accessLevel,
        expiresAt ? new Date(expiresAt).toISOString() : undefined,
      );
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to share", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Share project"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!userId.trim()}>
            Share
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="User ID">
          <Input
            placeholder="User UUID"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
        </Field>
        <Field label="Access level">
          <Select
            value={accessLevel}
            onChange={(e) => setAccessLevel(e.target.value)}
          >
            <option value="read">Read</option>
            <option value="update">Update</option>
            <option value="share">Share</option>
          </Select>
        </Field>
        <Field label="Expires at" hint="Optional">
          <Input
            type="date"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}
