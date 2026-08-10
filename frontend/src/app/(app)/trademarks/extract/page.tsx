"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, UploadCloud } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import {
  Badge,
  Button,
  Card,
  CardBody,
  Field,
  Input,
  MessageBar,
  PageHeader,
  Select,
  Table,
  TD,
  TH,
  THead,
  TR,
} from "@/components/ui";
import { useToast } from "@/components/toast";
import type {
  DocumentExtractTemplate,
  ExtractResponse,
  FieldDataType,
  FieldDefinition,
  UploadDocumentResponse,
} from "@/lib/types";

const STEPS = ["Upload", "Page range", "Template", "Review & extract"];

export default function DocumentExtractionPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();
  const [step, setStep] = useState(0);

  const [file, setFile] = useState<File | null>(null);
  const [doc, setDoc] = useState<UploadDocumentResponse | null>(null);
  const [uploading, setUploading] = useState(false);

  const [pageStart, setPageStart] = useState(1);
  const [pageEnd, setPageEnd] = useState(1);

  const [template, setTemplate] = useState<DocumentExtractTemplate>("generic");
  const [fields, setFields] = useState<FieldDefinition[]>([
    { name: "", data_type: "string", order: 0, anchor: "" },
  ]);

  const [extracting, setExtracting] = useState(false);
  const [extractResult, setExtractResult] = useState<ExtractResponse | null>(null);
  const [ingesting, setIngesting] = useState(false);

  async function handleUpload() {
    if (!file) return;
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await trademarksApi.uploadDocument(form);
      setDoc(result);
      setPageEnd(result.total_pages);
      setStep(1);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setUploading(false);
    }
  }

  function updateField(index: number, patch: Partial<FieldDefinition>) {
    setFields((fs) => fs.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  function addField() {
    setFields((fs) => [...fs, { name: "", data_type: "string", order: fs.length, anchor: "" }]);
  }

  function removeField(index: number) {
    setFields((fs) => fs.filter((_, i) => i !== index).map((f, i) => ({ ...f, order: i })));
  }

  async function runExtraction() {
    if (!doc) return;
    setExtracting(true);
    try {
      const result = await trademarksApi.extractFields({
        doc_id: doc.doc_id,
        page_start: pageStart,
        page_end: pageEnd,
        template,
        field_schema: template === "generic" ? fields.filter((f) => f.name && f.anchor) : [],
      });
      setExtractResult(result);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Extraction failed", "error");
    } finally {
      setExtracting(false);
    }
  }

  async function confirmIngest() {
    if (!doc || !extractResult) return;
    setIngesting(true);
    try {
      const result = await trademarksApi.ingest({
        doc_id: doc.doc_id,
        source_filename: doc.filename,
        page_start: pageStart,
        page_end: pageEnd,
        template,
        field_schema: template === "generic" ? fields.filter((f) => f.name && f.anchor) : [],
        records: extractResult.records,
      });
      qc.invalidateQueries({ queryKey: ["trademarks"] });
      qc.invalidateQueries({ queryKey: ["trademarks", "dashboard"] });
      notify(`Ingested ${result.ingested_count} record(s)`, "success");
      router.push("/trademarks");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Ingest failed", "error");
    } finally {
      setIngesting(false);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title="Document extraction"
        description="Upload a filed trademark document and extract structured records into your portfolio."
      />

      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex items-center gap-2">
            <div
              className={`flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-medium ${
                i === step
                  ? "bg-brand-600 text-white"
                  : i < step
                    ? "bg-success-subtle text-success"
                    : "bg-slate-50 text-slate-400"
              }`}
            >
              {i + 1}
            </div>
            <span className={`text-[13px] ${i === step ? "font-medium text-slate-900" : "text-slate-500"}`}>
              {label}
            </span>
            {i < STEPS.length - 1 && <div className="h-px w-8 bg-slate-200" />}
          </div>
        ))}
      </div>

      <Card>
        <CardBody className="space-y-4">
          {step === 0 && (
            <div className="space-y-4">
              <Field label="PDF document" hint="Only PDF files are supported.">
                <input
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="block w-full text-[13px] text-slate-600"
                />
              </Field>
              <Button disabled={!file} loading={uploading} onClick={handleUpload}>
                <UploadCloud className="h-4 w-4" />
                Upload
              </Button>
            </div>
          )}

          {step === 1 && doc && (
            <div className="space-y-4">
              <MessageBar intent="info">
                {doc.filename} — {doc.total_pages} page(s)
              </MessageBar>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Start page">
                  <Input
                    type="number"
                    min={1}
                    max={doc.total_pages}
                    value={pageStart}
                    onChange={(e) => setPageStart(Number(e.target.value))}
                  />
                </Field>
                <Field label="End page">
                  <Input
                    type="number"
                    min={1}
                    max={doc.total_pages}
                    value={pageEnd}
                    onChange={(e) => setPageEnd(Number(e.target.value))}
                  />
                </Field>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <Field label="Extraction template">
                <Select
                  value={template}
                  onChange={(e) => setTemplate(e.target.value as DocumentExtractTemplate)}
                >
                  <option value="generic">Generic (define fields manually)</option>
                  <option value="ip_india_journal">India Trade Marks Journal (vision-based)</option>
                </Select>
              </Field>

              {template === "generic" && (
                <div className="space-y-2">
                  <p className="text-xs font-medium uppercase tracking-[0.04em] text-slate-500">
                    Field schema
                  </p>
                  {fields.map((f, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <Input
                        placeholder="Field name"
                        value={f.name}
                        onChange={(e) => updateField(i, { name: e.target.value })}
                      />
                      <Input
                        placeholder="Anchor label, e.g. Applicant Name:"
                        value={f.anchor}
                        onChange={(e) => updateField(i, { anchor: e.target.value })}
                      />
                      <Select
                        className="w-32"
                        value={f.data_type}
                        onChange={(e) => updateField(i, { data_type: e.target.value as FieldDataType })}
                      >
                        <option value="string">String</option>
                        <option value="number">Number</option>
                        <option value="date">Date</option>
                        <option value="boolean">Boolean</option>
                      </Select>
                      <Button variant="ghost" size="icon" onClick={() => removeField(i)}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))}
                  <Button variant="outline" size="sm" onClick={addField}>
                    <Plus className="h-4 w-4" />
                    Add field
                  </Button>
                </div>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              {!extractResult ? (
                <Button loading={extracting} onClick={runExtraction}>
                  Run extraction (preview)
                </Button>
              ) : (
                <div className="space-y-3">
                  {extractResult.warnings.length > 0 && (
                    <MessageBar intent="warning">{extractResult.warnings.join("; ")}</MessageBar>
                  )}
                  <Table>
                    <THead>
                      <tr>
                        <TH>Page</TH>
                        <TH>Entry</TH>
                        <TH>Fields</TH>
                        <TH>OCR</TH>
                      </tr>
                    </THead>
                    <tbody>
                      {extractResult.records.map((r, i) => (
                        <TR key={i}>
                          <TD>{r.page_number}</TD>
                          <TD>{r.entry_index}</TD>
                          <TD className="max-w-md">
                            <pre className="whitespace-pre-wrap break-words text-xs text-slate-700">
                              {JSON.stringify(r.fields, null, 2)}
                            </pre>
                          </TD>
                          <TD>{r.used_ocr ? <Badge tone="amber">OCR</Badge> : "—"}</TD>
                        </TR>
                      ))}
                    </tbody>
                  </Table>
                  <Button loading={ingesting} onClick={confirmIngest}>
                    Confirm and ingest {extractResult.records.length} record(s)
                  </Button>
                </div>
              )}
            </div>
          )}
        </CardBody>
      </Card>

      <div className="flex items-center justify-between">
        <Button
          variant="outline"
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Back
        </Button>
        {step > 0 && step < STEPS.length - 1 && (
          <Button onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>Next</Button>
        )}
      </div>
    </div>
  );
}
