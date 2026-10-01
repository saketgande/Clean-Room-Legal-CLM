"use client";

// A Word file drawn the way Word draws it, tracked changes included.
//
// `renderChanges` matters for contracts: a counterparty's redline lives in the
// file as tracked changes, and a viewer that silently applies or drops them
// shows a document neither side sent.

import { useEffect, useRef, useState } from "react";

import { DOCX_MIME } from "./index";

export async function renderDocx(file: Blob, host: HTMLElement): Promise<void> {
  const { renderAsync } = await import("docx-preview");
  await renderAsync(file, host, undefined, {
    inWrapper: true,
    breakPages: true,
    renderChanges: true,
    renderHeaders: true,
    renderFooters: true,
    // The file's own fonts are not on this machine; using its stylesheet
    // without them is closer to the original than ignoring it.
    ignoreFonts: false,
  });
  // docx-preview copies a hyperlink's target from the file into `href` as-is,
  // and the file is somebody else's: a `javascript:` link would run in this
  // origin on click. Keep web, mail and in-document links; drop the rest.
  for (const a of host.querySelectorAll<HTMLAnchorElement>("a[href]")) {
    const href = a.getAttribute("href") ?? "";
    if (!href.startsWith("#") && !/^(https?|mailto):/i.test(href.trim())) a.removeAttribute("href");
  }
}

export function DocxRenderer({ bytes }: { bytes: ArrayBuffer }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const host = hostRef.current;
    if (!host) return;

    (async () => {
      try {
        if (cancelled) return;
        host.replaceChildren();
        await renderDocx(new Blob([bytes], { type: DOCX_MIME }), host);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "This Word file could not be displayed.");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [bytes]);

  return (
    <div className="docxv">
      {error ? <p className="err">{error}</p> : null}
      <div ref={hostRef} />
    </div>
  );
}
