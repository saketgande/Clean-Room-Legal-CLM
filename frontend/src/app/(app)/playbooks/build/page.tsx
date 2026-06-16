"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  Paperclip,
  Send,
  Loader2,
  Save,
  FileText,
  X,
  Sparkles,
} from "lucide-react";
import { playbooksApi } from "@/lib/endpoints";
import type { ExtractedDoc, PlaybookDraftRule } from "@/lib/types";
import { Badge, Button, Input } from "@/components/ui";
import { useToast } from "@/components/toast";
import { titleCase } from "@/lib/utils";

type Msg = { role: "user" | "assistant"; content: string };

function riskTone(level?: string | null): "red" | "amber" | "slate" {
  if (level === "critical" || level === "high") return "red";
  if (level === "medium") return "amber";
  return "slate";
}

export default function BuildPlaybookPage() {
  const router = useRouter();
  const { notify } = useToast();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [docs, setDocs] = useState<ExtractedDoc[]>([]);
  const [rules, setRules] = useState<PlaybookDraftRule[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [view, setView] = useState<"chat" | "draft">("chat");
  const fileRef = useRef<HTMLInputElement>(null);

  async function attach(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      const form = new FormData();
      Array.from(files).forEach((f) => form.append("files", f));
      const out = await playbooksApi.buildExtract(form);
      setDocs((d) => [...d, ...out]);
      notify(`Attached ${out.length} file(s)`, "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Upload failed", "error");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    const nextMsgs: Msg[] = [...messages, { role: "user", content: text }];
    setMessages(nextMsgs);
    setBusy(true);
    try {
      const res = await playbooksApi.buildChat({
        message: text,
        conversation: messages,
        current_rules: rules,
        documents: docs.map((d) => ({ filename: d.filename, content: d.content })),
        name: name || undefined,
      });
      setMessages([...nextMsgs, { role: "assistant", content: res.reply }]);
      setRules(res.rules);
      if (!name && res.suggested_name) setName(res.suggested_name);
    } catch (e) {
      setMessages([
        ...nextMsgs,
        { role: "assistant", content: "Sorry — that request failed. Please try again." },
      ]);
      notify(e instanceof Error ? e.message : "Chat failed", "error");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!name.trim() || rules.length === 0) return;
    setSaving(true);
    try {
      const pb = await playbooksApi.buildSave({ name: name.trim(), rules });
      notify("Playbook saved as a draft", "success");
      router.push(`/playbooks/${pb.id}`);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="shrink-0 border-b border-slate-200 bg-slate-100 px-4 py-3">
        <Link
          href="/playbooks"
          className="mb-1 inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Playbooks
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="flex items-center gap-2 text-lg font-semibold text-slate-900">
            <Sparkles className="h-5 w-5 text-brand-600" /> Build playbook with AI
          </h1>
          <div className="flex items-center gap-2">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Playbook name"
              className="w-56"
            />
            <Button
              onClick={save}
              loading={saving}
              disabled={!name.trim() || rules.length === 0}
            >
              <Save className="h-4 w-4" /> Save ({rules.length})
            </Button>
          </div>
        </div>
      </div>

      <div className="flex shrink-0 gap-1 border-b border-slate-200 bg-slate-100 p-1 lg:hidden">
        {(["chat", "draft"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
              view === v ? "bg-brand-50 text-brand-700" : "text-slate-500 hover:text-slate-700"
            }`}
          >
            {v === "chat" ? "Chat" : `Draft (${rules.length})`}
          </button>
        ))}
      </div>

      <div className="flex flex-1 overflow-hidden">
        <section
          className={`min-w-0 flex-1 flex-col lg:flex lg:border-r lg:border-slate-200 ${
            view === "chat" ? "flex" : "hidden lg:flex"
          }`}
        >
          <div className="flex-1 space-y-3 overflow-y-auto p-4">
            {messages.length === 0 && (
              <div className="rounded-lg border border-dashed border-slate-300 p-4 text-sm text-slate-500">
                Attach a template, an exemplar contract, or your existing playbook, then say
                <span className="font-medium"> “build a playbook from these.”</span> Iterate with
                things like <span className="font-medium">“make the liability rule stricter”</span>{" "}
                or <span className="font-medium">“add a data-privacy clause.”</span>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div
                  className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm ${
                    m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-800"
                  }`}
                >
                  {m.content}
                </div>
              </div>
            ))}
            {busy && (
              <div className="flex items-center gap-2 text-xs text-slate-400">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking…
              </div>
            )}
          </div>

          {docs.length > 0 && (
            <div className="flex flex-wrap gap-1.5 border-t border-slate-100 px-4 py-2">
              {docs.map((d, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
                >
                  <FileText className="h-3 w-3" /> {d.filename}
                  <button
                    onClick={() => setDocs(docs.filter((_, idx) => idx !== i))}
                    className="text-slate-400 hover:text-rose-600"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="flex items-end gap-2 border-t border-slate-200 p-3">
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md"
              className="hidden"
              onChange={(e) => attach(e.target.files)}
            />
            <Button
              variant="outline"
              size="icon"
              loading={uploading}
              onClick={() => fileRef.current?.click()}
              title="Attach files"
            >
              <Paperclip className="h-4 w-4" />
            </Button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={1}
              placeholder="Ask the assistant to build or change the playbook…"
              className="flex-1 resize-none rounded-md border border-slate-200 p-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
            />
            <Button onClick={send} loading={busy} disabled={!input.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </section>

        <section
          className={`w-full shrink-0 flex-col overflow-y-auto bg-slate-50 lg:flex lg:w-[34rem] xl:w-[40rem] ${
            view === "draft" ? "flex" : "hidden lg:flex"
          }`}
        >
          <div className="sticky top-0 z-10 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-sm font-semibold text-slate-700">
            Draft playbook · {rules.length} rule{rules.length === 1 ? "" : "s"}
          </div>
          <div className="space-y-2 p-3">
            {rules.length === 0 ? (
              <p className="text-sm text-slate-400">Rules will appear here as you build.</p>
            ) : (
              rules.map((r, i) => (
                <div key={i} className="rounded-lg border border-slate-200 bg-slate-100 p-3">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-900">
                      {titleCase(r.clause_type)}
                    </span>
                    {r.risk_level && <Badge tone={riskTone(r.risk_level)}>{r.risk_level}</Badge>}
                    {r.approval_required && <Badge tone="violet">approval</Badge>}
                  </div>
                  {r.preferred_position && (
                    <p className="text-xs text-slate-700">
                      <span className="font-medium">Preferred:</span> {r.preferred_position}
                    </p>
                  )}
                  {r.fallback_position && (
                    <p className="mt-0.5 text-xs text-slate-500">
                      <span className="font-medium">Fallback:</span> {r.fallback_position}
                    </p>
                  )}
                  {r.negotiation_guidance && (
                    <p className="mt-1 border-t border-slate-100 pt-1 text-xs text-slate-500">
                      <span className="font-medium">Guidance:</span> {r.negotiation_guidance}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
