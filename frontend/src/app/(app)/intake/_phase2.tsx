"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { intakeApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import type { CopilotTurn } from "@/lib/types";

// ======================= COPILOT CHAT =======================

const ph2svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

// The intake assistant, styled to match the New Request catalog (`.nr` scope):
// a conversation thread with a live request-preview card that fills in as the
// assistant extracts fields, and a rounded composer. Wired to intakeApi.copilot*.
export function CopilotChat({ onFiled }: { onFiled: (id: string) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([
    { role: "assistant", content: "Hi — I can help you file a legal request. What do you need?" },
  ]);
  const [input, setInput] = useState("");
  const [state, setState] = useState<CopilotTurn | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    const msg = input.trim();
    if (!msg) return;
    const nextMsgs = [...messages, { role: "user", content: msg }];
    setMessages(nextMsgs);
    setInput("");
    setBusy(true);
    try {
      const turn = await intakeApi.copilotTurn(nextMsgs, msg);
      setMessages((m) => [...m, { role: "assistant", content: turn.reply }]);
      setState(turn);
    } catch (e) { notify(e instanceof Error ? e.message : "Copilot error", "error"); }
    finally { setBusy(false); }
  }

  async function file() {
    if (!state) return;
    setBusy(true);
    try {
      const desc = messages.filter((m) => m.role === "user").map((m) => m.content).join(" ");
      const fv = state.extracted.counterparty ? { counterparty: state.extracted.counterparty } : null;
      const r = await intakeApi.copilotFile({
        messages, type_label: state.suggested_type_label ?? "General request",
        description: desc, field_values: fv,
      });
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${r.ref}`, "success");
      onFiled(r.id);
    } catch (e) { notify(e instanceof Error ? e.message : "File failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <div className="cochat">
      <div className="coh"><div className="glb">✦</div><div style={{ flex: 1 }}><div className="con">Intake assistant</div><div className="cos">describe what you need — I&apos;ll draft the request</div></div></div>
      <div className="cothread">
        {messages.map((m, i) => (
          <div key={i} className={`comsg ${m.role === "user" ? "you" : "agent"}`}>
            <div className="mav">{m.role === "user" ? "You" : "✦"}</div>
            <div className="cobub">{m.content}</div>
          </div>
        ))}
        {state && (state.suggested_type_label || Object.keys(state.extracted).length > 0) && (
          <div className="reqprev">
            <div className="rph">{ph2svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>')}Request taking shape{state.ready ? <span className="ready">ready</span> : null}</div>
            <div className="rpb">
              {state.suggested_type_label ? <div className="rpr"><span className="rpk">Type</span><span className="rpv">{state.suggested_type_label}</span></div> : null}
              {Object.entries(state.extracted).map(([k, v]) => <div key={k} className="rpr"><span className="rpk">{k.replace(/_/g, " ")}</span><span className="rpv">{String(v)}</span></div>)}
            </div>
          </div>
        )}
        {busy && <div className="comsg agent"><div className="mav">✦</div><div className="cobub dim">Thinking…</div></div>}
      </div>
      {state?.ready && <button className="btn pri filebtn" onClick={file} disabled={busy}>File this request →</button>}
      <div className="cocomposer">
        <input value={input} aria-label="Describe what you need" placeholder="Describe what you need…" onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") send(); }} />
        <button className="cosend" aria-label="Send" onClick={send} disabled={!input.trim() || busy}>{ph2svg('<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>')}</button>
      </div>
    </div>
  );
}

