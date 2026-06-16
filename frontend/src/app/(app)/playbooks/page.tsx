"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Bot, Plus, Sparkles } from "lucide-react";
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
            <Button onClick={() => router.push("/playbooks/build")}>
              <Bot className="h-4 w-4" />
              Build with AI
            </Button>
            <Button variant="outline" onClick={() => setGenerateOpen(true)}>
              <Sparkles className="h-4 w-4" />
              Quick generate
            </Button>
            <Button variant="outline" onClick={() => setCreateOpen(true)}>
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
  const [mode, setMode] = useState<"file" | "text">("file");
  const [file, setFile] = useState<File | null>(null);
  const [pastedText, setPastedText] = useState("");
  const [name, setName] = useState("");
  const [contractType, setContractType] = useState("");
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);

  const canSubmit = mode === "file" ? !!file : pastedText.trim().length > 50;

  async function submit() {
    if (!canSubmit) return;
    setBusy(true);
    try {
      const form = new FormData();
      if (mode === "file" && file) form.append("file", file);
      if (mode === "text") form.append("pasted_text", pastedText);
      if (name.trim()) form.append("name", name.trim());
      if (contractType.trim()) form.append("contract_type", contractType.trim());
      if (instructions.trim()) form.append("instructions", instructions.trim());
      const res = await playbooksApi.generateFromDocument(form);
      setFile(null);
      setPastedText("");
      setName("");
      setContractType("");
      setInstructions("");
      onCreated(res.id);
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
          <Button onClick={submit} loading={busy} disabled={!canSubmit}>
            <Sparkles className="h-4 w-4" />
            Generate
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-sm text-slate-500">
          Upload a template, an exemplar contract, or your existing playbook — AI reads it and
          drafts standard positions you can review and edit.
        </p>
        <div className="flex gap-1 rounded-md bg-slate-100 p-0.5 text-sm">
          {(["file", "text"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`flex-1 rounded px-3 py-1.5 font-medium ${
                mode === m ? "bg-white text-slate-900 shadow-sm" : "text-slate-500"
              }`}
            >
              {m === "file" ? "Upload file" : "Paste text"}
            </button>
          ))}
        </div>

        {mode === "file" ? (
          <Field label="Document" hint="PDF, DOCX, or text file">
            <input
              type="file"
              accept=".pdf,.docx,.txt,.md,application/pdf,text/plain"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-slate-600 file:mr-3 file:rounded-md file:border-0 file:bg-brand-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-brand-700 hover:file:bg-brand-100"
            />
          </Field>
        ) : (
          <Field label="Paste standard terms / playbook text">
            <Textarea
              rows={6}
              value={pastedText}
              onChange={(e) => setPastedText(e.target.value)}
              placeholder="Paste contract clauses or playbook text…"
            />
          </Field>
        )}

        <Field label="Playbook name" hint="Optional — AI suggests one if left blank">
          <Input
            placeholder="e.g. Vendor MSA Playbook"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="Contract type" hint="Optional — e.g. saas, nda, msa">
          <Input value={contractType} onChange={(e) => setContractType(e.target.value)} />
        </Field>
        <Field
          label="Instructions"
          hint="Optional — e.g. 'we are the customer; be strict on liability'"
        >
          <Input value={instructions} onChange={(e) => setInstructions(e.target.value)} />
        </Field>
        <p className="text-[11px] text-slate-400">
          This runs an AI analysis and may take ~10–30 seconds.
        </p>
      </div>
    </Modal>
  );
}
