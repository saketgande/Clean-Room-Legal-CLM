"use client";

// The contract's current version in ONLYOFFICE Docs (backend:
// app/contract_files/editor.py). The editor's own script comes from its
// server; the config is signed by our backend. Saving there makes the
// contract's next version — the page only has to refresh when it closes.

import { useEffect, useRef, useState } from "react";
import { contractsApi } from "@/lib/endpoints";

type DocEditor = { destroyEditor: () => void };
declare global {
  interface Window {
    DocsAPI?: { DocEditor: new (id: string, config: Record<string, unknown>) => DocEditor };
  }
}

const loaded: Record<string, Promise<void>> = {};

function loadScript(src: string): Promise<void> {
  loaded[src] ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => {
      delete loaded[src];
      reject(new Error("The Word editor isn't answering. Is the onlyoffice service running?"));
    };
    document.head.appendChild(s);
  });
  return loaded[src];
}

export function WordEditor({ contractId }: { contractId: string }) {
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [readOnly, setReadOnly] = useState<string | null>(null);
  const editor = useRef<DocEditor | null>(null);
  const holder = `oo-${contractId}`;

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await contractsApi.editorConfig(contractId);
        if (!res.enabled) throw new Error("The Word editor isn't set up on this server.");
        await loadScript(`${res.server}/web-apps/apps/api/documents/api.js`);
        if (cancelled || !window.DocsAPI) return;
        setReadOnly(res.read_only_reason ?? null);
        setNote(res.original
          ? `Version ${res.version_number} — the Word file as uploaded.`
          : `Version ${res.version_number} came as a PDF or was generated, so it opens as Word built from its text.`);
        editor.current = new window.DocsAPI.DocEditor(holder, res.config);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Couldn't open the editor.");
      }
    })();
    return () => {
      cancelled = true;
      editor.current?.destroyEditor();
      editor.current = null;
    };
  }, [contractId, holder]);

  return (
    <div className="wordedit">
      {error ? <div className="we-err">{error}</div>
        : readOnly ? <div className="we-ro">Read-only: {readOnly}</div>
        : note ? <div className="we-note">{note} Save writes a new version; your edits are tracked changes.</div> : null}
      <div className="we-frame"><div id={holder} /></div>
    </div>
  );
}

export const WORD_EDITOR_CSS = `
.clm .wordedit{display:flex;flex-direction:column;height:100%;min-height:0}
.clm .we-note{flex:none;padding:7px 14px;font-size:12px;color:var(--ink-2);background:var(--surface-2);border-bottom:1px solid var(--border)}
.clm .we-ro{flex:none;padding:8px 14px;font-size:12.5px;font-weight:500;color:var(--warn);background:var(--warn-soft);border-bottom:1px solid var(--border)}
.clm .we-err{flex:none;margin:16px;padding:12px 14px;border-radius:9px;background:var(--crit-soft);color:var(--crit);font-size:13px}
.clm .we-frame{flex:1;min-height:560px}
.clm .we-frame > div, .clm .we-frame iframe{width:100%;height:100%}
`;
