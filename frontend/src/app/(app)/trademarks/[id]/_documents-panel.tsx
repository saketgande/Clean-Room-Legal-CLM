"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Paperclip } from "lucide-react";
import { Button, Card, CardBody, CenterSpinner, EmptyState, ErrorState } from "@/components/ui";
import { intakeApi } from "@/lib/endpoints";
import { fmtDateTime } from "@/lib/utils";
import { useToast } from "@/components/toast";

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

/** The case's documents are the attachments of its Legal Intake request (LTE reports, filing documents). */
export function DocumentsPanel({ requestId }: { requestId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const { data, isLoading, error } = useQuery({
    queryKey: ["intake", requestId, "documents"],
    queryFn: () => intakeApi.documents(requestId),
  });

  async function upload(file: File) {
    setUploading(true);
    try {
      await intakeApi.uploadDocument(requestId, {
        filename: file.name,
        mime_type: file.type || "application/octet-stream",
        content_b64: await fileToBase64(file),
      });
      qc.invalidateQueries({ queryKey: ["intake", requestId, "documents"] });
      notify("Document attached", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[13px] text-slate-500">
          Attach the LTE search report (Registry, Mike Legal, WIPO or digital searches) and the filing documents. They
          are saved on the case&apos;s Legal Intake request.
        </p>
        <input
          ref={input}
          type="file"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
        />
        <Button
          variant="outline"
          size="icon"
          className="h-8 w-8 shrink-0"
          loading={uploading}
          aria-label="Attach document"
          title="Attach document"
          onClick={() => input.current?.click()}
        >
          {!uploading && <Paperclip className="h-4 w-4" />}
        </Button>
      </div>
      {isLoading ? (
        <CenterSpinner label="Loading documents…" />
      ) : error ? (
        <ErrorState error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState title="No documents attached" description="Attached documents appear here." />
      ) : (
        <Card>
          <CardBody className="divide-y divide-slate-200 p-0">
            {data.map((doc) => (
              <div key={doc.id} className="flex items-center justify-between gap-3 px-4 py-3 text-[13px]">
                <span className="truncate text-slate-900">{doc.filename}</span>
                <span className="shrink-0 text-xs text-slate-500">
                  {Math.max(1, Math.round(doc.size_bytes / 1024))} KB · {fmtDateTime(doc.created_at)}
                </span>
              </div>
            ))}
          </CardBody>
        </Card>
      )}
    </div>
  );
}
