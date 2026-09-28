// The typed client for /docstudio. Nothing else in the app calls these routes.

import { apiBytes, apiDownload, apiFetch } from "@/lib/api";
import type { DsDocument, DsVersionDetail, DsVersionSummary } from "./types";

export const docstudioApi = {
  documents: () => apiFetch<DsDocument[]>("/docstudio/documents"),

  version: (versionId: string) =>
    apiFetch<DsVersionDetail>(`/docstudio/versions/${versionId}`),

  /** The bytes the version was read from, for the page to draw itself. */
  file: (versionId: string) => apiBytes(`/docstudio/versions/${versionId}/file`),

  download: (versionId: string, filename: string) =>
    apiDownload(`/docstudio/versions/${versionId}/file`, filename),

  /** `documentId` makes this a new version of that document. */
  upload: (file: File, documentId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (documentId) form.append("document_id", documentId);
    return apiFetch<DsVersionSummary>("/docstudio/documents", { form });
  },
};
