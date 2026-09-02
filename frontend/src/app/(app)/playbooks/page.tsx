"use client";

// Playbook catalog — list org playbooks in the new mockup style (scoped `.pbk`).
// Rule editing happens on the playbook detail page (/playbooks/[id]).

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Plus, Sparkles, X } from "lucide-react";
import { playbooksApi } from "@/lib/endpoints";
import { statusTone, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";

const ic = (p: string) => (
  <svg className="ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: p }} />
);

export default function PlaybooksPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();

  const [generateOpen, setGenerateOpen] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ["playbooks"],
    queryFn: playbooksApi.list,
  });
  const playbooks = data ?? [];

  function onCreated(id: string) {
    qc.invalidateQueries({ queryKey: ["playbooks"] });
    notify("Playbook created", "success");
    router.push(`/playbooks/${id}`);
  }

  return (
    <div className="pbk">
      <style dangerouslySetInnerHTML={{ __html: PBK_CSS }} />
      <div className="hd">
        <div>
          <h1>Playbooks</h1>
          <p className="sub">Negotiation rulebooks used to review contracts and surface deviations.</p>
        </div>
        <div className="acts">
          <button className="btn" onClick={() => router.push("/playbooks/build")}>
            Build with AI
          </button>
          <button className="btn pri" onClick={() => setGenerateOpen(true)}>
            + New playbook
          </button>
        </div>
      </div>

      {playbooks.length > 0 && (
        <div className="stats">
          <div className="stat">
            <span className="si a">{ic('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>')}</span>
            <div><div className="sv">{playbooks.length}</div><div className="sl">playbook{playbooks.length === 1 ? "" : "s"}</div></div>
          </div>
          <div className="stat">
            <span className="si g">{ic('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>')}</span>
            <div><div className="sv">{playbooks.filter((p) => p.status === "published").length}</div><div className="sl">published</div></div>
          </div>
          <div className="stat">
            <span className="si b">{ic('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>')}</span>
            <div><div className="sv">{playbooks.filter((p) => p.status === "draft").length}</div><div className="sl">draft</div></div>
          </div>
          <div className="stat">
            <span className="si">{ic('<path d="M21 8v13H3V8"/><path d="M1 3h22v5H1z"/><path d="M10 12h4"/>')}</span>
            <div><div className="sv">{playbooks.filter((p) => p.status === "archived").length}</div><div className="sl">archived</div></div>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="pcard skel" />
          ))}
        </div>
      ) : error ? (
        <div className="empty err">
          {error instanceof Error ? error.message : "Something went wrong"}
        </div>
      ) : playbooks.length === 0 ? (
        <div className="empty">
          <div className="eicon">
            <BookOpen className="ic" />
          </div>
          <p className="et">No playbooks yet</p>
          <p className="ed">
            Create a playbook manually or generate one with AI to start reviewing contracts against
            your standards.
          </p>
          <button className="btn pri" onClick={() => setGenerateOpen(true)}>
            <Plus className="ic" />
            New playbook
          </button>
        </div>
      ) : (
        <div className="grid">
          {playbooks.map((p) => (
            <div key={p.id} className="pcard">
              <div className="pch">
                <h3>{p.name}</h3>
                <span className={`bdg ${statusTone(p.status)}`}>{titleCase(p.status)}</span>
              </div>
              <p className="pd">{p.description || "No description provided."}</p>
              <div className="pf">
                <button className="btn sm" onClick={() => router.push(`/playbooks/${p.id}`)}>
                  Open
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <GeneratePlaybookModal
        open={generateOpen}
        onClose={() => setGenerateOpen(false)}
        onCreated={onCreated}
      />
    </div>
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
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const canSubmit = mode === "file" ? !!file : pastedText.trim().length > 50;

  useEffect(() => {
    if (!open) return;
    firstFieldRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

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

  if (!open) return null;

  return (
    <div className="ovl" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="Generate playbook with AI">
        <div className="mhd">
          <h2>Generate playbook with AI</h2>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <X className="ic" />
          </button>
        </div>
        <div className="mbody">
          <p className="hint">
            Upload a template, an exemplar contract, or your existing playbook — AI reads it and
            drafts standard positions you can review and edit.
          </p>

          <div className="seg">
            {(["file", "text"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={mode === m ? "on" : ""}
                onClick={() => setMode(m)}
              >
                {m === "file" ? "Upload file" : "Paste text"}
              </button>
            ))}
          </div>

          {mode === "file" ? (
            <div className="field">
              <label>Document</label>
              <input
                ref={firstFieldRef}
                type="file"
                accept=".pdf,.docx,.txt,.md,application/pdf,text/plain"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="filein"
              />
              <span className="fhint">PDF, DOCX, or text file</span>
            </div>
          ) : (
            <div className="field">
              <label>Paste standard terms / playbook text</label>
              <textarea
                rows={6}
                value={pastedText}
                onChange={(e) => setPastedText(e.target.value)}
                placeholder="Paste contract clauses or playbook text…"
              />
            </div>
          )}

          <div className="field">
            <label>Playbook name</label>
            <input
              type="text"
              placeholder="e.g. Vendor MSA Playbook"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <span className="fhint">Optional — AI suggests one if left blank</span>
          </div>
          <div className="field">
            <label>Contract type</label>
            <input
              type="text"
              value={contractType}
              onChange={(e) => setContractType(e.target.value)}
            />
            <span className="fhint">Optional — e.g. saas, nda, msa</span>
          </div>
          <div className="field">
            <label>Instructions</label>
            <input
              type="text"
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
            />
            <span className="fhint">Optional — e.g. &apos;we are the customer; be strict on liability&apos;</span>
          </div>
          <p className="note">This runs an AI analysis and may take ~10–30 seconds.</p>
        </div>
        <div className="mft">
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn pri" onClick={submit} disabled={!canSubmit || busy}>
            <Sparkles className="ic" />
            {busy ? "Generating…" : "Generate"}
          </button>
        </div>
      </div>
    </div>
  );
}

const PBK_CSS = `
.pbk{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .pbk{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)}
.pbk .ic{width:15px;height:15px;flex:none}
.pbk .hd{display:flex;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:18px}
.pbk .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.pbk .hd .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:640px}
.pbk .acts{margin-left:auto;display:flex;gap:8px}
.pbk .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:20px}
.pbk .stat{display:flex;align-items:center;gap:12px;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:14px 16px}
.pbk .si{flex:none;display:grid;place-items:center;width:38px;height:38px;border-radius:10px;background:var(--surface-2);color:var(--ink-2)}
.pbk .si.a{background:var(--accent-soft);color:var(--accent)} .pbk .si.g{background:var(--good-soft);color:var(--good)} .pbk .si.b{background:var(--warn-soft);color:var(--warn)}
.pbk .si .ic{width:19px;height:19px}
.pbk .sv{font-size:21px;font-weight:700;letter-spacing:-.02em;line-height:1} .pbk .sl{margin-top:3px;font-size:11.5px;color:var(--ink-2)}






.pbk .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.pbk .pcard{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);padding:15px 16px;display:flex;flex-direction:column;gap:10px;transition:.12s;min-height:150px}
.pbk .pcard:not(.skel):hover{border-color:var(--accent);transform:translateY(-1px)}
.pbk .pcard.skel{background:var(--surface-2);border-style:dashed;animation:pbkpulse 1.4s ease-in-out infinite}
@keyframes pbkpulse{50%{opacity:.55}}
.pbk .pch{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}
.pbk .pch h3{margin:0;font-size:14px;font-weight:660}
.pbk .bdg{flex:none;font:600 9.5px var(--sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px}
.pbk .bdg.green{background:var(--good-soft);color:var(--good)}
.pbk .bdg.amber{background:var(--warn-soft);color:var(--warn)}
.pbk .bdg.red{background:var(--crit-soft);color:var(--crit)}
.pbk .bdg.slate{background:var(--surface-2);color:var(--ink-3)}
.pbk .pd{flex:1;margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.55;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.pbk .pf{display:flex}


.pbk .eicon{width:44px;height:44px;border-radius:11px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;margin-bottom:8px}
.pbk .et{margin:0;font-size:14px;font-weight:660;color:var(--ink)}
.pbk .ed{margin:0 0 10px;font-size:12.5px;color:var(--ink-2);max-width:380px}

.pbk .ovl{position:fixed;inset:0;background:rgba(12,15,22,.5);display:grid;place-items:center;z-index:50;padding:24px}
.pbk .modal{width:100%;max-width:460px;max-height:min(88vh,720px);display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:var(--pop)}
.pbk .mhd{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid var(--border)}
.pbk .mhd h2{margin:0;font-size:15px;font-weight:660}
.pbk .iconbtn{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;color:var(--ink-2);background:none;border:0;cursor:pointer}
.pbk .iconbtn:hover{background:var(--surface-2)}
.pbk .mbody{padding:16px 18px;overflow:auto;display:flex;flex-direction:column;gap:14px}
.pbk .hint{margin:0;font-size:12.5px;color:var(--ink-2);line-height:1.55}
.pbk .seg{display:flex;gap:2px;background:var(--surface-2);border:1px solid var(--border);border-radius:9px;padding:3px}
.pbk .seg button{flex:1;padding:7px 10px;border-radius:7px;font-weight:600;font-size:12.5px;color:var(--ink-2);background:none;border:0;cursor:pointer}
.pbk .seg button.on{background:var(--accent-soft);color:var(--accent)}
.pbk .field{display:flex;flex-direction:column;gap:5px}
.pbk .field label{font:600 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3)}
.pbk .field input[type=text],.pbk .field textarea{border:1px solid var(--border-strong);border-radius:8px;padding:8px 10px;background:var(--surface);color:var(--ink);font-size:12.5px;font-family:var(--sans)}
.pbk .field textarea{resize:vertical;line-height:1.5}
.pbk .field input[type=text]:focus,.pbk .field textarea:focus{outline:none;border-color:var(--accent)}
.pbk .field .fhint{font-size:11px;color:var(--ink-3)}
.pbk .filein{font-size:12.5px;color:var(--ink-2)}
.pbk .filein::file-selector-button{margin-right:10px;border:0;border-radius:8px;background:var(--accent-soft);color:var(--accent);padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer}
.pbk .filein::file-selector-button:hover{filter:brightness(0.97)}
.pbk .note{margin:0;font-size:11px;color:var(--ink-3)}
.pbk .mft{display:flex;justify-content:flex-end;gap:8px;padding:14px 18px;border-top:1px solid var(--border)}
`;
