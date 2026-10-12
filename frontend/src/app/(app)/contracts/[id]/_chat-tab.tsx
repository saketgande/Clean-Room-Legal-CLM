"use client";

// The Assistant tab on the contract page: a chat about THIS contract that can do
// what the rest of the page does. Suggested changes it drafts land in the Review tab
// and are accepted or rejected there like any other; a stage move waits for a yes and
// follows the same rules as the Lifecycle tab.
//
// `ChatPanel` only shows things and reports clicks; `ChatTab` connects it to the server.

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Markdown } from "@/components/markdown";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/utils";
import {
  confirmCopy,
  groupTurns,
  shortcutsFor,
  startersFor,
  toolLabel,
  toolNamesOf,
  type ChatItem,
  type ChatShortcut,
  type ToolStatus,
  type Turn,
} from "@/lib/contract-chat";
import type { ChatView } from "@/lib/contract-chat-controller";
import { useContractChat } from "./_use-contract-chat";

const SHORTCUT_LABEL: Record<ChatShortcut, string> = { review: "Review suggestions", lifecycle: "Open lifecycle" };
const prettyKey = (k: string) => {
  const spaced = k.replace(/_/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
};

function Step({ name, status }: { name: string; status: ToolStatus }) {
  return (
    <div className={`cht-step ${status}`}>
      <span className="cht-ico" aria-hidden="true" />
      <span>{toolLabel(name, status)}</span>
    </div>
  );
}

function AgentBubble({ children }: { children: ReactNode }) {
  return (
    <div className="msg agent">
      <div className="mav">✦</div>
      <div className="bubble md">{children}</div>
    </div>
  );
}

function Reply({ item }: { item: ChatItem }) {
  if (item.tool) return <Step name={item.tool.name} status={item.tool.status} />;
  if (item.blocks)
    return (
      <>
        {item.blocks.map((b, i) =>
          b.type === "tool" ? (
            <Step key={i} name={b.name} status={b.status} />
          ) : b.text ? (
            <AgentBubble key={i}><Markdown>{b.text}</Markdown></AgentBubble>
          ) : null,
        )}
      </>
    );
  if (item.role === "system") return <div className="cht-note">{item.text}</div>;
  if (!item.text) return null;
  return <AgentBubble><Markdown>{item.text}</Markdown></AgentBubble>;
}

function TurnView({ turn, working, userInitials, onShortcut }: { turn: Turn; working: boolean; userInitials: string; onShortcut: (s: ChatShortcut) => void }) {
  const shortcuts = shortcutsFor(turn.replies.flatMap(toolNamesOf));
  return (
    <>
      {turn.user ? (
        <div className="msg you">
          <div className="mav">{userInitials}</div>
          <div className="bubble">{turn.user.text}</div>
        </div>
      ) : null}
      {turn.replies.map((r) => <Reply key={r.id} item={r} />)}
      {shortcuts.length > 0 && !working ? (
        <div className="cht-short">
          {shortcuts.map((s) => <button key={s} className="mbtn" onClick={() => onShortcut(s)}>{SHORTCUT_LABEL[s]}</button>)}
        </div>
      ) : null}
    </>
  );
}

export interface ChatPanelProps {
  view: ChatView;
  userInitials: string;
  /** Shown first in an empty chat: what Aegis already knows about this contract. */
  intro?: ReactNode;
  /** Shown above the conversation, whether or not there is one: a state of the contract worth knowing before asking. */
  banner?: ReactNode;
  /** The one-click questions shown in an empty chat. */
  starters?: string[];
  /** `retryRunId` is set by "Try again" on an answer that wrote nothing. */
  onSend: (text: string, retryRunId?: string | null) => void;
  onStop: () => void;
  onResolve: (approve: boolean) => void;
  onDismissError: () => void;
  onConsumeDraft: () => void;
  onShortcut: (s: ChatShortcut) => void;
}

export function ChatPanel(p: ChatPanelProps) {
  const { view } = p;
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);

  // A message that could not be sent goes back in the box.
  const draft = view.draft;
  const consumeDraft = p.onConsumeDraft;
  useEffect(() => {
    if (draft) {
      setText((cur) => cur || draft);
      consumeDraft();
    }
  }, [draft, consumeDraft]);

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [view.items, view.pending, view.streaming]);

  const turns = groupTurns(view.items);
  const last = view.items[view.items.length - 1];
  const writing = last?.role === "assistant" && !last.blocks && last.text.length > 0;
  const waiting = !!view.pending;
  const busy = view.streaming || view.resolving;

  function submit(value = text, retryRunId?: string | null) {
    const t = value.trim();
    if (!t || busy || waiting) return;
    setText("");
    if (retryRunId) p.onSend(t, retryRunId);
    else p.onSend(t);
  }

  const copy = view.pending ? confirmCopy(view.pending.toolName) : null;

  return (
    <>
      {p.banner ? <div className="cht-banner">{p.banner}</div> : null}
      <div className="chat" role="log" aria-label="Chat with Aegis about this contract">
        {view.loading ? <div className="cht-note">Opening the chat…</div> : null}

        {!view.loading && view.items.length === 0 ? (
          <AgentBubble>
            {p.intro ?? "Ask about this contract, or tell me what to change."}
            <div className="cht-hint">
              I can draft suggested changes (you accept or reject them in the Review tab) and move the contract between stages once you confirm.
            </div>
          </AgentBubble>
        ) : null}

        {turns.map((turn, i) => (
          <TurnView key={turn.key} turn={turn} working={view.streaming && i === turns.length - 1} userInitials={p.userInitials} onShortcut={p.onShortcut} />
        ))}

        {view.pending && copy ? (
          <div className="cht-confirm" role="group" aria-label="Confirm action">
            <div className="cht-badge">Needs your confirmation</div>
            <div className="cht-title">{copy.title}</div>
            {Object.keys(view.pending.details).length > 0 ? (
              <dl className="cht-rows">
                {Object.entries(view.pending.details).map(([k, v]) => (
                  <div key={k}><dt>{prettyKey(k)}</dt><dd>{v}</dd></div>
                ))}
              </dl>
            ) : null}
            <div className="cht-row">
              <button className="btn sm" disabled={view.resolving} onClick={() => p.onResolve(false)}>Cancel</button>
              <button className="btn sm pri" disabled={view.resolving} onClick={() => p.onResolve(true)}>{copy.ok}</button>
            </div>
          </div>
        ) : null}

        {view.streaming && !writing ? (
          <div className="msg agent"><div className="mav">✦</div><div className="bubble dim">Working…</div></div>
        ) : null}

        {view.notice && !view.streaming ? (
          <div className="cht-notice" role="status">
            {view.notice.message}
            {view.notice.retryText ? <button className="btn sm" disabled={busy} onClick={() => submit(view.notice?.retryText ?? "", view.notice?.retryRunId)}>Try again</button> : null}
          </div>
        ) : null}

        {view.error ? (
          <div className="cht-error" role="alert">
            <span>{view.error}</span>
            <button className="btn sm" onClick={p.onDismissError}>Dismiss</button>
          </div>
        ) : null}
        <div ref={end} />
      </div>

      {!view.loading && view.items.length === 0 && !view.streaming ? (
        <div className="chips">
          {(p.starters ?? startersFor(undefined)).map((s) => <button key={s} className="qchip" disabled={busy} onClick={() => submit(s)}>{s}</button>)}
        </div>
      ) : null}

      <div className="inputbar">
        <input
          value={text}
          aria-label="Message Aegis about this contract"
          placeholder={waiting ? "Confirm or cancel the action above first" : "Ask a question or request a change…"}
          disabled={waiting}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {view.streaming ? (
          <button className="send stop" aria-label="Stop" disabled={view.stopping} onClick={p.onStop}>■</button>
        ) : (
          <button className="send" aria-label="Send" disabled={!text.trim() || busy || waiting} onClick={() => submit()}>
            <svg className="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="M22 2 11 13M22 2l-7 20-4-9-9-4z" /></svg>
          </button>
        )}
      </div>
    </>
  );
}

export function ChatTab({ contractId, stage, intro, banner, onOpenReview, onOpenLifecycle, onSettled }: {
  contractId: string;
  /** The contract's lifecycle stage: the starter questions depend on it. */
  stage?: string | null;
  intro?: ReactNode;
  banner?: ReactNode;
  onOpenReview: () => void;
  onOpenLifecycle: () => void;
  /** Something on the contract changed because of an answer: refetch what the page shows. */
  onSettled: () => void;
}) {
  const { user } = useAuth();
  const { controller, view } = useContractChat(contractId, onSettled);
  return (
    <ChatPanel
      view={view}
      userInitials={initials(user?.full_name ?? "You")}
      intro={intro}
      banner={banner}
      starters={startersFor(stage)}
      onSend={(t, retryRunId) => void controller?.send(t, retryRunId)}
      onStop={() => void controller?.stop()}
      onResolve={(a) => void controller?.resolve(a)}
      onDismissError={() => controller?.dismissError()}
      onConsumeDraft={() => controller?.consumeDraft()}
      onShortcut={(s) => (s === "review" ? onOpenReview() : onOpenLifecycle())}
    />
  );
}

export const CHAT_TAB_CSS = `
.clm .chat{flex:1;min-height:0;overflow:auto;padding:16px;display:flex;flex-direction:column;gap:12px}
.clm .msg{display:flex;gap:9px;align-items:flex-start;min-width:0}
.clm .msg.you{flex-direction:row-reverse}
.clm .mav{flex:none;width:24px;height:24px;border-radius:8px;display:grid;place-items:center;font-size:12px;font-weight:600;line-height:1}
.clm .msg.agent .mav{background:var(--btn);color:var(--btn-ink)}
.clm .msg.you .mav{background:var(--surface-2);color:var(--ink-2);border:1px solid var(--border);font-size:9.5px}
.clm .bubble{min-width:0;max-width:calc(100% - 33px);font-size:13px;line-height:1.55;color:var(--ink);padding:9px 12px;border-radius:12px;overflow-wrap:anywhere}
.clm .msg.agent .bubble{background:var(--surface-2);border-top-left-radius:4px}
.clm .msg.you .bubble{background:var(--btn);color:var(--btn-ink);border-top-right-radius:4px;white-space:pre-wrap}
.clm .bubble.dim{color:var(--ink-3)}
.clm .bubble.md>div{font-size:13px;line-height:1.55;color:inherit}
.clm .bubble.md h1,.clm .bubble.md h2,.clm .bubble.md h3{font-size:13.5px;font-weight:650;margin:10px 0 4px}
.clm .bubble.md p{margin:0 0 8px} .clm .bubble.md p:last-child{margin-bottom:0}
.clm .bubble.md ul,.clm .bubble.md ol{margin:0 0 8px;padding-left:18px} .clm .bubble.md li{margin:2px 0}
.clm .bubble.md ul:last-child,.clm .bubble.md ol:last-child{margin-bottom:0}
.clm .bubble.md code{font:12px var(--mono);background:var(--surface);border:1px solid var(--border);border-radius:5px;padding:0 4px}
.clm .mact{margin-top:10px;display:flex;gap:7px;flex-wrap:wrap}
.clm .mbtn{padding:6px 12px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:600 12px var(--sans)}
.clm .mbtn:hover{background:var(--surface-2)}
.clm .mbtn.go{background:var(--btn);color:var(--btn-ink);border-color:var(--btn)} .clm .mbtn.go:hover{opacity:.88}
.clm .chips{flex:none;display:flex;flex-direction:column;gap:7px;padding:0 16px 12px}
.clm .qchip{display:flex;align-items:center;justify-content:space-between;gap:10px;text-align:left;padding:10px 13px;border:1px solid var(--border);border-radius:10px;background:var(--surface);color:var(--ink);font-size:13px}
.clm .qchip::after{content:"\\2192";color:var(--ink-3)}
.clm .qchip:hover:not(:disabled){background:var(--surface-2);border-color:var(--border-strong)}
.clm .qchip:disabled{opacity:.5}
.clm .inputbar{flex:none;display:flex;align-items:center;gap:8px;margin:0 16px 14px;padding:5px 5px 5px 14px;border:1px solid var(--border-strong);border-radius:12px;background:var(--surface);transition:border-color .15s,box-shadow .15s}
.clm .inputbar:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.clm .inputbar input{flex:1;min-width:0;height:34px;border:0;outline:0;background:none;color:var(--ink);font:13px var(--sans)}
.clm .inputbar input:focus-visible{outline:none}
.clm .inputbar input::placeholder{color:var(--ink-3)}
.clm .inputbar input:disabled{opacity:.6}
.clm .send{flex:none;width:32px;height:32px;border-radius:50%;background:var(--btn);color:var(--btn-ink);display:grid;place-items:center}
.clm .send:disabled{opacity:.35}
.clm .send.stop{background:var(--crit);color:#fff;font-size:11px}
.clm .cht-note{font-size:12px;color:var(--ink-3);text-align:center;padding:4px 0}
.clm .cht-hint{margin-top:8px;font-size:11.5px;color:var(--ink-3);line-height:1.5}
.clm .cht-step{display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--ink-3);padding:0 2px 0 33px}
.clm .cht-step .cht-ico{width:9px;height:9px;border-radius:50%;border:1.5px solid var(--ink-3);flex:none}
.clm .cht-step.running .cht-ico{border-top-color:transparent;animation:chtspin .8s linear infinite}
.clm .cht-step.done .cht-ico{background:var(--good);border-color:var(--good)}
.clm .cht-step.error{color:var(--crit)} .clm .cht-step.error .cht-ico{background:var(--crit);border-color:var(--crit)}
.clm .cht-step.refused{color:var(--ink-2)} .clm .cht-step.refused .cht-ico{background:var(--warn);border-color:var(--warn)}
@keyframes chtspin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.clm .cht-step.running .cht-ico{animation:none}}
.clm .cht-short{display:flex;gap:7px;flex-wrap:wrap;padding-left:33px}
.clm .cht-confirm{border:1px solid color-mix(in srgb,var(--ai) 40%,var(--border));background:var(--ai-soft);border-radius:12px;padding:12px 14px;margin-left:33px}
.clm .cht-badge{font:600 9.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ai)}
.clm .cht-title{font-weight:650;font-size:13px;margin-top:3px}
.clm .cht-rows{margin:8px 0 0;display:flex;flex-direction:column;gap:5px;font-size:12px}
.clm .cht-rows div{display:flex;gap:8px} .clm .cht-rows dt{color:var(--ink-3);min-width:62px;flex:none} .clm .cht-rows dd{margin:0;color:var(--ink)}
.clm .cht-row{display:flex;gap:7px;justify-content:flex-end;margin-top:10px}
.clm .cht-notice,.clm .cht-error{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12px;border-radius:10px;padding:8px 11px}
.clm .cht-notice{border:1px solid var(--border);background:var(--surface-2);color:var(--ink-2)}
.clm .cht-error{border:1px solid color-mix(in srgb,var(--crit) 40%,var(--border));background:var(--crit-soft);color:var(--crit)}
.clm .cht-banner{flex:none;padding:12px 16px 0}
`;
