"use client";

import { Suspense, use, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Scale, Loader2, MessageSquare, Lock } from "lucide-react";
import { externalShareApi } from "@/lib/endpoints";
import type { ExternalComment, ExternalShareView } from "@/lib/types";
import { Button, Input, Field } from "@/components/ui";

export default function ExternalSharePage({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-slate-400">
          <Loader2 className="h-5 w-5 animate-spin" />
        </div>
      }
    >
      <ShareView token={token} />
    </Suspense>
  );
}

function ShareView({ token }: { token: string }) {
  const search = useSearchParams();
  const initialPass = search.get("p") || search.get("passcode") || "";

  const [passcode, setPasscode] = useState(initialPass);
  const [entered, setEntered] = useState(initialPass);
  const [view, setView] = useState<ExternalShareView | null>(null);
  const [comments, setComments] = useState<ExternalComment[]>([]);
  const [needPass, setNeedPass] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);

  const load = useCallback(
    async (pc: string) => {
      setLoading(true);
      setError(null);
      try {
        const v = await externalShareApi.view(token, pc || undefined);
        setView(v);
        setComments(await externalShareApi.comments(token, pc || undefined));
        setNeedPass(false);
        setPasscode(pc);
      } catch (e) {
        setView(null);
        setNeedPass(true);
        setError(e instanceof Error ? e.message : "This link is invalid or has expired.");
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    load(initialPass);
  }, [load, initialPass]);

  async function post() {
    if (!body.trim()) return;
    setPosting(true);
    try {
      await externalShareApi.addComment(
        token,
        { author_name: name.trim() || undefined, body: body.trim() },
        passcode || undefined,
      );
      setBody("");
      setComments(await externalShareApi.comments(token, passcode || undefined));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't post your comment.");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-3">
          <Scale className="h-5 w-5 text-brand-600" />
          <span className="text-sm font-semibold text-slate-800">AEGIS — Shared contract</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-6">
        {loading ? (
          <div className="flex justify-center py-20 text-slate-400">
            <Loader2 className="h-6 w-6 animate-spin" />
          </div>
        ) : needPass && !view ? (
          <div className="mx-auto max-w-sm rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <div className="mb-3 flex items-center gap-2 text-slate-700">
              <Lock className="h-4 w-4" />
              <span className="font-medium">This contract is passcode-protected</span>
            </div>
            <Field label="Passcode">
              <Input
                type="password"
                value={entered}
                onChange={(e) => setEntered(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load(entered)}
                placeholder="Enter the passcode"
              />
            </Field>
            {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
            <Button className="mt-3 w-full" onClick={() => load(entered)} disabled={!entered}>
              Unlock
            </Button>
          </div>
        ) : view ? (
          <div className="space-y-6">
            <div>
              <h1 className="text-xl font-semibold text-slate-900">{view.title}</h1>
              {view.filename && (
                <p className="mt-0.5 text-sm text-slate-500">{view.filename}</p>
              )}
            </div>

            {view.text_excerpt && (
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Document
                </h2>
                <pre className="max-h-72 overflow-y-auto whitespace-pre-wrap font-sans text-sm text-slate-700">
                  {view.text_excerpt}
                  {view.text_truncated ? "\n\n… (excerpt truncated)" : ""}
                </pre>
              </div>
            )}

            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-900">
                <MessageSquare className="h-4 w-4" />
                Comments
              </h2>

              {comments.length === 0 ? (
                <p className="text-sm text-slate-500">No comments yet. Start the discussion below.</p>
              ) : (
                <div className="space-y-2">
                  {comments.map((c) => (
                    <div
                      key={c.id}
                      className="rounded-lg border border-slate-200 bg-slate-50 p-3"
                    >
                      <div className="mb-1 flex items-center gap-2">
                        <span className="text-sm font-medium text-slate-800">
                          {c.author_name}
                        </span>
                        <span className="rounded-full bg-slate-200 px-1.5 text-[10px] font-semibold uppercase text-slate-600">
                          {c.author_kind === "counterparty" ? "You / counterparty" : "Owner"}
                        </span>
                        <span className="ml-auto text-[11px] text-slate-400">
                          {new Date(c.created_at).toLocaleString()}
                        </span>
                      </div>
                      <p className="whitespace-pre-wrap text-sm text-slate-700">{c.body}</p>
                    </div>
                  ))}
                </div>
              )}

              <div className="mt-4 border-t border-slate-100 pt-4">
                <div className="grid gap-2 sm:grid-cols-[200px_1fr]">
                  <Input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your name (optional)"
                  />
                  <textarea
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    placeholder="Add a comment…"
                    rows={2}
                    className="w-full resize-none rounded-md border border-slate-200 p-2 text-sm focus:border-brand-400 focus:outline-none focus:ring-1 focus:ring-brand-400"
                  />
                </div>
                {error && <p className="mt-2 text-sm text-rose-600">{error}</p>}
                <div className="mt-2 flex justify-end">
                  <Button loading={posting} disabled={!body.trim()} onClick={post}>
                    Post comment
                  </Button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-sm rounded-xl border border-slate-200 bg-white p-6 text-center shadow-sm">
            <p className="text-sm text-rose-600">{error ?? "This link is invalid or has expired."}</p>
          </div>
        )}
      </main>
    </div>
  );
}
