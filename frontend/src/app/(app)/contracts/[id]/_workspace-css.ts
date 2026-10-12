// The look of the contract page (the CLM workspace): one calm reading desk. A cool grey surround,
// one white contract page, and a review panel beside it. Everything is scoped under `.clm`, and the
// colour variables defined first are the ones every part of the page uses (the chat, the review
// cards, the lifecycle tab and the document sheet read them too), so a colour is changed in one place.
//
// The sidebar (AegisRail) and the other screens keep the app's own palette; only this page uses this one.

export const CLM_CSS = `
.clm{
  --bg:#e5e8ee;--surface:#fafbfc;--surface-2:#f0f2f5;--inset:#f0f2f5;
  --ink:#14171d;--ink-2:#3c424d;--ink-3:#626874;--border:#d9dde4;--border-strong:#c3c9d3;
  --accent:#2f56e0;--accent-soft:#e5ebfd;--accent-ink:#ffffff;--btn:#14171d;--btn-ink:#ffffff;
  --good:#16705a;--good-soft:#dcf2ea;--warn:#8a4d00;--warn-soft:#fcefdc;--crit:#b3302a;--crit-soft:#fbe9e7;
  --risk-med:#d98a1c;--risk-low:#5a9a78;--ai:#2f56e0;--ai-soft:#e5ebfd;--ext:#75589f;--ext-soft:#efe8f7;
  --paper:#ffffff;--paper-ink:#1b1d22;--paper-muted:#6a6e77;
  --p-del:#b3261e;--p-del-bg:#fde8e5;--p-ins:#0b6b55;--p-ins-bg:#dcf3ea;--p-hl:#fff0a0;--p-cmt:rgba(255,196,70,.26);--p-cmt-line:#e0a028;
  --sheet-shadow:0 1px 2px rgba(20,24,34,.06),0 14px 34px -14px rgba(20,24,34,.30);
  --shadow:0 1px 2px rgba(20,26,40,.05),0 10px 26px rgba(20,26,40,.06);--pop:0 12px 30px rgba(20,26,40,.16);
  --mono:var(--font-mono),ui-monospace,SFMono-Regular,Menlo,monospace;--sans:var(--font-sans);--serif:"Tinos","Times New Roman",Times,serif;
  height:100%;background:var(--bg);color:var(--ink);font:400 13px/1.5 var(--sans);-webkit-font-smoothing:antialiased
}
.dark .clm{
  --bg:#13161b;--surface:#171a20;--surface-2:#1f232b;--inset:#1f232b;
  --ink:#eceef3;--ink-2:#c3c8d2;--ink-3:#9aa1ae;--border:#2a2f39;--border-strong:#3a4150;
  --accent:#8ea3ff;--accent-soft:#232c55;--accent-ink:#0d1230;--btn:#eceef3;--btn-ink:#12141a;
  --good:#6fd6b6;--good-soft:#12332a;--warn:#f2b866;--warn-soft:#372812;--crit:#ff9188;--crit-soft:#3a1d1b;
  --ai:#8ea3ff;--ai-soft:#232c55;--ext:#ab90d2;--ext-soft:#221b31;
  --sheet-shadow:0 1px 2px rgba(0,0,0,.4),0 14px 34px -14px rgba(0,0,0,.75);--shadow:0 1px 2px rgba(0,0,0,.4),0 12px 30px rgba(0,0,0,.4);--pop:0 14px 34px rgba(0,0,0,.55)
}
.clm *{box-sizing:border-box}
.clm :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.clm .dim{color:var(--ink-3)}
.clm .lbl{font:600 10px/1.4 var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.clm .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.clm h1,.clm h2{margin:0}
.clm button{cursor:pointer;border:0;background:none;color:inherit;font:inherit}
.clm button:disabled{cursor:default}
.clm .app{height:100%;display:flex;flex-direction:column;min-height:0}
.clm .main{min-width:0;display:flex;flex-direction:column;min-height:0;height:100%}

/* buttons */
.clm .btn{height:34px;padding:0 14px;border-radius:9px;border:1px solid var(--border);background:var(--surface);color:var(--ink);font:500 13px var(--sans);display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap;text-decoration:none;transition:background .15s,border-color .15s,transform .06s,opacity .15s}
.clm .btn:hover{background:var(--surface-2)}
.clm .btn:active{transform:translateY(1px)}
.clm .btn.pri{background:var(--btn);color:var(--btn-ink);border-color:var(--btn)}
.clm .btn.pri:hover{background:var(--btn);opacity:.88;filter:none}
.clm .btn:disabled,.clm .btn[disabled]{opacity:.38;pointer-events:none;transform:none}
.clm .btn.sm{height:30px;padding:0 11px;font-size:12.5px}
.clm .iconbtn{width:32px;height:32px;border-radius:9px;border:1px solid var(--border);background:var(--surface);display:grid;place-items:center;color:var(--ink);text-decoration:none;flex:none}
.clm .iconbtn:hover{background:var(--surface-2)}
.clm .lnk{background:none;border:0;padding:0;color:var(--accent);font-weight:500;font-size:12.5px}
.clm .lnk:hover{text-decoration:underline}

/* title bar */
.clm .bar{flex:none;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 16px;background:var(--surface);border-bottom:1px solid var(--border);min-height:58px}
.clm .bar-l,.clm .bar-r{display:flex;align-items:center;gap:10px;min-width:0}
.clm .bar-r{flex:none;flex-wrap:wrap;justify-content:flex-end}
.clm .ttl{min-width:0}
.clm .crumb{display:flex;align-items:center;gap:6px;font-size:11.5px;color:var(--ink-3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clm .crumb a{color:inherit;text-decoration:none} .clm .crumb a:hover{color:var(--ink);text-decoration:underline}
.clm .ttl h1{font-size:15.5px;line-height:1.25;font-weight:600;letter-spacing:-.005em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.clm .vchip{font:500 12px var(--mono);color:var(--ink-3);border:1px solid var(--border);border-radius:7px;padding:3px 8px;white-space:nowrap}
.clm .vchip.flow{text-transform:none;max-width:220px;overflow:hidden;text-overflow:ellipsis}
.clm .vchip.none{font-family:var(--sans);background:none;cursor:pointer;color:var(--ink-2)} .clm .vchip.none:hover{border-color:var(--border-strong);color:var(--ink)}

/* stage row */
.clm .life{flex:none;padding:7px 16px;background:var(--surface);border-bottom:1px solid var(--border)}
.clm .life .lcs-detail{margin-top:8px}
.clm .lchip{display:inline-flex;align-items:center;gap:8px;font-size:12px;font-weight:500;padding:3px 10px;border-radius:99px;background:var(--surface-2);color:var(--ink-3);white-space:nowrap}
.clm .lchip.warn{background:var(--warn-soft);color:var(--warn)}

/* the "they sent it back" bar */
.clm .ctx{flex:none;display:flex;align-items:center;gap:14px;padding:9px 16px;border-bottom:1px solid color-mix(in srgb,var(--accent) 26%,var(--border));background:var(--accent-soft);flex-wrap:wrap}
.clm .ctx .cx-ic{width:28px;height:28px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none}
.clm .ctx .cx-t{font-size:12.5px;color:var(--ink);min-width:200px;flex:1}
.clm .cx-t .k{font:600 9px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--accent)} .clm .cx-t b{font-weight:650}

/* work area: the desk and the panel */
.clm .work{flex:1;min-height:0;display:grid;grid-template-columns:minmax(0,1fr) 404px}
.clm .canvas{min-width:0;min-height:0;display:flex;flex-direction:column;overflow:hidden;background:var(--bg);position:relative}
.clm .canvas-fill{flex:1;min-height:0;display:flex;flex-direction:column}
.clm .canvas-fill>*{flex:1;min-height:0}
.clm .backrow{flex:none;padding:10px 20px 0}
.clm .cd-pill .seg-ctl{display:flex;background:var(--surface-2);border-radius:8px;padding:2px}
.clm .cd-pill .seg-ctl button{border-radius:6px;padding:4px 11px;font-size:12.5px;font-weight:500;color:var(--ink-3);min-height:26px}
.clm .cd-pill .seg-ctl button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(20,24,34,.15)}
.clm .cd-pill .dsep{width:1px;height:20px;background:var(--border)}
.clm .cd-pill .tbtn{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;color:var(--ink)}
.clm .cd-pill .tbtn:hover:not(:disabled){background:var(--surface-2)} .clm .cd-pill .tbtn:disabled{opacity:.35}
.clm .cd-pill .zoomv{font:500 12px var(--mono);color:var(--ink-3);min-width:38px;text-align:center}
.clm .panel{border-left:1px solid var(--border);background:var(--surface);display:flex;flex-direction:column;min-height:0;min-width:0}

/* panel header: title, how much is decided, the tabs */
.clm .ph2{flex:none;padding:14px 16px 0;border-bottom:1px solid var(--border)}
.clm .ph2-top{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.clm .ph2 h2{font-size:16px;font-weight:600;letter-spacing:-.005em}
.clm .ph2 .sub{font-size:12px;color:var(--ink-3);text-align:right}
.clm .rsum{margin:10px 0 2px}
.clm .rsum-t{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;flex-wrap:wrap}
.clm .rsum-t b{font-weight:600}
.clm .risks{display:flex;gap:10px;color:var(--ink-3);font-size:12px}
.clm .risks span{display:inline-flex;align-items:center;gap:5px}
.clm .risks i{width:7px;height:7px;border-radius:50%;background:var(--rc)}
.clm .track{height:4px;border-radius:4px;background:var(--surface-2);margin-top:8px;overflow:hidden}
.clm .track i{display:block;height:100%;background:var(--good);border-radius:4px;transition:width .35s ease}
.clm .tabs{display:flex;align-items:flex-end;gap:13px;margin-top:6px;flex:none;position:relative}
.clm .tabs-main{display:flex;gap:13px;min-width:0;overflow-x:auto;scrollbar-width:none}
.clm .tabs-main::-webkit-scrollbar{display:none}
.clm .tabs button{padding:10px 0 9px;font-size:13px;font-weight:500;color:var(--ink-3);border-bottom:2px solid transparent;display:inline-flex;align-items:center;gap:7px;white-space:nowrap;flex:none}
.clm .tabs button:hover{color:var(--ink)}
.clm .tabs button.on{color:var(--ink);border-bottom-color:var(--ink)}
.clm .tabs button .b{font:500 11px var(--mono);background:var(--surface-2);color:var(--ink-3);padding:1px 6px;border-radius:99px}
.clm .more{position:relative;flex:none;margin-left:auto}
.clm .tabs .menu{position:absolute;right:0;top:calc(100% + 4px);z-index:30;min-width:150px;padding:4px;background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:var(--pop);display:flex;flex-direction:column}
.clm .tabs .menu button{padding:8px 10px;border-bottom:0;border-radius:7px;justify-content:space-between;font-size:13px;color:var(--ink)}
.clm .tabs .menu button:hover{background:var(--surface-2)} .clm .tabs .menu button.on{background:var(--surface-2);font-weight:600;border-bottom:0}
.clm .tabwrap{flex:1;min-height:0;display:flex;flex-direction:column}
.clm .pfoot{flex:none;display:flex;align-items:center;gap:7px;padding:9px 16px;border-top:1px solid var(--border);font-size:12px;color:var(--ink-3)}
.clm .pfoot svg{flex:none}

/* plain lists in the panel (sources, parties, clauses) */
.clm .list{flex:1;overflow:auto;padding:14px}
.clm .src{border:1px solid var(--border);background:var(--surface);border-radius:12px;padding:12px 14px;margin-bottom:10px}
.clm .src .st{font-weight:600;font-size:13px;display:flex;align-items:center;gap:8px}
.clm .src .stag{font:500 11px var(--mono);color:var(--accent);background:var(--accent-soft);padding:1px 7px;border-radius:99px;margin-left:auto}
.clm .src .sx{font-size:12.5px;color:var(--ink-2);margin-top:6px;line-height:1.55}
.clm .partyform{display:flex;flex-direction:column;gap:7px;margin-top:8px}
.clm .partyform input{height:32px;padding:0 10px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--ink);font-size:13px;outline:none}
.clm .partyform input:focus{border-color:var(--accent);background:var(--surface);box-shadow:0 0 0 3px var(--accent-soft)}

@media (max-width:1180px){.clm .work{grid-template-columns:minmax(0,1fr) 372px}}
@media (max-width:980px){
  .clm,.clm .app,.clm .main{height:auto;min-height:100%}
  .clm .work{display:block}
  .clm .canvas{height:72vh}
  .clm .panel{height:86vh;border-left:0;border-top:1px solid var(--border)}
  .clm .bar{position:sticky;top:0;z-index:30}
}
@media (max-width:640px){
  .clm .crumb,.clm .vchip{display:none}
  .clm .bar-r .btn{padding:0 10px}
}
@media (prefers-reduced-motion:reduce){.clm *{transition:none!important;animation:none!important}}
`;
