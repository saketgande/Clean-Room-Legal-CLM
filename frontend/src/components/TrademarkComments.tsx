"use client";

import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Smile } from "lucide-react";
import { trademarksApi } from "@/lib/endpoints";
import { Button, CenterSpinner, ErrorState, Textarea } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { fmtRelative, initials } from "@/lib/utils";
import type { TrademarkComment } from "@/lib/types";

// A small curated set, not the full Unicode emoji range — matches the
// source module's "curated grid" rather than a full picker library.
const EMOJI = [
  "👍", "👎", "🙂", "😀", "😂", "😍", "🎉", "🔥", "✅", "❌",
  "⚠️", "💡", "📌", "👀", "🤔", "👏", "🙏", "💯", "🚀", "⏰",
  "📄", "✍️", "❤️", "😕", "😅", "🤝", "📎", "🧐", "🙌", "⭐",
  "🚩", "🔒", "📅", "✉️", "🏷️",
];

function EmojiButton({ onPick }: { onPick: (emoji: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        className="rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        onClick={() => setOpen((v) => !v)}
        aria-label="Insert emoji"
      >
        <Smile className="h-4 w-4" />
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute bottom-7 right-0 z-50 grid w-[220px] grid-cols-7 gap-1 rounded-lg border border-slate-200 bg-slate-50 p-2 shadow-pop">
            {EMOJI.map((e) => (
              <button
                key={e}
                type="button"
                className="rounded p-1 text-base hover:bg-slate-100"
                onClick={() => {
                  onPick(e);
                  setOpen(false);
                }}
              >
                {e}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function Composer({
  placeholder,
  submitting,
  onSubmit,
  onCancel,
  autoFocus,
}: {
  placeholder: string;
  submitting: boolean;
  onSubmit: (body: string) => void;
  onCancel?: () => void;
  autoFocus?: boolean;
}) {
  const [body, setBody] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function insertEmoji(emoji: string) {
    const el = ref.current;
    if (!el) {
      setBody((b) => b + emoji);
      return;
    }
    const start = el.selectionStart ?? body.length;
    const end = el.selectionEnd ?? body.length;
    const next = body.slice(0, start) + emoji + body.slice(end);
    setBody(next);
    requestAnimationFrame(() => {
      el.focus();
      const pos = start + emoji.length;
      el.setSelectionRange(pos, pos);
    });
  }

  function submit() {
    const trimmed = body.trim();
    if (!trimmed) return;
    onSubmit(trimmed);
    setBody("");
  }

  return (
    <div className="space-y-2">
      <div className="relative">
        <Textarea
          ref={ref}
          rows={2}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={placeholder}
          autoFocus={autoFocus}
          className="pr-9"
        />
        <div className="absolute bottom-1.5 right-1.5">
          <EmojiButton onPick={insertEmoji} />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        {onCancel && (
          <Button variant="outline" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button size="sm" loading={submitting} disabled={!body.trim()} onClick={submit}>
          Post
        </Button>
      </div>
    </div>
  );
}

function CommentRow({ comment, onReply }: { comment: TrademarkComment; onReply?: () => void }) {
  return (
    <div className="flex gap-2.5">
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-100 text-[11px] font-semibold text-brand-700">
        {initials(comment.author_name)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-[12.5px] font-semibold text-slate-900">{comment.author_name}</span>
          <span className="text-[11px] text-slate-400">{fmtRelative(comment.created_at)}</span>
        </div>
        <p className="mt-0.5 whitespace-pre-wrap text-[13px] text-slate-700">{comment.body}</p>
        {onReply && (
          <button type="button" onClick={onReply} className="mt-1 text-[11.5px] font-medium text-brand-700 hover:underline">
            Reply
          </button>
        )}
      </div>
    </div>
  );
}

export function TrademarkComments({ trademarkId }: { trademarkId: string }) {
  const { user } = useAuth();
  const { notify } = useToast();
  const qc = useQueryClient();
  const [replyingTo, setReplyingTo] = useState<string | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ["trademark-comments", trademarkId],
    queryFn: () => trademarksApi.comments(trademarkId),
  });

  const post = useMutation({
    mutationFn: (payload: { body: string; parent_comment_id?: string | null }) =>
      trademarksApi.postComment(trademarkId, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["trademark-comments", trademarkId] });
      qc.invalidateQueries({ queryKey: ["trademark-comment-counts"] });
      setReplyingTo(null);
    },
    onError: (e) => notify(e instanceof Error ? e.message : "Could not post comment", "error"),
  });

  // One top-level comment followed by its flat replies — the server
  // already guarantees no reply points at another reply.
  const threads = useMemo(() => {
    const rows = data ?? [];
    const topLevel = rows.filter((c) => !c.parent_comment_id);
    return topLevel.map((top) => ({
      top,
      replies: rows.filter((c) => c.parent_comment_id === top.id),
    }));
  }, [data]);

  if (isLoading) return <CenterSpinner label="Loading discussion…" />;
  if (error) return <ErrorState error={error} />;

  return (
    <div className="space-y-5">
      <Composer
        placeholder={`Comment as ${user?.full_name ?? user?.email ?? "you"}…`}
        submitting={post.isPending && !replyingTo}
        onSubmit={(body) => post.mutate({ body })}
      />

      {threads.length === 0 ? (
        <p className="text-[13px] text-slate-500">No discussion yet — be the first to comment.</p>
      ) : (
        <div className="space-y-5">
          {threads.map(({ top, replies }) => (
            <div key={top.id} className="space-y-3">
              <CommentRow comment={top} onReply={() => setReplyingTo(replyingTo === top.id ? null : top.id)} />
              {replies.length > 0 && (
                <div className="ml-9 space-y-3 border-l border-slate-200 pl-4">
                  {replies.map((r) => (
                    <CommentRow key={r.id} comment={r} />
                  ))}
                </div>
              )}
              {replyingTo === top.id && (
                <div className="ml-9 border-l border-slate-200 pl-4">
                  <Composer
                    placeholder="Write a reply…"
                    submitting={post.isPending}
                    autoFocus
                    onCancel={() => setReplyingTo(null)}
                    onSubmit={(body) => post.mutate({ body, parent_comment_id: top.id })}
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
