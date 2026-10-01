"use client";

// Which renderer draws a file, and the dispatch that picks one.
//
// Matching is by exact media type, never by prefix. `startsWith("image/")`
// also accepts image/svg+xml, which is a scripting format wearing an image
// type — and these bytes are whatever somebody uploaded.

import { DocxRenderer } from "./DocxRenderer";
import { PdfRenderer } from "./PdfRenderer";
import { ImageRenderer, TextRenderer, UnsupportedRenderer } from "./SimpleRenderer";

export const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export type RendererKind = "pdf" | "docx" | "image" | "text" | "none";

export function rendererFor(mimeType: string): RendererKind {
  if (mimeType === "application/pdf") return "pdf";
  if (mimeType === DOCX_MIME) return "docx";
  if (mimeType === "image/png" || mimeType === "image/jpeg") return "image";
  if (mimeType === "text/plain") return "text";
  return "none";
}

export function DocumentView({
  bytes,
  mimeType,
  filename,
  onDownload,
}: {
  bytes: ArrayBuffer;
  mimeType: string;
  filename: string;
  onDownload: () => void;
}) {
  switch (rendererFor(mimeType)) {
    case "pdf":
      return <PdfRenderer bytes={bytes} />;
    case "docx":
      return <DocxRenderer bytes={bytes} />;
    case "image":
      return <ImageRenderer bytes={bytes} mimeType={mimeType} filename={filename} />;
    case "text":
      return <TextRenderer bytes={bytes} />;
    default:
      return (
        <UnsupportedRenderer mimeType={mimeType} filename={filename} onDownload={onDownload} />
      );
  }
}
