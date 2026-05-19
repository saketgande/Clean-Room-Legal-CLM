"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Plus, Sparkles } from "lucide-react";
import { playbooksApi } from "@/lib/endpoints";
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
  Textarea,
} from "@/components/ui";
import { statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";

export default function PlaybooksPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();

  const [createOpen, setCreateOpen] = useState(false);
  const [generateOpen, setGenerateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["playbooks"],
    queryFn: playbooksApi.list,
  });

  function onCreated(id: string) {
    qc.invalidateQueries({ queryKey: ["playbooks"] });
    notify("Playbook created", "success");
    router.push(`/playbooks/${id}`);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Playbooks"
        description="Negotiation rulebooks used to review contracts and surface deviations."
        actions={
          <>
            <Button variant="outline" onClick={() => setGenerateOpen(true)}>
              <Sparkles className="h-4 w-4" />
              Generate with AI
            </Button>
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New playbook
            </Button>
          </>
        }
      />

      {isLoading ? (
        <CenterSpinner label="Loading playbooks…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : (data ?? []).length === 0 ? (
        <EmptyState
          icon={<BookOpen className="h-6 w-6" />}
          title="No playbooks yet"
          description="Create a playbook manually or generate one with AI to start reviewing contracts against your standards."
          action={
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New playbook
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
                  <Badge tone={statusTone(p.status)}>
                    {titleCase(p.status)}
                  </Badge>
                </div>
                <p className="flex-1 text-sm text-slate-500">
                  {p.description || "No description provided."}
                </p>
                <div>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => router.push(`/playbooks/${p.id}`)}
                  >
                    Open
                  </Button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}

      <CreatePlaybookModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={onCreated}
      />
      <GeneratePlaybookModal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        onCreated={onCreated}
      />
    </div>
  );
}

function CreatePlaybookModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const res = await playbooksApi.create(
        name.trim(),
        description.trim() || undefined,
      );
      onCreated(res.id);
      setName("");
      setDescription("");
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New playbook"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            Create
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            placeholder="e.g. Standard SaaS Playbook"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Description" hint="Optional">
          <Textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}

function GeneratePlaybookModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const { notify } = useToast();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [contractType, setContractType] = useState("");
  const [focusAreas, setFocusAreas] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const focus = focusAreas
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await playbooksApi.generate({
        name: name.trim(),
        description: description.trim() || undefined,
        contract_type: contractType.trim() || undefined,
        focus_areas: focus.length ? focus : undefined,
      });
      onCreated(res.id);
      setName("");
      setDescription("");
      setContractType("");
      setFocusAreas("");
      onClose();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Generation failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Generate playbook with AI"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!name.trim()}>
            <Sparkles className="h-4 w-4" />
            Generate
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <Input
            placeholder="e.g. Vendor MSA Playbook"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Description" hint="Optional — guides the generated rules">
          <Textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>
        <Field label="Contract type" hint="Optional — e.g. saas, nda, msa">
          <Input
            value={contractType}
            onChange={(e) => setContractType(e.target.value)}
          />
        </Field>
        <Field
          label="Focus areas"
          hint="Optional — comma-separated, e.g. liability, indemnity, data privacy"
        >
          <Input
            value={focusAreas}
            onChange={(e) => setFocusAreas(e.target.value)}
          />
        </Field>
      </div>
    </Modal>
  );
}
