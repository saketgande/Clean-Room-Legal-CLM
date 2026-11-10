"use client";

// The Admin workspace: a grouped side navigation instead of nine tabs in a row, and a small
// kit (section header, stats, pills, rows, empty state) so every section reads the same way.
// Presentation only; each section keeps its own data and saving.

import type { ComponentType, ReactNode } from "react";
import { useEffect, useRef } from "react";

export type NavItem = { id: string; label: string; hint: string; icon: ComponentType<{ size?: number }> };
export type NavGroup = { title: string; items: NavItem[] };

export function AdminShell({ groups, active, onChange, children }: { groups: NavGroup[]; active: string; onChange: (id: string) => void; children: ReactNode }) {
  const main = useRef<HTMLDivElement>(null);
  useEffect(() => { main.current?.scrollIntoView?.({ block: "nearest" }); }, [active]);
  return (
    <div className="adm">
      <style dangerouslySetInnerHTML={{ __html: KIT_CSS }} />
      <header className="adm-top"><h1>Admin</h1></header>
      <nav className="adm-nav" aria-label="Admin sections">
        {groups.map((g, gi) => (
          <div key={g.title} className="adm-grp" role="group" aria-label={g.title}>
            {gi > 0 && <i className="adm-sep" aria-hidden />}
            {g.items.map((it) => {
              const I = it.icon;
              return (
                <button key={it.id} type="button" className={active === it.id ? "on" : ""} aria-current={active === it.id ? "page" : undefined} title={it.hint} onClick={() => onChange(it.id)}>
                  <I size={15} /><span>{it.label}</span>
                </button>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="adm-main" ref={main} key={active}>{children}</div>
    </div>
  );
}

export function Seg<T extends string>({ value, onChange, items, label }: { value: T; onChange: (v: T) => void; items: { id: T; label: string; n?: number }[]; label: string }) {
  return (
    <div className="ad-seg" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" aria-selected={value === it.id} className={value === it.id ? "on" : ""} onClick={() => onChange(it.id)}>
          {it.label}{it.n !== undefined && <span className="n">{it.n}</span>}
        </button>
      ))}
    </div>
  );
}

export function SecHead({ title, desc, action }: { title: string; desc?: string; action?: ReactNode }) {
  return (
    <div className="ad-head">
      <div><h2>{title}</h2>{desc && <p>{desc}</p>}</div>
      {action}
    </div>
  );
}

export const AdBtn = ({ kind = "ghost", children, ...p }: { kind?: "primary" | "ghost" | "danger" } & React.ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button type="button" {...p} className={`ad-btn ${kind}${p.className ? " " + p.className : ""}`}>{children}</button>
);

export type Tone = "green" | "red" | "amber" | "slate" | "blue" | "violet";
export const Pill = ({ tone = "slate", children }: { tone?: Tone; children: ReactNode }) => <span className={`ad-pill ${tone}`}>{children}</span>;

export function Stats({ items }: { items: { n: ReactNode; label: string; tone?: "bad" | "warn" }[] }) {
  return (
    <div className="ad-stats">
      {items.map((s, i) => <div key={s.label} className={s.tone ?? ""} style={{ ["--i" as string]: i }}><b>{s.n}</b><span>{s.label}</span></div>)}
    </div>
  );
}

export function Empty({ icon, title, desc, action }: { icon: ReactNode; title: string; desc: string; action?: ReactNode }) {
  return <div className="ad-empty"><span className="ic">{icon}</span><b>{title}</b><p>{desc}</p>{action}</div>;
}

export function Search({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  return <input className="ad-search" type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} />;
}

export const KIT_CSS = `
@keyframes adrise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes adpop{from{opacity:0;transform:scale(.92)}to{opacity:1;transform:none}}
@keyframes adspin{to{transform:rotate(360deg)}}
.adm{display:flex;flex-direction:column;gap:20px;max-width:1360px;margin:0 auto;color:var(--ink)}
.adm-top h1{margin:0;font-size:26px;font-weight:750;letter-spacing:-.01em} .adm-top p{margin:4px 0 0;font-size:14px;color:var(--ink-3)}
.adm-layout{display:grid;grid-template-columns:232px minmax(0,1fr);gap:28px;align-items:start}
.adm-nav{position:sticky;top:12px;display:flex;flex-direction:column;gap:18px}
.adm-grp{display:flex;flex-direction:column;gap:2px}
.adm-gt{padding:0 12px 6px;font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.adm-nav button{position:relative;display:flex;align-items:center;gap:11px;height:40px;padding:0 12px;border-radius:11px;font:600 13.5px inherit;font-family:inherit;color:var(--ink-2);background:transparent;text-align:left;cursor:pointer;transition:background .18s,color .18s,transform .18s}
.adm-nav button:hover{background:var(--surface-2);color:var(--ink);transform:translateX(2px)}
.adm-nav button.on{background:var(--accent-soft,var(--surface-2));color:var(--accent)}
.adm-nav button.on::before{content:"";position:absolute;left:-14px;top:9px;bottom:9px;width:3px;border-radius:3px;background:var(--accent);animation:adpop .25s both}
.adm-nav button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.adm-main{min-width:0;display:flex;flex-direction:column;gap:18px;animation:adrise .32s both}
.ad-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}
.ad-head h2{margin:0;font-size:20px;font-weight:700} .ad-head p{margin:4px 0 0;max-width:680px;font-size:13.5px;line-height:1.55;color:var(--ink-3)}
.ad-btn{display:inline-flex;align-items:center;gap:7px;height:38px;padding:0 15px;border-radius:11px;font:600 13px inherit;font-family:inherit;cursor:pointer;border:0;transition:transform .15s,background .15s,box-shadow .2s,opacity .2s,color .15s;white-space:nowrap}
.ad-btn.primary{background:var(--accent);color:var(--accent-ink,#fff);box-shadow:0 8px 18px -10px var(--accent)} .ad-btn.primary:hover:not(:disabled){transform:translateY(-1px)}
.ad-btn.ghost{background:transparent;color:var(--ink-2);box-shadow:inset 0 0 0 1px var(--border-strong)} .ad-btn.ghost:hover:not(:disabled){background:var(--surface-2);color:var(--ink)}
.ad-btn.danger{background:transparent;color:var(--ink-2);box-shadow:inset 0 0 0 1px var(--border-strong)} .ad-btn.danger:hover:not(:disabled){color:#a3271a;box-shadow:inset 0 0 0 1px #a3271a;background:#fdecea}
.ad-btn:active:not(:disabled){transform:scale(.97)} .ad-btn:disabled{opacity:.5;cursor:default} .ad-btn.sm{height:32px;padding:0 12px;font-size:12.5px}
.ad-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ad-pill{display:inline-flex;align-items:center;gap:5px;padding:2px 10px;border-radius:99px;font-size:11.5px;font-weight:650;white-space:nowrap}
.ad-pill.slate{background:var(--surface-2);color:var(--ink-3)} .ad-pill.green{background:#e2f4ea;color:#17673a} .ad-pill.red{background:#fdecea;color:#a3271a} .ad-pill.amber{background:#fdf0d5;color:#8a5a00} .ad-pill.blue{background:var(--accent-soft,var(--surface-2));color:var(--accent)} .ad-pill.violet{background:#ede7fb;color:#5b3fb0}
.ad-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.ad-stats>div{display:flex;flex-direction:column;gap:2px;padding:13px 16px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:adrise .35s both;animation-delay:calc(var(--i)*50ms)}
.ad-stats b{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums} .ad-stats span{font-size:12px;color:var(--ink-3)} .ad-stats .bad b{color:#a3271a} .ad-stats .warn b{color:#8a5a00}
.ad-card{padding:20px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:adrise .35s both;display:flex;flex-direction:column;gap:14px}
.ad-card h3{margin:0;font-size:15px;font-weight:700} .ad-card .sub{margin:2px 0 0;font-size:13px;color:var(--ink-3)}
.ad-rows{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.ad-row{display:flex;align-items:center;gap:14px;padding:12px 14px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:adrise .32s both;animation-delay:calc(var(--i)*35ms);transition:box-shadow .18s,transform .18s,background .18s}
.ad-row:hover{box-shadow:inset 0 0 0 1px var(--border-strong);transform:translateY(-1px)}
.ad-row .main{flex:1;min-width:0;display:flex;flex-direction:column;gap:3px} .ad-row .main b{font-size:14px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .ad-row .main small{font-size:12.5px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-row .acts{display:flex;gap:6px;flex:none;align-items:center}
.ad-search{height:40px;min-width:220px;padding:0 14px;border-radius:11px;border:0;background:var(--surface);color:var(--ink);font:500 14px inherit;font-family:inherit;box-shadow:inset 0 0 0 1px var(--border-strong);transition:box-shadow .15s} .ad-search:focus{outline:none;box-shadow:inset 0 0 0 2px var(--accent)}
.ad-empty{display:flex;flex-direction:column;align-items:center;gap:6px;padding:44px 16px;border-radius:18px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);text-align:center;animation:adrise .35s both}
.ad-empty .ic{display:grid;place-items:center;width:56px;height:56px;border-radius:18px;background:var(--accent-soft,var(--surface-2));color:var(--accent);margin-bottom:6px;animation:adpop .4s .1s both} .ad-empty b{font-size:15px} .ad-empty p{margin:0 0 8px;font-size:13px;color:var(--ink-3);max-width:380px;line-height:1.5}
.ad-field{display:flex;flex-direction:column;gap:6px;font-size:12.5px;font-weight:650;color:var(--ink-2)} .ad-field small{font-weight:500;color:var(--ink-3);font-size:12px}
.ad-field input,.ad-field textarea{width:100%;height:42px;padding:0 13px;border-radius:11px;border:0;background:var(--surface);color:var(--ink);font:500 14px inherit;font-family:inherit;box-shadow:inset 0 0 0 1px var(--border-strong);transition:box-shadow .15s} .ad-field textarea{height:auto;padding:10px 13px}
.ad-field input:focus,.ad-field textarea:focus{outline:none;box-shadow:inset 0 0 0 2px var(--accent)}
.ad-spin{width:18px;height:18px;border-radius:50%;border:2px solid var(--border-strong);border-top-color:var(--accent);animation:adspin .7s linear infinite}
.ad-load{display:flex;justify-content:center;padding:48px}
/* legacy tables inside Admin (entities register) get the same soft look */
.adm table{border-collapse:separate;border-spacing:0 6px;width:100%}
.adm thead th{padding:8px 14px;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);text-align:left;background:transparent;border:0}
.adm tbody td{padding:12px 14px;background:var(--surface);border:0;font-size:13.5px;box-shadow:inset 0 1px 0 var(--border),inset 0 -1px 0 var(--border);transition:background .15s}
.adm tbody td:first-child{border-radius:12px 0 0 12px;box-shadow:inset 1px 1px 0 var(--border),inset 0 -1px 0 var(--border)} .adm tbody td:last-child{border-radius:0 12px 12px 0;box-shadow:inset -1px 1px 0 var(--border),inset 0 -1px 0 var(--border)}
.adm thead,.adm thead tr,.adm thead th{background:transparent!important;box-shadow:none!important} .adm tbody td:last-child{white-space:nowrap}
.adm tbody tr{animation:adrise .3s both} .adm tbody tr:hover td{background:var(--surface-2)}
.ad-sh{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}
.ad-org{display:flex;align-items:center;gap:14px;padding-bottom:6px} .ad-org b{display:block;font-size:17px} .ad-org small{color:var(--ink-3);font-size:13px} .ad-org em{font-style:normal;font-weight:650;color:var(--ink-2)}
.ad-chips{display:flex;flex-wrap:wrap;gap:8px;margin-top:2px} .ad-chips button{min-height:36px;padding:0 15px;border-radius:99px;font:600 13px inherit;font-family:inherit;cursor:pointer;color:var(--ink-2);background:var(--surface);border:0;box-shadow:inset 0 0 0 1px var(--border-strong);transition:background .18s,color .18s,box-shadow .18s,transform .12s} .ad-chips button:hover{box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)} .ad-chips button:active{transform:scale(.95)} .ad-chips button.on{background:var(--accent);color:var(--accent-ink,#fff);box-shadow:none}
.ad-actions{display:flex;justify-content:flex-end;align-items:center;gap:12px} .ad-dirty{font-size:12.5px;color:#8a5a00;animation:adpop .25s both}
.ad-calm{display:flex;align-items:center;gap:8px;padding:11px 14px;border-radius:12px;background:var(--surface-2);color:var(--ink-3);font-size:13px;animation:adrise .3s both}
.ad-attn{box-shadow:inset 0 0 0 1.5px #e9c36a}
.ad-rl{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end;max-width:240px}
.ad-ico{display:grid;place-items:center;flex:none;width:36px;height:36px;border-radius:11px;background:var(--surface-2);color:var(--ink-2)} .ad-ico.lg{width:46px;height:46px;border-radius:14px} .ad-ico.red{background:#fdecea;color:#a3271a}
.ad-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace!important;font-size:13px!important}
.ad-val{max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:4px 10px;border-radius:8px;background:var(--surface-2);font-size:12px;color:var(--ink-2)}
.ad-link{display:flex;flex-direction:column;gap:10px;padding:14px;border-radius:14px;background:var(--accent-soft,var(--surface-2));animation:adpop .3s both}
.ad-link .top{display:flex;justify-content:space-between;gap:10px} .ad-link b{font-size:14px} .ad-link p{margin:3px 0 0;font-size:12.5px;color:var(--ink-3)} .ad-link small{font-size:11.5px;color:var(--ink-3)}
.ad-link .x{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:transparent;border:0;color:var(--ink-3);cursor:pointer} .ad-link .x:hover{background:var(--surface)}
.ad-link .copy{display:flex;gap:8px} .ad-link input{flex:1;min-width:0;height:36px;padding:0 12px;border-radius:10px;border:0;background:var(--surface);color:var(--ink);font:12px ui-monospace,monospace;box-shadow:inset 0 0 0 1px var(--border-strong)}
.ad-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.ad-int{display:flex;align-items:center;gap:14px;padding:16px;border-radius:16px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);animation:adrise .35s both;animation-delay:calc(var(--i)*60ms);transition:transform .18s,box-shadow .18s}
.ad-int:hover{transform:translateY(-2px);box-shadow:inset 0 0 0 1px var(--border-strong),0 14px 26px -20px rgba(20,26,40,.4)}
.ad-int .main{display:flex;flex-direction:column;gap:5px} .ad-int b{font-size:14.5px} .ad-int .st{display:flex;align-items:center;gap:8px;font-size:12.5px;font-weight:600}
.ad-int.ok .st{color:#17673a} .ad-int.bad .st{color:#a3271a} .ad-int.ok .ad-ico{background:#e2f4ea;color:#17673a} .ad-int.bad .ad-ico{background:#fdecea;color:#a3271a}
.ad-int .dot{width:8px;height:8px;border-radius:50%;background:currentColor;position:relative} .ad-int.ok .dot::after{content:"";position:absolute;inset:-4px;border-radius:50%;border:2px solid currentColor;opacity:.4;animation:adping 1.8s ease-out infinite}
@keyframes adping{from{transform:scale(.5);opacity:.5}to{transform:scale(1.5);opacity:0}}
.ad-env{margin:0;display:flex;flex-direction:column;gap:10px} .ad-env>div{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 12px;border-radius:12px;background:var(--surface-2)} .ad-env dt{font-size:13px;color:var(--ink-3)} .ad-env dd{margin:0}
.ad-warn{margin:0;padding:10px 12px;border-radius:12px;background:#fdf0d5;color:#8a5a00;font-size:13px}
.ad-tags{display:flex;flex-wrap:wrap;gap:5px;margin-top:3px}
.ad-wall.off{opacity:.6} .ad-sw{display:flex;flex-direction:column;align-items:center;gap:3px;flex:none} .ad-sw small{font-size:11px;color:var(--ink-3)}
.ad-act{display:flex;align-items:center;gap:12px}
.ad-limit{display:flex;flex-direction:column;align-items:flex-end;flex:none} .ad-limit small{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)} .ad-limit b{font-size:15px;font-variant-numeric:tabular-nums}
.ad-row .ad-btn{flex:none}
.rtd-sw{position:relative;flex:none;width:40px;height:24px;border-radius:99px;background:var(--border-strong);transition:background .2s;cursor:pointer;border:0;padding:0} .rtd-sw i{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .22s cubic-bezier(.3,1.4,.5,1)} .rtd-sw.on{background:var(--accent)} .rtd-sw.on i{transform:translateX(16px)} .rtd-sw:disabled{opacity:.5;cursor:default}
.ad-subh{margin:0;font-size:15px;font-weight:700} .ad-subp{margin:2px 0 0;font-size:13px;color:var(--ink-3)} .ad-who{display:inline-flex;align-items:center;gap:10px}
.rt-tile{display:grid;place-items:center;flex:none;border-radius:30%;font-weight:700;letter-spacing:.02em;user-select:none}

/* ---- dense mode: one calm, compact system ---- */
.adm{gap:12px}
.adm-top h1{font-size:22px}
.adm-nav{position:static;flex-direction:row;align-items:center;gap:2px;padding:4px;border-radius:14px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);flex-wrap:wrap;width:fit-content;max-width:100%}
.adm-nav::-webkit-scrollbar{display:none}
.adm-grp{flex-direction:row;align-items:center;gap:2px;flex:none}
.adm-sep{display:block;width:1px;height:18px;background:var(--border);margin:0 6px;flex:none}
.adm-nav button{flex:none;white-space:nowrap;height:34px;padding:0 11px;gap:6px;border-radius:10px;font-size:13px;box-shadow:none;transform:none!important}
.adm-nav button.on{background:var(--accent-soft,var(--surface-2));color:var(--accent)} .adm-nav button.on::before{display:none}
.adm-layout{display:block}
.adm-main{gap:12px}
.ad-head h2{font-size:17px} .ad-head p{margin:2px 0 0;font-size:13px;max-width:760px;line-height:1.45}
.ad-btn{height:34px;padding:0 13px;font-size:12.5px;border-radius:10px} .ad-btn.sm{height:28px;padding:0 10px;font-size:12px;border-radius:8px}
.ad-stats{display:flex;flex-wrap:wrap;gap:6px}
.ad-stats>div{flex-direction:row;align-items:baseline;gap:7px;padding:6px 12px;border-radius:99px;box-shadow:inset 0 0 0 1px var(--border)}
.ad-stats b{font-size:14px} .ad-stats span{font-size:12.5px}
.ad-card{padding:14px;border-radius:14px;gap:10px} .ad-card h3{font-size:14px}
.ad-card.flush{padding:0;overflow:hidden}
.ad-rows{gap:0}
.ad-row{padding:8px 12px;border-radius:0;box-shadow:none;border-bottom:1px solid var(--border);gap:12px;min-height:46px;transform:none!important}
.ad-row:last-child{border-bottom:0} .ad-row:hover{background:var(--surface-2);box-shadow:none}
.ad-row .main b{font-size:13.5px} .ad-row .main small{font-size:12px}
.ad-rows.boxed{border-radius:12px;box-shadow:inset 0 0 0 1px var(--border);overflow:hidden}
.ad-t{border-radius:12px;box-shadow:inset 0 0 0 1px var(--border);overflow:hidden;background:var(--surface)}
.ad-t .ad-rows{border:0}
.ad-th{display:grid;grid-template-columns:var(--cols);gap:12px;align-items:center;padding:7px 12px;background:var(--surface-2);font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ad-tr{display:grid;grid-template-columns:var(--cols);gap:12px;align-items:center;padding:7px 12px;min-height:46px;border-bottom:1px solid var(--border);font-size:13px;animation:adrise .3s both;animation-delay:calc(var(--i)*25ms);transition:background .15s}
.ad-tr:last-child{border-bottom:0} .ad-tr:hover{background:var(--surface-2)}
.ad-tr .who{display:flex;align-items:center;gap:10px;min-width:0} .ad-tr .who b{display:block;font-size:13.5px;font-weight:650;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .ad-tr .who small{display:block;font-size:12px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ad-tr .cell{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--ink-2)} .ad-tr .wrap{display:flex;flex-wrap:wrap;gap:4px} .ad-tr .end{display:flex;justify-content:flex-end;gap:6px;align-items:center}
.ad-seg{display:inline-flex;gap:0;padding:3px;border-radius:11px;background:var(--surface-2)}
.ad-seg button{display:inline-flex;align-items:center;gap:6px;height:28px;padding:0 11px;border-radius:8px;border:0;background:transparent;font:600 12.5px inherit;font-family:inherit;color:var(--ink-3);cursor:pointer;transition:background .18s,color .18s,box-shadow .18s}
.ad-seg button:hover{color:var(--ink)} .ad-seg button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(20,26,40,.14),0 0 0 1px var(--border)} .ad-seg .n{font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums} .ad-seg .on .n{color:var(--accent)}
.ad-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap} .ad-bar .sp{flex:1}
.ad-search{height:34px;min-width:200px;font-size:13px;border-radius:10px}
.ad-pill{padding:1px 8px;font-size:11px}
.ad-empty{padding:28px 16px;border-radius:14px} .ad-empty .ic{width:44px;height:44px;border-radius:14px}
.ad-ico{width:30px;height:30px;border-radius:9px} .ad-ico.lg{width:36px;height:36px}
.ad-grid2{grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:8px}
.ad-int{padding:12px 14px;border-radius:12px;gap:12px;transform:none!important}
.ad-calm{padding:8px 12px;font-size:12.5px;border-radius:10px}
.ad-limit b{font-size:13.5px}
.ad-load{padding:28px}
.adm table{border-spacing:0} .adm tbody td{box-shadow:none!important;border-radius:0!important;border-bottom:1px solid var(--border);padding:8px 12px;font-size:13px} .adm thead th{padding:7px 12px}
.adm-sec{display:flex;flex-direction:column;gap:8px} .rt-dim-s{font-size:12.5px;color:var(--ink-3)}
@media (max-width:900px){
.adm-layout{grid-template-columns:1fr;gap:14px}
.adm-nav{position:static;flex-direction:row;overflow-x:auto;gap:6px;padding-bottom:4px;scrollbar-width:none} .adm-nav::-webkit-scrollbar{display:none}
.adm-grp{flex-direction:row;gap:6px} .adm-gt{display:none} .adm-nav button{flex:none;height:36px;border-radius:99px;box-shadow:inset 0 0 0 1px var(--border)} .adm-nav button.on::before{display:none}
}
@media (prefers-reduced-motion:reduce){.adm *{animation:none!important;transition:none!important}}
`;

export const KitStyles = () => <style dangerouslySetInnerHTML={{ __html: KIT_CSS }} />;
