"use client";

import { useState } from "react";
import { contractsApi } from "@/lib/endpoints";
import { Button, Field, Input, Modal } from "@/components/ui";
import { useToast } from "@/components/toast";
import type { ContractResponse } from "@/lib/types";

/**
 * Shared "import a file from outside" modal. Uploads a document via
 * /contracts/upload (MIME-sniffed, stored, text-extracted, AI-queued) and
 * hands the created contract back to the caller. Reused by Contract Hub,
 * Projects, Tabular Review and the AI Assistant so a new file can be brought
 * in from anywhere — not only Contract Hub.
 */
export function ImportContractModal({
  open,
  onClose,
  onUploaded,
  defaultProjectId,
  modalTitle = "Import contract",
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: (contract: ContractResponse) => void;
  defaultProjectId?: string;
  modalTitle?: string;
}) {
  const { notify } = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  function reset() {
    setFile(null);
    setTitle("");
  }

  async function submit() {
    if (!file || busy) return;
    setBusy(true);
    try {
      const res = await contractsApi.upload(file, {
        title: title.trim() || undefined,
        project_id: defaultProjectId,
      });
      notify("Contract imported", "success");
      reset();
      onClose();
      onUploaded(res.contract);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={modalTitle}
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={busy} disabled={!file}>
            Upload
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Contract file" hint="PDF, DOCX, DOC, TXT, PNG, JPEG">
          <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center text-sm text-slate-600 hover:border-brand-400">
            {file ? file.name : "Click to choose a file"}
            <input
              type="file"
              className="hidden"
              accept=".pdf,.docx,.doc,.txt,.png,.jpg,.jpeg"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
        </Field>
        <Field label="Title" hint="Optional — extracted if blank">
          <Input value={title} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </div>
    </Modal>
  );
}
