"use client";

import type { ActionLevelCode } from "@/lib/types";

const ABOUT: Record<string, string> = {
  VIEW: "Can see it",
  ADD: "Can see and add",
  EDIT: "Can see, add and change",
  DELETE: "Can do everything, including delete",
};

/** The four action levels as one ladder: pick a step and every step below it lights up. */
export function LevelLadder({ levels, value, onChange, disabled, title }: { levels: ActionLevelCode[]; value: ActionLevelCode; onChange: (l: ActionLevelCode) => void; disabled?: boolean; title?: string }) {
  const at = levels.indexOf(value);
  return (
    <span className={`lad${disabled ? " dis" : ""}`} role="radiogroup" aria-label="Highest action level" title={title}>
      <style dangerouslySetInnerHTML={{ __html: CSS }} />
      {levels.map((l, i) => (
        <button key={l} type="button" role="radio" aria-checked={l === value} disabled={disabled} title={ABOUT[l] ?? l} className={`lv-${l}${i <= at ? " on" : ""}${l === value ? " cur" : ""}`} onClick={() => l !== value && onChange(l)}>
          {l.charAt(0) + l.slice(1).toLowerCase()}
        </button>
      ))}
    </span>
  );
}

const CSS = `
.lad{display:inline-flex;gap:3px;padding:3px;border-radius:10px;background:var(--surface-2)}
.lad button{height:26px;padding:0 11px;border:0;border-radius:7px;background:transparent;color:var(--ink-3);font:600 12px inherit;font-family:inherit;cursor:pointer;transition:background .2s,color .2s,transform .12s,box-shadow .2s}
.lad button:hover:not(:disabled){color:var(--ink);background:var(--surface)} .lad button:active:not(:disabled){transform:scale(.94)}
.lad button.on{color:#fff;box-shadow:none} .lad button.on.cur{box-shadow:0 2px 8px -3px rgba(0,0,0,.45)}
.lad button.on:hover:not(:disabled){filter:brightness(1.08)}
.lad button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.lad.dis{opacity:.6} .lad.dis button{cursor:default}
@media (prefers-reduced-motion:reduce){.lad button{transition:none}}
`;
