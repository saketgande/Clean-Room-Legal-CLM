"use client";

// The documents read by docstudio, and the way to add one. Opening a row shows
// its current version in the viewer.

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { docstudioApi } from "@/features/docstudio/api";
import { fmtRelative } from "@/lib/utils";

export default function DocstudioPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  const documents = useQuery({
    queryKey: ["docstudio", "documents"],
    queryFn: docstudioApi.documents,
  });

  const upload = useMutation({
    mutationFn: (file: File) => docstudioApi.upload(file),
    onSuccess: (version) => {
      queryClient.invalidateQueries({ queryKey: ["docstudio", "documents"] });
      router.push(`/docstudio/${version.id}`);
    },
    onError: (e: unknown) => {
      const detail = (e as { detail?: unknown; message?: string })?.detail;
      setError(typeof detail === "string" ? detail : ((e as Error)?.message ?? "Upload failed."));
    },
  });

  const rows = documents.data ?? [];

  return (
    <div className="dsl">
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      <div className="hd">
        <div>
          <h1>Documents</h1>
          <p className="sub">
            Contracts read into clauses, shown as they really look. A .pdf, .docx or .txt file.
          </p>
        </div>
        <div className="acts">
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.docx,.txt"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) {
                setError(null);
                upload.mutate(file);
              }
            }}
          />
          <button
            type="button"
            className="btn pri"
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? "Reading…" : "+ Add a document"}
          </button>
        </div>
      </div>

      {error ? <p className="err">{error}</p> : null}

      {documents.isLoading ? <p className="muted">Loading…</p> : null}
      {!documents.isLoading && !rows.length ? (
        <div className="empty">
          <p className="t">No documents yet</p>
          <p className="d">Add a contract and it will be read into clauses and shown here.</p>
        </div>
      ) : null}

      {rows.length ? (
        <table className="tbl">
          <thead>
            <tr>
              <th>Document</th>
              <th>Versions</th>
              <th>Pages</th>
              <th>Read by</th>
              <th>Added</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((document) => (
              <tr
                key={document.id}
                className={document.current ? "go" : ""}
                onClick={() =>
                  document.current ? router.push(`/docstudio/${document.current.id}`) : undefined
                }
              >
                <td className="nm">{document.title ?? document.current?.filename ?? "Untitled"}</td>
                <td>{document.version_count}</td>
                <td>{document.current?.page_count ?? "—"}</td>
                <td className="muted">{document.current?.read_by ?? "—"}</td>
                <td className="muted">
                  {document.current?.created_at ? fmtRelative(document.current.created_at) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

const CSS = `
.dsl{padding:22px 24px 40px;color:var(--ink);font:400 13px/1.55 var(--font-sans)}
.dsl .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.dsl .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.dsl .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.dsl .acts{margin-left:auto}
.dsl .btn{font:inherit;padding:7px 14px;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--ink);cursor:pointer}
.dsl .btn.pri{background:var(--accent);border-color:var(--accent);color:#fff}
.dsl .btn:disabled{opacity:.5;cursor:not-allowed}
.dsl .muted{color:var(--ink-2)} .dsl .err{color:var(--bad,#b91c1c);margin:0 0 12px}
.dsl .tbl{width:100%;border-collapse:separate;border-spacing:0;border:1px solid var(--border);border-radius:13px;overflow:hidden;background:var(--surface)}
.dsl .tbl th{text-align:left;font:600 11px/1 var(--font-sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3);padding:10px 14px;background:var(--surface-2)}
.dsl .tbl td{padding:11px 14px;border-top:1px solid var(--border);font-size:12.5px}
.dsl .tbl tr.go{cursor:pointer}
.dsl .tbl tr.go:hover td{background:var(--surface-2)}
.dsl .tbl .nm{font-weight:620;overflow-wrap:anywhere}
.dsl .empty{border:1px dashed var(--border);border-radius:13px;padding:40px 16px;text-align:center;background:var(--surface)}
.dsl .empty .t{margin:0;font-weight:650;font-size:14px} .dsl .empty .d{margin:6px 0 0;color:var(--ink-2);font-size:12.5px}
`;
