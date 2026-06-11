"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus, ArrowRight } from "lucide-react";
import { projectsApi } from "@/lib/endpoints";
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
  PageHeader,
  Select,
  Textarea,
} from "@/components/ui";
import { titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { ProjectType } from "@/lib/types";

const PROJECT_TYPES: ProjectType[] = [
  "general",
  "contract_review",
  "due_diligence",
  "regulatory",
];

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

export default function ProjectsPage() {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [createOpen, setCreateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["projects"],
    queryFn: projectsApi.list,
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projects"
        description="Organize contracts into matters, deal rooms and review workspaces."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            New project
          </Button>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading projects…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<FolderKanban className="h-6 w-6" />}
          title="No projects yet"
          description="Create a project to group related contracts, add folders, members and shares."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New project
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {(data ?? []).map((p) => (
            <Card key={p.id} className="flex flex-col">
              <CardBody className="flex flex-1 flex-col gap-3">
                <div className="flex items-start justify-between gap-3">
                  <h3 className="text-sm font-semibold text-slate-900">
                    {p.name}
                  </h3>
                  <Badge tone={projectTypeTone(p.project_type)}>
                    {titleCase(p.project_type)}
                  </Badge>
                </div>
                <p className="flex-1 text-sm text-slate-500">
                  {p.description?.trim() || "No description."}
                </p>
                <Link
                  href={`/projects/${p.id}`}
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-700 hover:text-brand-800"
                >
                  Open
                  <ArrowRight className="h-4 w-4" />
                </Link>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <CreateProjectModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          qc.invalidateQueries({ queryKey: ["projects"] });
          notify("Project created", "success");
          setCreateOpen(false);
        }}
      />
    </div>
  );
}

function CreateProjectModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [projectType, setProjectType] = useState<ProjectType>("general");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await projectsApi.create({
        name: name.trim(),
        description: description.trim() || undefined,
        project_type: projectType,
      });
      setName("");
      setDescription("");
      setProjectType("general");
      onCreated();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to create project", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New project"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create project
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            placeholder="e.g. Acme acquisition — due diligence"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={3}
            placeholder="What is this project for?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="Project type">
          <Select
            value={projectType}
            onChange={(e) => setProjectType(e.target.value as ProjectType)}
          >
            {PROJECT_TYPES.map((t) => (
              <option key={t} value={t}>
                {titleCase(t)}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}
