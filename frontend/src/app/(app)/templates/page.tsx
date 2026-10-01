"use client";

// Drafting templates — our standard paper, one per playbook. A request's
// "Draft" fills the template for its kind of agreement; the matching playbook
// then reviews the result, so a template that disagrees with its playbook gets
// our own paper redlined. Edits save as new versions (the shipped text stays
// the default); every earlier wording stays in History.

import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { draftingTemplatesApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { useAuth } from "@/lib/auth";
import { can } from "@/lib/intake";
import type { DraftingTemplate, DraftingTemplateVersion } from "@/lib/types";

type Tab = "edit" | "preview" | "history";

export default function TemplatesPage() {
  const { data: list, isLoading } = useQuery({ queryKey: ["drafting-templates"], queryFn: draftingTemplatesApi.list });
  const [key, setKey] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const selected = key ?? list?.[0]?.key ?? null;

  function pick(next: string) {
    if (next === selected) return;
    if (dirty && !window.confirm("You have unsaved changes to this template. Leave without saving?")) return;
    setDirty(false);
    setKey(next);
  }

  return (
    <div className="tpl">
      <style dangerouslySetInnerHTML={{ __html: TPL_CSS }} />
      <div className="hd">
        <h1>Templates</h1>
        <p className="sub">
          The standard paper each request is drafted from. Each one is written to pass the playbook that reviews it,
          so a fresh draft on our template comes back without redlines.
        </p>
      </div>
      <div className="cols">
        <nav className="list" aria-label="Templates">
          {isLoading && <div className="muted">Loading…</div>}
          {list?.map((t) => (
            <button key={t.key} className={`item${t.key === selected ? " on" : ""}`} onClick={() => pick(t.key)}>
              <span className="nm">{t.name}</span>
              <span className="meta">
                {t.customized ? <span className="bdg ed">Edited · v{t.version}</span> : <span className="bdg">Default</span>}
                <span className="pb">{t.playbook ?? "No playbook reviews it"}</span>
              </span>
            </button>
          ))}
        </nav>
        {selected && <Editor key={selected} templateKey={selected} onDirty={setDirty} />}
      </div>
    </div>
  );
}

function Editor({ templateKey, onDirty }: { templateKey: string; onDirty: (d: boolean) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const canEdit = can(user, "playbook:update");
  const { data: tpl } = useQuery({ queryKey: ["drafting-template", templateKey], queryFn: () => draftingTemplatesApi.get(templateKey) });
  const [body, setBody] = useState("");
  const [note, setNote] = useState("");
  const [tab, setTab] = useState<Tab>("edit");
  const [busy, setBusy] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (tpl) setBody(tpl.body);
  }, [tpl]);
  const dirty = !!tpl && body !== tpl.body;
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  function insert(name: string) {
    const el = area.current;
    const token = `{${name}}`;
    if (!el) return setBody((b) => b + token);
    const { selectionStart: a, selectionEnd: b } = el;
    setBody((cur) => cur.slice(0, a) + token + cur.slice(b));
    requestAnimationFrame(() => {
      el.focus();
      el.selectionStart = el.selectionEnd = a + token.length;
    });
  }

  function landed(next: DraftingTemplate, message: string) {
    qc.setQueryData(["drafting-template", templateKey], next);
    qc.invalidateQueries({ queryKey: ["drafting-templates"] });
    qc.invalidateQueries({ queryKey: ["drafting-template-versions", templateKey] });
    setBody(next.body);
    setNote("");
    notify(message, "success");
  }

  async function save() {
    setBusy(true);
    try {
      landed(await draftingTemplatesApi.save(templateKey, body, note || undefined), "Template saved — new drafts use it");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't save the template", "error");
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (!window.confirm("Go back to the default wording? Your edited versions stay in History.")) return;
    setBusy(true);
    try {
      landed(await draftingTemplatesApi.reset(templateKey, note || undefined), "Back to the default template");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't reset the template", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!tpl) return <section className="ed muted">Loading…</section>;

  return (
    <section className="ed">
      <div className="edhd">
        <div>
          <h2>{tpl.name}</h2>
          <div className="sub2">
            Reviewed by <b>{tpl.playbook ?? "no playbook"}</b>
            {tpl.customized
              ? ` · edited, version ${tpl.version}${tpl.updated_by ? ` by ${tpl.updated_by}` : ""}`
              : " · the default wording"}
          </div>
        </div>
        <div className="tabs" role="tablist">
          {(["edit", "preview", "history"] as Tab[]).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t === "edit" ? "Edit" : t === "preview" ? "Preview" : "History"}
            </button>
          ))}
        </div>
      </div>

      {tab === "edit" && (
        <div className="editgrid">
          <div className="main">
            <textarea
              ref={area}
              className="body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              spellCheck={false}
              readOnly={!canEdit}
              aria-label={`${tpl.name} template text`}
            />
            {canEdit && (
              <div className="bar">
                <input
                  className="note"
                  placeholder="What changed? (optional, shown in History)"
                  value={note}
                  maxLength={300}
                  onChange={(e) => setNote(e.target.value)}
                />
                <button className="btn" disabled={!dirty || busy} onClick={() => setBody(tpl.body)}>
                  Discard changes
                </button>
                <button className="btn" disabled={busy || !tpl.customized} onClick={reset}>
                  Reset to default
                </button>
                <button className="btn pri" disabled={!dirty || busy} onClick={save}>
                  {busy ? "Saving…" : "Save"}
                </button>
              </div>
            )}
          </div>
          <aside className="ph">
            <h3>Filled in when drafting</h3>
            <p className="muted">Click one to put it at the cursor. Write a literal brace as {"{{"} or {"}}"}.</p>
            {tpl.placeholders.map((p) => (
              <button key={p.name} className="phi" onClick={() => insert(p.name)} disabled={!canEdit} title={`e.g. ${p.sample}`}>
                <code>{`{${p.name}}`}</code>
                <span>{p.about}</span>
              </button>
            ))}
          </aside>
        </div>
      )}
      {tab === "preview" && <Preview templateKey={templateKey} body={body} />}
      {tab === "history" && (
        <History templateKey={templateKey} onUse={(text) => { setBody(text); setTab("edit"); }} canEdit={canEdit} />
      )}
    </section>
  );
}

function Preview({ templateKey, body }: { templateKey: string; body: string }) {
  const { data, error, isFetching } = useQuery({
    queryKey: ["drafting-template-preview", templateKey, body],
    queryFn: () => draftingTemplatesApi.preview(templateKey, body),
    retry: false,
  });
  if (error) return <div className="err">{error instanceof Error ? error.message : "This template can't be filled."}</div>;
  return (
    <div className="pv">
      <p className="muted">Filled with example values{isFetching ? " …" : ""} — this is what a draft will read like.</p>
      <pre className="doc">{data?.text ?? ""}</pre>
    </div>
  );
}

function History({ templateKey, onUse, canEdit }: { templateKey: string; onUse: (body: string) => void; canEdit: boolean }) {
  const { data } = useQuery({
    queryKey: ["drafting-template-versions", templateKey],
    queryFn: () => draftingTemplatesApi.versions(templateKey),
  });
  const [open, setOpen] = useState<number | null>(null);
  const rows = useMemo<DraftingTemplateVersion[]>(() => data ?? [], [data]);
  if (!rows.length) return <div className="muted pad">Never edited — drafts use the default wording.</div>;
  return (
    <ul className="hist">
      {rows.map((v) => (
        <li key={v.version}>
          <button className="hrow" onClick={() => setOpen(open === v.version ? null : v.version)}>
            <b>Version {v.version}</b>
            <span>{v.is_default ? "Back to the default" : v.note || "No note"}</span>
            <span className="muted">
              {v.created_by ?? "Someone"} · {new Date(v.created_at).toLocaleString()}
            </span>
          </button>
          {open === v.version && (
            <div className="hbody">
              <pre className="doc">{v.body}</pre>
              {canEdit && (
                <button className="btn" onClick={() => onUse(v.body)}>
                  Load this wording into the editor
                </button>
              )}
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

const TPL_CSS = `
.tpl{--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;padding:22px 24px 40px;color:var(--ink);font:400 13px/1.5 var(--font-sans)}
.dark .tpl{--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.tpl .hd{margin-bottom:16px}
.tpl .hd h1{margin:0;font-size:19px;font-weight:680;letter-spacing:-.015em}
.tpl .sub{margin:4px 0 0;font-size:13px;color:var(--ink-2);max-width:720px}
.tpl .muted{color:var(--ink-3);font-size:12.5px}
.tpl .pad{padding:16px}
.tpl .cols{display:grid;grid-template-columns:260px minmax(0,1fr);gap:16px;align-items:start}
@media (max-width:900px){.tpl .cols{grid-template-columns:minmax(0,1fr)}}
.tpl .list{display:flex;flex-direction:column;gap:6px}
.tpl .item{display:flex;flex-direction:column;gap:4px;text-align:left;border:1px solid var(--border);border-radius:11px;background:var(--surface);padding:10px 12px;cursor:pointer;color:var(--ink);box-shadow:var(--shadow)}
.tpl .item:hover{border-color:var(--accent)}
.tpl .item.on{border-color:var(--accent);background:var(--accent-soft)}
.tpl .nm{font-weight:640;font-size:13px}
.tpl .meta{display:flex;flex-direction:column;gap:3px}
.tpl .pb{font-size:11.5px;color:var(--ink-3)}
.tpl .bdg{align-self:flex-start;font:600 9.5px var(--font-sans);letter-spacing:.04em;text-transform:uppercase;padding:2px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-3)}
.tpl .bdg.ed{background:var(--warn-soft);color:var(--warn)}
.tpl .ed{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow);min-width:0}
.tpl .edhd{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:14px 16px 0;border-bottom:1px solid var(--border)}
.tpl .edhd h2{margin:0;font-size:15px;font-weight:660}
.tpl .sub2{font-size:12px;color:var(--ink-2);margin:2px 0 10px}
.tpl .tabs{display:flex;gap:2px}
.tpl .tabs button{padding:8px 12px;border:none;border-bottom:2px solid transparent;margin-bottom:-1px;background:none;color:var(--ink-2);font-weight:600;font-size:12.5px;cursor:pointer}
.tpl .tabs button.on{color:var(--accent);border-bottom-color:var(--accent)}
.tpl .editgrid{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:0}
@media (max-width:1100px){.tpl .editgrid{grid-template-columns:minmax(0,1fr)}}
.tpl .main{display:flex;flex-direction:column;min-width:0;border-right:1px solid var(--border)}
.tpl textarea.body{width:100%;min-height:62vh;resize:vertical;border:none;outline:none;padding:16px;background:var(--surface);color:var(--ink);font:12.5px/1.6 var(--mono);white-space:pre-wrap}
.tpl .bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;padding:10px 12px;border-top:1px solid var(--border);background:var(--surface-2);border-radius:0 0 0 13px}
.tpl .note{flex:1 1 220px;min-width:0;border:1px solid var(--border);border-radius:8px;padding:7px 10px;background:var(--surface);color:var(--ink);font:inherit}
.tpl .btn{border:1px solid var(--border);border-radius:8px;padding:7px 12px;background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;cursor:pointer}
.tpl .btn:disabled{opacity:.5;cursor:default}
.tpl .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink,#fff)}
.tpl .ph{padding:14px;display:flex;flex-direction:column;gap:6px;max-height:70vh;overflow:auto}
.tpl .ph h3{margin:0;font-size:12.5px;font-weight:650}
.tpl .ph p{margin:0 0 4px}
.tpl .phi{display:flex;flex-direction:column;gap:1px;text-align:left;border:1px solid var(--border);border-radius:8px;padding:6px 8px;background:var(--surface);color:var(--ink);cursor:pointer}
.tpl .phi:hover:not(:disabled){border-color:var(--accent)}
.tpl .phi code{font:11.5px var(--mono);color:var(--accent)}
.tpl .phi span{font-size:11.5px;color:var(--ink-2)}
.tpl .pv{padding:14px 16px}
.tpl .doc{margin:8px 0 0;white-space:pre-wrap;font:12.5px/1.65 var(--mono);color:var(--ink);background:var(--surface-2);border:1px solid var(--border);border-radius:10px;padding:16px;max-height:70vh;overflow:auto}
.tpl .err{margin:16px;padding:12px 14px;border-radius:9px;background:var(--crit-soft);color:var(--crit);font-size:13px}
.tpl .hist{list-style:none;margin:0;padding:8px 12px 12px}
.tpl .hist li{border-bottom:1px solid var(--border)}
.tpl .hrow{display:grid;grid-template-columns:90px minmax(0,1fr) auto;gap:10px;width:100%;text-align:left;background:none;border:none;padding:10px 4px;color:var(--ink);cursor:pointer;font-size:12.5px}
.tpl .hbody{padding:0 4px 12px;display:flex;flex-direction:column;gap:8px;align-items:flex-start}
`;
