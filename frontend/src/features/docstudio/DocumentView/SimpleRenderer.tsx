"use client";

// The three cases that need no library: a picture, plain text, and the honest
// "this cannot be shown here" panel.

import { useEffect, useState } from "react";

export function ImageRenderer({
  bytes,
  mimeType,
  filename,
}: {
  bytes: ArrayBuffer;
  mimeType: string;
  filename: string;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    // The media type is one of the two this renderer is chosen for, so the
    // browser cannot be talked into running the bytes as a script.
    const objectUrl = URL.createObjectURL(new Blob([bytes], { type: mimeType }));
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [bytes, mimeType]);

  // eslint-disable-next-line @next/next/no-img-element -- a blob of unknown size, not a static asset
  return url ? <img className="imgv" src={url} alt={filename} /> : null;
}

export function TextRenderer({ bytes }: { bytes: ArrayBuffer }) {
  // Replacement characters rather than a thrown error: a mis-declared encoding
  // should cost a few characters, not the whole view.
  const text = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  return <pre className="textv">{text}</pre>;
}

export function UnsupportedRenderer({
  mimeType,
  filename,
  onDownload,
}: {
  mimeType: string;
  filename: string;
  onDownload: () => void;
}) {
  return (
    <div className="cannot">
      <p className="t">This file cannot be shown here</p>
      <p className="d">
        {filename} is a <code>{mimeType}</code> file. Download it to open in the program it
        belongs to — nothing about it has been lost.
      </p>
      <button type="button" className="btn" onClick={onDownload}>
        Download the file
      </button>
    </div>
  );
}
