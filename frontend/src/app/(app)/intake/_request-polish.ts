// The look of the request page, in one place, appended LAST so it wins over each card's own rules.
// Enterprise "record page": white cards on the canvas, ONE 10px radius, ONE border per card, hairline dividers
// inside, no shadows, no gradients, one accent (indigo) plus green / amber / red only where they mean something.
// Dense: 13px text, 12px notes, 10-14px padding. Palette variables only (light + dark).
export const POLISH_CSS = `
.ro{max-width:1240px;margin:0 auto}

/* ---- rhythm ---- */
.ro .body{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:12px;align-items:start;padding:12px 0}
.ro .col{display:flex;flex-direction:column;gap:12px;min-width:0}
.ro .lower{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;align-items:start;padding:0 0 24px}
.ro .decision{display:flex;flex-direction:column;gap:12px}
@media (max-width:1180px){.ro .body,.ro .lower{grid-template-columns:minmax(0,1fr)}}

/* ---- every card ---- */
.ro .card{background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:none;overflow:hidden}
.ro .card>.ch{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border);background:var(--inset)}
.ro .card>.ch h2{margin:0;font:650 13px var(--sans);letter-spacing:-.005em;color:var(--ink)}
.ro .card>.ch .cnt,.ro .card>.ch .chsub{margin-left:auto;font:600 11px var(--sans);color:var(--ink-3);background:none;padding:0}
.ro .card>.cb{padding:12px 14px 14px}
.ro .glyph,.ro .glyph.g-ind,.ro .glyph.g-vio,.ro .glyph.g-sky,.ro .glyph.g-emr,.ro .glyph.g-amb,.ro .glyph.g-red,.ro .glyph.flow{width:20px;height:20px;display:grid;place-items:center;flex:none;border-radius:0;background:none;color:var(--accent)}
.ro .glyph.warn{color:var(--warn);background:none}
.ro .glyph .ic,.ro .glyph svg{width:16px;height:16px}
.ro .hint{margin:0 0 10px;font-size:12px;line-height:1.45;color:var(--ink-3)}
.ro .btn{min-height:30px;padding:0 12px;border-radius:6px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font:600 12.5px var(--sans);gap:6px;box-shadow:none}
.ro .btn:hover:not(:disabled){background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.ro .btn.pri,.ro .btn.pri:hover:not(:disabled){background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
.ro .btn.pri:hover:not(:disabled){filter:brightness(1.08)}
.ro .btn:disabled{opacity:.5}
.ro .btn:focus-visible,.ro .xtoggle:focus-visible,.ro .lnk:focus-visible,.ro .ministep:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ro .sel,.ro .cin{min-height:30px;padding:5px 10px;border-radius:6px;font-size:12.5px}
.ro .xtoggle,.ro .lnk{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid var(--border-strong);border-radius:6px;background:var(--surface);color:var(--ink);font:600 12px var(--sans);cursor:pointer}
.ro .xtoggle:hover,.ro .lnk:hover{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);text-decoration:none}
.ro .ministep{padding:5px 11px;border-radius:6px;font:600 12px var(--sans);box-shadow:none}

/* ---- header: icon, title, breadcrumb, status ---- */
.ro .rohd{display:flex;align-items:center;gap:12px;margin:0 0 8px;padding:12px 16px;background:var(--surface);border:1px solid var(--border);border-radius:10px;box-shadow:none;overflow:visible}
.ro .rohd::before{display:none}
.ro .rohd .pico{width:34px;height:34px;border-radius:8px;display:grid;place-items:center;flex:none;background:var(--accent);color:var(--accent-ink)}
.ro .rohd .pico svg{width:18px;height:18px}
.ro .rohd .ttlblk{flex:1;min-width:0}
.ro .rohd h1{margin:0;font:700 17px/1.25 var(--sans);letter-spacing:-.02em}
.ro .rohd .crumb{display:flex;align-items:center;gap:6px;margin:2px 0 0;font-size:12px;color:var(--ink-3)}
.ro .rohd .crumb .back{height:22px;padding:0 6px;margin-left:-6px;border:0;border-radius:6px;background:none;color:var(--accent);font:600 12px var(--sans);cursor:pointer}
.ro .rohd .crumb .back:hover{background:var(--accent-soft)}
.ro .rohd .crumb .refchip{padding:1px 7px;border-radius:5px;background:var(--surface-2);color:var(--ink-2);font:600 11px var(--mono)}
.ro .rohd .subl{margin-top:2px;font-size:12px;color:var(--ink-3)}
.ro .rohd .pills{display:flex;gap:6px;margin:0;flex:none}
.ro .rohd .pill{padding:3px 11px;font-size:12px;font-weight:700}
.ro .rohd.late{border-color:color-mix(in srgb,var(--crit) 45%,var(--border))}
.ro .rohd.risk{border-color:color-mix(in srgb,var(--warn) 45%,var(--border))}

/* ---- stage timeline (reference: milestone track): nodes on a line, label + caption under each; the current one is a dark pill ---- */
.ro .lcwrap{min-width:0;overflow-x:auto;padding:16px 8px 6px;background:var(--surface);border:1px solid var(--border);border-bottom:0;border-radius:10px 10px 0 0}
.ro .stline{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 4px;padding:8px 14px;border:1px solid var(--border);border-radius:0 0 10px 10px;background:var(--surface);color:var(--ink);font:600 12.5px var(--sans)}
.ro .stline.warn{background:var(--warn-soft);color:var(--warn)}
.ro .lcwrap>div:last-child{margin:6px 0 4px;border-radius:8px;border:1px solid var(--border);background:var(--inset)}

/* ---- the decision: the one card with an accent frame ---- */
.ro .decision>.card{border:1px solid var(--accent)}
.ro .decision>.card>.ch{background:var(--accent-soft);border-bottom:1px solid color-mix(in srgb,var(--accent) 25%,var(--border))}
.ro .decision>.card>.ch h2{font-size:14px;font-weight:700;color:var(--accent)}
.ro .decision>.card>.ch h2::after{content:"Do first";margin-left:8px;padding:2px 8px;border-radius:99px;background:var(--accent);color:var(--accent-ink);font:700 10px var(--sans);letter-spacing:.03em;vertical-align:1px}
.ro .decision>.card>.ch .cnt{background:none;color:var(--accent);padding:0}
.ro .decide{margin-top:8px;padding:10px 12px;border:0;border-radius:6px;background:var(--inset)}
.ro .decide .dw{margin-bottom:10px;font-size:12.5px}
.ro .decide .db{display:flex;flex-wrap:wrap;gap:8px}
.ro .decide .db .btn{flex:1 1 calc(50% - 4px);justify-content:center}
.ro .decide .db .btn.pri{flex:1 1 100%;min-height:34px;background:var(--good);border-color:var(--good);color:#fff}
.ro .decide.warn .db .btn.pri{background:var(--warn);border-color:var(--warn)}
.ro .apc .decide .db .btn:not(.pri){color:var(--crit);border-color:color-mix(in srgb,var(--crit) 45%,var(--border-strong))}
.ro .apc .decide .db .btn:not(.pri):hover{background:var(--crit-soft)}
.ro .apc .src{display:inline-block;margin:0 0 0 8px;white-space:nowrap;vertical-align:1px}
.ro .apc .facts{grid-template-columns:1fr}

/* ---- what was asked: label / value pairs ---- */
.ro .facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:0 20px}
.ro .facts .f{padding:7px 0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none}
.ro .facts .f .k{font:600 10.5px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .facts .f .v{margin-top:1px;font-size:13px;font-weight:600}
.ro .quote{margin-top:10px;padding:8px 12px;max-height:7.5em;overflow:auto;border:0;border-left:3px solid var(--accent);border-radius:0 6px 6px 0;background:var(--inset);font-size:12.5px;line-height:1.5;color:var(--ink)}
.ro .needsinfo{border-radius:6px}

/* ---- AI risk review ---- */
.ro .risktop{display:flex;align-items:center;gap:12px;padding:0 0 10px;margin-bottom:10px;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none}
.ro .rgauge{width:38px;height:38px;border-radius:8px;font:800 16px var(--sans);box-shadow:none}
.ro .rmeta .rb{font-size:14px} .ro .rmeta .rc{font-size:12px}
.ro .sevgroup{margin-bottom:8px}
.ro .sevhead{padding:3px 0}
.ro .risk{margin:0 0 6px;padding:8px 12px;border:1px solid var(--border);border-left:3px solid var(--border-strong);border-radius:6px;background:var(--surface);box-shadow:none}
.ro .risk.crit,.ro .risk.high{border-left-color:var(--crit)} .ro .risk.med{border-left-color:var(--warn)}
.ro .risk .rfix{padding:0;background:none;margin-top:6px}

/* ---- workflow: one row per step, details right under each row ---- */
.ro .wfprog{flex:none;width:80px;height:6px;border-radius:99px;background:var(--surface-2);overflow:hidden}
.ro .wfprog i{display:block;height:100%;background:var(--good)}
.ro .wft{display:flex;flex-direction:column;margin:0}
.ro .wft .t{position:relative;display:grid;grid-template-columns:26px minmax(0,1fr) auto;column-gap:10px;row-gap:1px;align-items:start;padding:10px 12px 10px 0;margin:0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none}
.ro .wft .t:last-child{border-bottom:0}
.ro .wft .t>*{grid-column:2}
.ro .wft .t::before{display:none}
.ro .wft .tnode{position:static;grid-column:1;grid-row:1/6;width:24px;height:24px;margin-top:1px;border-width:2px;font-size:11px;box-shadow:none}
.ro .wft .t.done .tnode{background:var(--good);border-color:var(--good);color:#fff}
.ro .wft .t.cur .tnode{border-color:var(--accent);background:var(--accent);color:var(--accent-ink);box-shadow:0 0 0 3px var(--accent-soft)}
.ro .wft .t.warn .tnode{border-color:var(--warn);background:var(--warn);color:#fff}
.ro .wft .t::after{grid-column:3;grid-row:1;align-self:start;padding:2px 9px;border-radius:99px;font:700 11px var(--sans);background:var(--surface-2);color:var(--ink-3);content:"Up next"}
.ro .wft .t.done::after{content:"Done";background:var(--good-soft);color:var(--good)}
.ro .wft .t.cur::after{content:"In progress";background:var(--accent-soft);color:var(--accent)}
.ro .wft .t.warn::after{content:"Needs attention";background:var(--warn-soft);color:var(--warn)}
.ro .wft .t.skip::after{content:"Skipped"}
.ro .wft .t.cur,.ro .wft .t.warn{margin:0 -14px;padding:10px 14px;background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}
.ro .wft .t.warn{background:var(--warn-soft);box-shadow:inset 3px 0 0 var(--warn)}
.ro .wft .t.cur .tnode,.ro .wft .t.warn .tnode{margin-left:0}
.ro .wft .t b{font:650 13px/1.3 var(--sans);color:var(--ink)}
.ro .wft .t.todo b,.ro .wft .t.skip b{color:var(--ink-2);font-weight:600}
.ro .wft .t small{font-size:12px;line-height:1.4;color:var(--ink-2)}
.ro .wft .t .over{color:var(--crit);font-weight:700}
.ro .wft .t .tstage{padding:0;background:none;color:var(--ink-3);font:700 10px var(--sans);letter-spacing:.07em;text-transform:uppercase}
.ro .wft .t .ministep,.ro .wft .t .doorway{justify-self:start;align-self:start;width:auto;margin-top:6px}
.ro .wflinks{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;padding-top:10px;border-top:1px solid var(--border)}
.ro .wfpanel{margin-top:10px;padding:10px 12px;border:1px solid var(--border);border-radius:6px;background:var(--inset)}
.ro .wfpanel .lbl{margin-bottom:6px}
.ro .sdetail{padding:0;border:0;background:none;margin:0 0 8px}

/* ---- counterparty ---- */
.ro .cphead{display:flex;align-items:center;gap:10px;padding:0 0 10px;margin:0 0 4px;border-bottom:1px solid var(--border)}
.ro .cpav{width:32px;height:32px;border-radius:8px;display:grid;place-items:center;flex:none;background:var(--surface-2);color:var(--accent);font:700 15px var(--sans)}
.ro .cpname{font-size:14px;font-weight:700} .ro .cpstat{margin:1px 0 0;font-size:12px;color:var(--ink-3)}
.ro .cph{display:flex;align-items:center;gap:8px;margin-top:10px;font:700 10.5px var(--sans);letter-spacing:.07em;color:var(--ink-3)}
.ro .cph::after{content:"";flex:1;height:1px;background:var(--border)}
.ro .cpfile{padding:8px 0;margin:0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none}
.ro .cpfile:last-child{border-bottom:0}
.ro .cpfile a{font-size:13px;font-weight:600}
.ro .cppill{padding:1px 8px;font-weight:700;background:var(--accent-soft);color:var(--accent)}

/* ---- people, documents ---- */
.ro .person{display:flex;align-items:center;gap:10px;padding:7px 0;margin:0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none;box-shadow:none}
.ro .person:last-child{border-bottom:0}
.ro .person .av{width:28px;height:28px;font-size:11px;background:var(--surface-2);color:var(--ink-2)}
.ro .person.own .av{background:var(--accent);color:var(--accent-ink)}
.ro .person .n{font-size:13px;font-weight:650}
.ro .person .r{display:inline-block;margin-top:1px;padding:0 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);font:700 10.5px var(--sans);text-transform:capitalize}
.ro .person.own .r{background:var(--accent-soft);color:var(--accent)}
.ro .reassign{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;margin:8px 0 4px}
.ro .reassign .btn,.ro .reassign .btn.wide{width:auto;white-space:nowrap}
.ro .docrow{display:flex;align-items:center;gap:10px;padding:7px 0;margin:0;border:0;border-bottom:1px solid var(--border);border-radius:0;background:none;box-shadow:none}
.ro .docrow .x{width:28px;height:28px;border-radius:6px}
.ro .docacts{display:flex;flex-direction:column;gap:6px;margin-top:10px}

/* ---- activity: a feed ---- */
.ro .actv .day{display:block;margin:10px 0 4px;padding:0;background:none;font:700 10.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.ro .actv .ev{display:flex;align-items:center;gap:10px;padding:6px 0;margin:0;border-radius:0;background:none;border-bottom:1px solid var(--border)}
.ro .actv .ev::before{display:none}
.ro .actv .ev i{width:24px;height:24px;margin:0;border-radius:50%;display:grid;place-items:center;flex:none;box-shadow:none;background:var(--surface-2);color:var(--ink-3)}
.ro .actv .ev i svg{width:13px;height:13px}
.ro .actv .ev i .dot{width:6px;height:6px;border-radius:50%;background:var(--ink-3)}
.ro .actv .ev i.ai{background:var(--accent-soft);color:var(--accent)}
.ro .actv .ev i.done{background:var(--good-soft);color:var(--good)}
.ro .actv .ev i.stage{background:var(--warn-soft);color:var(--warn)}
.ro .actv .ev .sent{font-size:12.5px;font-weight:600;color:var(--ink)} .ro .actv .ev .who{font-size:12px}
.ro .actv .ev small{margin-left:auto;font-size:11.5px;color:var(--ink-3)}

/* ---- comments: bubbles + composer ---- */
.ro .wfcomments .clist{display:flex;flex-direction:column;gap:8px;margin:0 0 10px}
.ro .wfcomments .clist li{display:flex;gap:8px;align-items:flex-start;padding:0;border:0;background:none;border-radius:0}
.ro .wfcomments .cav{width:26px;height:26px;border-radius:50%;display:grid;place-items:center;flex:none;background:var(--accent);color:var(--accent-ink);font:700 10px var(--sans)}
.ro .wfcomments .cbody{flex:1;min-width:0;padding:7px 11px;border-radius:2px 8px 8px 8px;background:var(--inset)}
.ro .wfcomments li.ret .cbody{background:var(--warn-soft)}
.ro .wfcomments .cmeta{margin-bottom:2px;font-size:12px} .ro .wfcomments .ctext{font-size:12.5px;line-height:1.5}
.ro .wfcomments .composer{display:flex;align-items:center;gap:6px;padding:4px 4px 4px 12px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface)}
.ro .wfcomments .composer:focus-within{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.ro .wfcomments .composer .cin{flex:1;border:0;background:none;padding:4px 0;min-height:0;outline:none}
.ro .emptynote{display:flex;flex-direction:column;align-items:center;gap:3px;padding:14px 10px;border-radius:6px;background:var(--inset);text-align:center;color:var(--ink-2);font:600 12.5px var(--sans)}
.ro .emptynote .ic{width:20px;height:20px;color:var(--ink-3)}
.ro .emptynote small{font:400 12px var(--sans);color:var(--ink-3)}

/* ---- Ask Aegis ---- */
.ro .chat .chip{padding:7px 11px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--ink);text-align:left;font:500 12.5px var(--sans)}
.ro .chat .chip:hover{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
.ro .chat .bub{border-radius:8px}

/* ---- tracker: summary row, and motion (the line draws, nodes pop in one after another, the current one breathes) ---- */
.ro .lchead{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 8px 10px;font-size:12px;color:var(--ink-3)}
.ro .lchead b{color:var(--accent);font-weight:700;font-size:13px}
.ro .lclegend{display:inline-flex;align-items:center;gap:6px}
.ro .lclegend i{width:8px;height:8px;border-radius:50%;margin-left:8px;background:var(--border-strong)}
.ro .lclegend i.d{background:var(--accent)} .ro .lclegend i.c{background:var(--ink)}
@keyframes lc-draw{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes lc-pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes lc-rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@keyframes lc-open{from{opacity:0;transform:translateY(-6px) scaleY(.97)}to{opacity:1;transform:none}}
@keyframes lc-breathe{0%{box-shadow:0 0 0 4px var(--surface),0 0 0 6px color-mix(in srgb,var(--accent) 30%,transparent)}70%{box-shadow:0 0 0 4px var(--surface),0 0 0 15px color-mix(in srgb,var(--accent) 0%,transparent)}100%{box-shadow:0 0 0 4px var(--surface),0 0 0 6px color-mix(in srgb,var(--accent) 0%,transparent)}}
.ro .lc-panel{transform-origin:top;animation:lc-open .26s cubic-bezier(.22,.8,.2,1) backwards}
.ro .lc-row{animation:lc-rise .28s ease-out backwards;animation-delay:calc(var(--r) * 50ms + 90ms)}

/* ---- workflow: stage groups, then a full set of facts under every step ---- */
.ro .wft .wsg{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:8px;padding:6px 0;border-bottom:1px solid var(--border-strong);font:700 11px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-2)}
.ro .wft .wsg:first-child{margin-top:0}
.ro .wft .wsg em{font-style:normal;font-weight:600;letter-spacing:0;text-transform:none;color:var(--ink-3)}
.ro .wft .t .tstage{display:none}
.ro .wft .tmeta{display:flex;flex-wrap:wrap;gap:4px;margin-top:3px}
.ro .wft .tchip{padding:1px 8px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);font:600 11px var(--sans)}
.ro .wft .tchip.kind{background:var(--accent-soft);color:var(--accent)}
.ro .wft .tchip.when{background:none;padding-left:2px;color:var(--ink-3);font-weight:500}
.ro .wft .t .tnote{margin-top:3px;color:var(--ink-2);font-style:italic}

/* =====================================================================================================
   v7 — colourful, with meaning. Solid colours only (no gradients): each kind of information has its own hue,
   used the same way everywhere:  indigo = the request / actions · violet = stage & people · sky = money & AI ·
   green = done / good · amber = needs care · red = problem.
   ===================================================================================================== */
/* ---- key facts row ---- */
.ro .kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin:0 0 12px}
@media (max-width:1280px){.ro .kpis{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){.ro .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
.ro .kpi{--c:var(--c-ind);display:flex;align-items:center;gap:10px;min-width:0;padding:10px 12px;border-radius:10px;background:color-mix(in srgb,var(--c) 11%,var(--surface));border:1px solid color-mix(in srgb,var(--c) 28%,var(--border));animation:lc-rise .3s ease-out backwards;animation-delay:calc(var(--k) * 50ms)}
.ro .kpi.k-red{--c:var(--c-red)} .ro .kpi.k-amber{--c:var(--c-amb)} .ro .kpi.k-green{--c:var(--c-emr)} .ro .kpi.k-vio{--c:var(--c-vio)} .ro .kpi.k-sky{--c:var(--c-sky)} .ro .kpi.k-grey{--c:var(--ink-3)}
.ro .kic{width:32px;height:32px;border-radius:8px;display:grid;place-items:center;flex:none;background:var(--c);color:#fff}
.ro .kic svg{width:17px;height:17px;stroke-width:1.9}
.ro .ktx{display:flex;flex-direction:column;min-width:0;line-height:1.25}
.ro .kl{font:700 10px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.ro .kv{font:700 14px var(--sans);color:var(--c);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ro .ks{font-size:11px;color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}

/* ---- header: quick actions in their own colours ---- */
.ro .hdacts{display:flex;flex-wrap:wrap;gap:6px;justify-content:flex-end;flex:none}
.ro .btn.act-ind,.ro .btn.act-vio,.ro .btn.act-sky,.ro .btn.act-emr{background:color-mix(in srgb,var(--c) 11%,var(--surface));border-color:color-mix(in srgb,var(--c) 40%,var(--border));color:var(--c)}
.ro .btn.act-ind{--c:var(--c-ind)} .ro .btn.act-vio{--c:var(--c-vio)} .ro .btn.act-sky{--c:var(--c-sky)} .ro .btn.act-emr{--c:var(--c-emr)}
.ro .btn.act-ind:hover:not(:disabled),.ro .btn.act-vio:hover:not(:disabled),.ro .btn.act-sky:hover:not(:disabled),.ro .btn.act-emr:hover:not(:disabled){background:var(--c);border-color:var(--c);color:#fff}
.ro .btn .ic{width:14px;height:14px}
.ro .rohd .pico{background:var(--c-ind)}
.ro .rohd.late .pico{background:var(--c-red)} .ro .rohd.risk .pico{background:var(--c-amb)}

/* ---- cards: each has its own coloured icon chip and a tinted header band ---- */
.ro .glyph,.ro .glyph.g-ind,.ro .glyph.g-vio,.ro .glyph.g-sky,.ro .glyph.g-emr,.ro .glyph.g-amb,.ro .glyph.g-red,.ro .glyph.flow{--c:var(--c-ind);width:26px;height:26px;border-radius:7px;background:var(--c);color:#fff}
.ro .glyph.g-vio{--c:var(--c-vio)} .ro .glyph.g-sky{--c:var(--c-sky)} .ro .glyph.g-emr{--c:var(--c-emr)} .ro .glyph.g-amb{--c:var(--c-amb)} .ro .glyph.g-red{--c:var(--c-red)} .ro .glyph.warn{--c:var(--c-amb);background:var(--c);color:#fff}
.ro .glyph .ic,.ro .glyph svg{width:15px;height:15px}
.ro .card.tint-ind>.ch{background:color-mix(in srgb,var(--c-ind) 9%,var(--surface))}
.ro .card.tint-vio>.ch{background:color-mix(in srgb,var(--c-vio) 9%,var(--surface))}
.ro .card.tint-sky>.ch{background:color-mix(in srgb,var(--c-sky) 10%,var(--surface))}
.ro .card.tint-emr>.ch{background:color-mix(in srgb,var(--c-emr) 10%,var(--surface))}
.ro .card.tint-amb>.ch{background:color-mix(in srgb,var(--c-amb) 11%,var(--surface))}
.ro .card.tint-red>.ch{background:color-mix(in srgb,var(--c-red) 8%,var(--surface))}
.ro .card.tint-ind{border-top:3px solid var(--c-ind)} .ro .card.tint-vio{border-top:3px solid var(--c-vio)} .ro .card.tint-sky{border-top:3px solid var(--c-sky)}
.ro .card.tint-emr{border-top:3px solid var(--c-emr)} .ro .card.tint-amb{border-top:3px solid var(--c-amb)} .ro .card.tint-red{border-top:3px solid var(--c-red)}

/* ---- tracker: done = green, now = dark pill with an indigo ring ---- */
.ro .lchead b{color:var(--c-emr)} .ro .lclegend i.d{background:var(--c-emr)} .ro .lclegend i.c{background:var(--c-ind)}
.ro .stline{background:color-mix(in srgb,var(--c-ind) 9%,var(--surface));color:var(--c-ind)}

/* ---- decision: coloured buttons, one meaning each ---- */
.ro .decision>.card{border-color:var(--c-ind);border-top-width:3px}
.ro .decide .db .btn{font-weight:700}
.ro .decide .db .btn:not(.pri){background:color-mix(in srgb,var(--c-ind) 10%,var(--surface));border-color:color-mix(in srgb,var(--c-ind) 40%,var(--border));color:var(--c-ind)}
.ro .decide .db .btn:not(.pri):nth-child(2){background:color-mix(in srgb,var(--c-amb) 12%,var(--surface));border-color:color-mix(in srgb,var(--c-amb) 45%,var(--border));color:var(--c-amb)}
.ro .apc .decide .db .btn:not(.pri){background:color-mix(in srgb,var(--c-red) 9%,var(--surface));border-color:color-mix(in srgb,var(--c-red) 45%,var(--border));color:var(--c-red)}
.ro .decide .db .btn.pri{background:var(--c-emr);border-color:var(--c-emr);color:#fff}
.ro .decide.warn .db .btn.pri{background:var(--c-amb);border-color:var(--c-amb)}
.ro .btn.pri,.ro .btn.pri:hover:not(:disabled){background:var(--c-ind);border-color:var(--c-ind)}

/* ---- people, workflow, comments, counterparty: a hue each ---- */
.ro .person .av{background:var(--c-ind);color:#fff}
.ro .person.req .av{background:var(--c-vio);color:#fff} .ro .person.dep .av{background:var(--c-sky);color:#fff}
.ro .person.own .r{background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.ro .person.req .r{background:color-mix(in srgb,var(--c-vio) 14%,transparent);color:var(--c-vio)}
.ro .person.dep .r{background:color-mix(in srgb,var(--c-sky) 15%,transparent);color:var(--c-sky)}
.ro .wft .t.done .tnode{background:var(--c-emr);border-color:var(--c-emr)}
.ro .wft .t.cur .tnode{background:var(--c-ind);border-color:var(--c-ind)}
.ro .wft .t.cur{background:color-mix(in srgb,var(--c-ind) 10%,var(--surface));box-shadow:inset 3px 0 0 var(--c-ind)}
.ro .wft .t.done::after{background:color-mix(in srgb,var(--c-emr) 15%,transparent);color:var(--c-emr)}
.ro .wft .t.cur::after{background:var(--c-ind);color:#fff}
.ro .wft .tchip.kind{background:color-mix(in srgb,var(--c-vio) 14%,transparent);color:var(--c-vio)}
.ro .wft .wsg{color:var(--ink);border-bottom-color:var(--c-vio)}
.ro .wfprog i{background:var(--c-emr)}
.ro .wfcomments .clist li:nth-child(3n+1) .cav{background:var(--c-ind);color:#fff}
.ro .wfcomments .clist li:nth-child(3n+2) .cav{background:var(--c-vio);color:#fff}
.ro .wfcomments .clist li:nth-child(3n) .cav{background:var(--c-sky);color:#fff}
.ro .cpav{background:var(--c-red);color:#fff}
.ro .cppill{background:color-mix(in srgb,var(--c-red) 12%,transparent);color:var(--c-red)}
.ro .actv .ev i.ai{background:color-mix(in srgb,var(--c-vio) 16%,transparent);color:var(--c-vio)}
.ro .risktop .rgauge{border-radius:10px}
.ro .facts .f:nth-child(5n+1) .k{color:var(--c-ind)} .ro .facts .f:nth-child(5n+2) .k{color:var(--c-vio)} .ro .facts .f:nth-child(5n+3) .k{color:var(--c-emr)} .ro .facts .f:nth-child(5n+4) .k{color:var(--c-amb)} .ro .facts .f:nth-child(5n+5) .k{color:var(--c-red)}

/* stage panel: timeline rail, progress, row hover */
.ro .lc-bar{display:block;width:96px;height:6px;border-radius:99px;background:var(--border);overflow:hidden}
.ro .lc-bar i{display:block;height:100%;border-radius:99px;background:var(--good);transform-origin:left;animation:lc-fill .7s cubic-bezier(.22,.8,.2,1) .15s backwards}
@keyframes lc-fill{from{transform:scaleX(0)}to{transform:none}}
/* KPI tiles are buttons: lift on hover, reveal where they lead; target card flashes */
.ro button.kpi{text-align:left;cursor:pointer;position:relative;font:inherit;transition:transform .15s,box-shadow .15s,border-color .15s}
.ro button.kpi:hover{transform:translateY(-2px);box-shadow:0 6px 14px rgba(20,26,40,.1);border-color:var(--c)}
.ro button.kpi:active{transform:none}
.ro .kgo{position:absolute;right:8px;top:6px;display:inline-flex;align-items:center;gap:2px;font-size:10.5px;font-weight:600;color:var(--c);opacity:0;transform:translateY(3px);transition:opacity .15s,transform .15s;white-space:nowrap}
.ro .kgo svg{width:12px;height:12px}
.ro button.kpi:hover .kgo,.ro button.kpi:focus-visible .kgo{opacity:1;transform:none}
.ro .card.flash{animation:card-flash 1.5s ease-out}
@keyframes card-flash{0%,35%{box-shadow:0 0 0 3px var(--accent)}100%{box-shadow:0 0 0 0 transparent}}

/* section tabs: only the open pane is on screen; the others stay mounted (drafts, scroll) but off-screen and inert */
.ro .rtabs{display:flex;gap:2px;overflow-x:auto;margin:0 0 12px;border-bottom:1px solid var(--border)}
.ro .rtabs button{position:relative;display:inline-flex;align-items:center;gap:6px;padding:10px 14px;font:600 13px var(--sans);color:var(--ink-3);white-space:nowrap;border-radius:8px 8px 0 0;transition:color .15s,background .15s}
.ro .rtabs button:hover{color:var(--ink);background:var(--surface-2)}
.ro .rtabs button[aria-selected="true"]{color:var(--accent)}
.ro .rtabs button[aria-selected="true"]::after{content:"";position:absolute;left:10px;right:10px;bottom:-1px;height:2px;border-radius:2px;background:var(--accent);animation:rt-in .25s ease-out}
@keyframes rt-in{from{transform:scaleX(0)}to{transform:none}}
.ro .rtabs i{font:700 10.5px var(--sans);font-style:normal;padding:1px 7px;border-radius:99px;background:var(--surface-2);color:var(--ink-2)}
.ro .rtabs button[aria-selected="true"] i{background:color-mix(in srgb,var(--accent) 14%,transparent);color:var(--accent)}
.ro .rpane{display:flex;flex-direction:column;gap:12px;animation:rp-in .3s cubic-bezier(.2,.9,.25,1)}
@keyframes rp-in{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.ro .rpane:not([data-on]){position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);opacity:0;pointer-events:none;animation:none}
.ro .col{position:relative}

/* ---- v10: the request page in the mockup's shape ---- */
.ro .rohd{display:flex;align-items:flex-start;gap:16px;margin:0 0 14px;padding:0;background:none;border:0;box-shadow:none}
.ro .rohd .pico{width:58px;height:58px;border-radius:16px;background:var(--accent)!important;margin-top:22px}
.ro .rohd .pico svg{width:28px;height:28px}
.ro .rohd .ttlblk{display:flex;flex-direction:column;gap:6px}
.ro .rohd .crumb{order:-1;margin:0 0 4px;font-size:12.5px}
.ro .rohd .crumb b{font:600 12px var(--mono);padding:1px 7px;border-radius:6px;background:var(--surface-2);color:var(--ink-2)}
.ro .rh-title{display:flex;align-items:center;flex-wrap:wrap;gap:10px}
.ro .rohd h1{font:700 24px/1.2 var(--sans);letter-spacing:-.02em}
.ro .rh-status{padding:3px 11px;border-radius:99px;font:650 12px var(--sans);background:color-mix(in srgb,var(--c-ind) 14%,transparent);color:var(--c-ind)}
.ro .rh-chips{display:flex;flex-wrap:wrap;gap:6px}
.ro .rh-chips span{padding:3px 10px;border-radius:99px;font:600 12px var(--sans);background:var(--surface-2);color:var(--ink-2)}
.ro .rh-chips span:nth-child(1){background:color-mix(in srgb,var(--c-ind) 10%,transparent);color:var(--c-ind)}
.ro .rh-chips span:nth-child(2){background:color-mix(in srgb,var(--c-vio) 10%,transparent);color:var(--c-vio)}
.ro .rh-chips span:nth-child(3){background:color-mix(in srgb,var(--c-emr) 12%,transparent);color:var(--c-emr)}
.ro .rohd .subl{margin:0;font-size:12.5px;color:var(--ink-3)}
.ro .rohd .hdacts{margin-top:22px}
.ro .rohd .pills{margin-top:22px}
.ro .rohd.late .pico,.ro .rohd.risk .pico{background:var(--accent)!important}
.ro .kpis{gap:12px}
.ro .kpi,.ro button.kpi{background:var(--surface)!important;box-shadow:0 0 0 1px var(--border)!important;border:0!important;border-radius:14px;padding:14px 16px}
.ro button.kpi:hover{box-shadow:0 0 0 1px var(--border-strong),0 10px 20px -12px rgba(20,26,40,.25)!important}
.ro .kpi .kic{width:42px;height:42px;border-radius:12px;background:color-mix(in srgb,var(--c) 15%,transparent);color:var(--c)}
.ro .kpi .kl{font:550 12px var(--sans);letter-spacing:0;text-transform:none;color:var(--ink-3)}
.ro .kpi .kv{font:700 18px/1.25 var(--sans);color:var(--c)}
.ro .kpi .ks{font-size:12px}
.ro .kpi.k-ind .kv,.ro .kpi.k-sky .kv,.ro .kpi.k-vio .kv{color:var(--ink)}
.ro .lcwrap{border:0;border-radius:14px;box-shadow:0 0 0 1px var(--border);padding:14px 16px 10px;margin-bottom:12px}
.ro .lchead{display:none}
.ro .stline{display:flex;margin:-4px 0 12px;padding:0 6px;background:none;border:0;font:500 12.5px var(--sans);color:var(--ink-2)}
.ro .body{padding-top:0}
.ro .cpmini{display:flex;flex-direction:column;gap:10px;padding-bottom:14px}
.ro .cpmini .cpn{display:flex;align-items:center;gap:10px}
.ro .cpmini .cpn i{display:grid;place-items:center;width:38px;height:38px;border-radius:50%;font:700 15px var(--sans);font-style:normal;background:color-mix(in srgb,var(--c-vio) 16%,transparent);color:var(--c-vio)}
.ro .cpmini .cpn b{font:650 14px var(--sans)}
.ro .cpmini .cpc{display:flex;flex-wrap:wrap;gap:8px}
.ro .cpmini .cpc span{padding:4px 10px;border-radius:99px;font-size:12px;background:var(--surface-2);color:var(--ink-2)}
.ro .cpmini .cpc b{color:var(--ink)}

/* ---- v11: the mockup's skin: flat white cards, tinted icon tiles, quiet headers ---- */
.ro .card,.ro .card.tint-ind,.ro .card.tint-vio,.ro .card.tint-sky,.ro .card.tint-emr,.ro .card.tint-amb,.ro .card.tint-red{border:0;border-top:0;border-radius:14px;background:var(--surface);box-shadow:0 0 0 1px var(--border)}
.ro .card.tint-ind>.ch,.ro .card.tint-vio>.ch,.ro .card.tint-sky>.ch,.ro .card.tint-emr>.ch,.ro .card.tint-amb>.ch,.ro .card.tint-red>.ch,.ro .card>.ch{background:none;border-bottom:0;padding:16px 18px 6px;gap:10px}
.ro .card>.ch h2{font:700 15px var(--sans);letter-spacing:-.01em}
.ro .glyph,.ro .glyph.g-ind,.ro .glyph.g-vio,.ro .glyph.g-sky,.ro .glyph.g-emr,.ro .glyph.g-amb,.ro .glyph.g-red,.ro .glyph.flow{width:34px;height:34px;border-radius:10px;background:color-mix(in srgb,var(--c) 14%,transparent);color:var(--c)}
.ro .glyph .ic,.ro .glyph svg{width:18px;height:18px}
.ro .card>.ch .lnk{margin-left:auto;padding:5px 12px;border-radius:9px;font:600 12.5px var(--sans);text-decoration:none;color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,transparent)}
.ro .card>.ch .lnk:hover{background:color-mix(in srgb,var(--accent) 16%,transparent)}
.ro .card>.cb{padding:4px 18px 18px}
.ro .rpane{gap:14px}
/* header: icon tile, title row, subline, actions */
.ro .rohd .pico{width:62px;height:62px;border-radius:18px;margin-top:20px}
.ro .rohd .pico svg{width:30px;height:30px}
.ro .rohd h1{font-size:26px}
.ro .rh-chips{align-items:center;gap:8px}
.ro .rh-chips span{font:500 13.5px var(--sans);color:var(--ink-2);padding:0;background:none}
.ro .rh-chips span:nth-child(-n+2)::after{content:"•";margin-left:8px;color:var(--ink-3)}
.ro .rh-chips span:nth-child(n+3){padding:3px 11px;border-radius:99px;font-size:12.5px;font-weight:600;background:color-mix(in srgb,var(--c-vio) 12%,transparent);color:var(--c-vio)}
.ro .rh-chips span:last-child:nth-child(n+3){background:color-mix(in srgb,var(--c-emr) 13%,transparent);color:var(--c-emr)}
.ro .rh-chips span:nth-child(3):last-child{background:color-mix(in srgb,var(--c-emr) 13%,transparent);color:var(--c-emr)}
.ro .rohd .hdacts .btn{height:38px;padding:0 14px;border-radius:10px;font-weight:600}
/* kpis */
.ro .kpi{padding:16px 18px;gap:14px}
.ro .kpi .kic{width:48px;height:48px;border-radius:14px}
.ro .kpi .kic svg{width:22px;height:22px}
.ro .kpi .kv{color:var(--ink)}
.ro .kpi.k-red .kv,.ro .kpi.k-amber .kv,.ro .kpi.k-green .kv{color:var(--c)}
.ro .kpi.k-red .ks{color:var(--c-red)}
.ro .kpi .kv{font-size:19px}
/* tracker card */
.ro .lcwrap{padding:18px 20px 12px;border-radius:14px}
.ro .lct-wrap.row .lct-btn{padding:9px 16px 8px}
.ro .lct-wrap.row .lct-n{width:32px;height:32px;font-size:13px}
.ro .lct-wrap.row .lct:not(:last-child)::after{top:22px;left:56px}
/* tabs */
.ro .rtabs{gap:6px;margin-bottom:14px}
.ro .rtabs button{padding:12px 14px;font-size:14px;font-weight:550}
.ro .rtabs i{font:500 13px var(--sans);padding:0;background:none;color:inherit;opacity:.85}
.ro .rtabs button[aria-selected="true"] i{background:none}
/* request details: label / value, no rules */
.ro .ov2 .kv>div{grid-template-columns:160px minmax(0,1fr);padding:11px 0}
.ro .ov2 .kv dt{font-size:13.5px}
.ro .ov2 .kv dd{font-size:14.5px;font-weight:500}

/* v12: closer to the mockup: the chips line, KPI values that fit, the approval card header */
.ro .rh-chips span:nth-child(-n+2)::after{content:none}
.ro .rh-chips span:nth-child(-n+2):not(:last-child)::after{content:"•";margin-left:8px;color:var(--ink-3)}
.ro .kpis{gap:12px}
.ro .kpi{padding:14px;gap:12px;min-width:0}
.ro .kpi .kic{width:42px;height:42px;border-radius:12px;flex:none}
.ro .kpi .ktx{min-width:0}
.ro .kpi .kv{font-size:17px;white-space:normal;overflow-wrap:anywhere;line-height:1.25}
.ro .kpi .ks{white-space:normal;overflow-wrap:anywhere}
.ro .apc>.ch{align-items:flex-start;flex-wrap:wrap;row-gap:6px}
.ro .apc .hd{flex:1;min-width:0}
.ro .apc>.ch .cnt{order:3;flex:1 0 100%;margin:0;width:fit-content;padding:4px 11px;border-radius:99px;background:color-mix(in srgb,var(--accent) 10%,transparent);color:var(--accent);font:650 12px var(--sans)}
.ro .decision>.card,.ro .decision>*{border:0;border-top:0;box-shadow:0 0 0 1px var(--border),0 8px 22px -16px rgba(20,26,40,.3)}
.ro .decision>.card>.ch{background:none;border-bottom:0}
.ro .decision>.card>.ch h2{color:var(--ink)}
.ro .decision>.card>.ch h2::after{content:none!important}
.ro .decision>.card>.ch{flex-wrap:wrap;row-gap:4px}
.ro .decision>.card>.ch h2{flex:1 1 auto}
.ro .decision>.card>.ch .cnt{order:3;flex:1 0 100%;margin:0;padding:0;font:500 12px var(--sans);color:var(--ink-3);background:none}
.ro .decision>.card>.ch .dnext{margin-left:auto}
.ro .decision>.card.apc>.ch .cnt{width:fit-content;padding:4px 11px;border-radius:99px;background:color-mix(in srgb,var(--accent) 10%,transparent);color:var(--accent);font:650 12px var(--sans)}

/* ---- v13: compact. 13px base, tight cards, small controls, like the mockup ---- */
.ro{font-size:13px}
.ro .body{grid-template-columns:minmax(0,1fr) 320px;gap:14px}
.ro .col,.ro .col.side{gap:12px}
.ro .rpane{gap:12px}
/* header */
.ro .rohd{gap:12px;margin-bottom:10px}
.ro .rohd .pico{width:48px;height:48px;border-radius:14px;margin-top:18px}
.ro .rohd .pico svg{width:24px;height:24px}
.ro .rohd h1{font-size:21px}
.ro .rohd .crumb{font-size:12px;margin-bottom:2px}
.ro .rh-status{padding:2px 9px;font-size:11.5px}
.ro .rh-chips span{font-size:12.5px}
.ro .rh-chips span:nth-child(n+3){padding:2px 9px;font-size:11.5px}
.ro .rohd .subl{font-size:12px}
.ro .rohd .hdacts{margin-top:16px;gap:6px}
.ro .rohd .hdacts .btn{height:32px;padding:0 11px;font-size:12.5px;border-radius:9px}
.ro .rohd .hdacts .btn .ic{width:14px;height:14px}
.ro .rohd .pills{margin-top:16px}
/* kpis */
.ro .kpis{gap:10px;margin-bottom:10px}
.ro .kpi,.ro button.kpi{padding:10px 12px;gap:10px;border-radius:12px}
.ro .kpi .kic{width:36px;height:36px;border-radius:10px}
.ro .kpi .kic svg{width:18px;height:18px}
.ro .kpi .kl{font-size:11.5px}
.ro .kpi .kv{font-size:15px;line-height:1.2}
.ro .kpi .ks{font-size:11.5px}
/* tracker */
.ro .lcwrap{padding:12px 16px 8px;margin-bottom:8px}
.ro .lct-wrap.row .lct-btn{padding:9px 16px 8px;gap:10px}
.ro .lct-wrap.row .lct-n{width:28px;height:28px;font-size:12px}
.ro .lct-wrap.row .lct:not(:last-child)::after{top:18px;left:46px}
.ro .lct-wrap.row .lct-tx b{font-size:12.5px}
.ro .lct-wrap.row .lct-tx small{font-size:11px}
.ro .stline{font-size:12px;margin:-2px 0 8px}
/* tabs */
.ro .rtabs{margin-bottom:12px}
.ro .rtabs button{padding:9px 12px;font-size:13px}
/* cards */
.ro .card>.ch{padding:12px 16px 4px;gap:8px}
.ro .card>.ch h2{font-size:14px}
.ro .glyph,.ro .glyph.g-ind,.ro .glyph.g-vio,.ro .glyph.g-sky,.ro .glyph.g-emr,.ro .glyph.g-amb,.ro .glyph.g-red,.ro .glyph.flow{width:28px;height:28px;border-radius:8px}
.ro .glyph .ic,.ro .glyph svg{width:15px;height:15px}
.ro .card>.cb{padding:2px 16px 14px}
.ro .card>.ch .lnk{padding:4px 10px;font-size:12px;border-radius:8px}
.ro .hint{font-size:12px;margin:0 0 6px}
/* overview sheet */
.ro .ov2{gap:12px}
.ro .ov2-stack{gap:12px}
.ro .ov2 .kv{padding:2px 16px 10px}
.ro .ov2 .kv>div{grid-template-columns:130px minmax(0,1fr);padding:7px 0;gap:10px}
.ro .ov2 .kv dt{font-size:12.5px}
.ro .ov2 .kv dd{font-size:13px}
.ro .ov2 .pv{margin:0 16px 12px;padding:9px}
.ro .ov2 .la,.ro .ov2 .kd{padding:0 16px 10px}
.ro .ov2 .la li{padding:6px 0;font-size:12.5px;grid-template-columns:24px minmax(0,1fr) auto}
.ro .ov2 .la i{width:24px;height:24px}
.ro .ov2 .kd li{padding:6px 0;font-size:12.5px}
/* people involved: a proper list */
.ro .person{padding:7px 0;gap:10px;border-bottom:0}
.ro .person .av{width:30px;height:30px;font-size:11px}
.ro .person .n{font-size:13px;font-weight:600;line-height:1.25}
.ro .person .r{display:inline-block;margin-top:1px;padding:0 7px;border-radius:99px;font-size:10.5px;font-weight:650;background:var(--surface-2);color:var(--ink-3)}
.ro .person.own .r{background:color-mix(in srgb,var(--c-ind) 12%,transparent);color:var(--c-ind)}
.ro .person.req .r{background:color-mix(in srgb,var(--c-vio) 12%,transparent);color:var(--c-vio)}
.ro .person.stp .r{background:color-mix(in srgb,var(--c-amb) 14%,transparent);color:var(--c-amb)}
.ro .person.dep .r{background:color-mix(in srgb,var(--c-emr) 13%,transparent);color:var(--c-emr)}
.ro .reassign{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;margin-top:8px;padding:10px;border:0;border-radius:10px;background:var(--inset)}
.ro .reassign .rlabel{grid-column:1/-1;font-size:11.5px}
.ro .reassign .sel,.ro .reassign select{height:32px;min-width:0;font-size:12.5px;border-radius:8px}
.ro .reassign .btn.wide{width:auto;height:32px;padding:0 12px;font-size:12.5px;border-radius:8px;white-space:nowrap}
.ro .reassign .btn.wide:not(:disabled){background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
/* buttons everywhere on the page */
.ro .btn{height:32px;padding:0 12px;font-size:12.5px;border-radius:9px;font-weight:600}
.ro .btn.sm{height:28px;padding:0 10px;font-size:12px}
.ro .btn.wide{height:34px}
/* decision */
.ro .decide{padding:10px;margin-top:6px}
.ro .decide .dhead{margin-bottom:8px;gap:9px}
.ro .decide .dtile{width:30px;height:30px;border-radius:9px}
.ro .decide .dtile .ic{width:16px;height:16px}
.ro .decide .dtx b{font-size:13px}
.ro .decide .dtx span{font-size:12px}
.ro .decide .dprompt{font-size:12.5px;margin-bottom:8px}
.ro .decide .dbtns .btn{min-height:34px;height:34px;font-size:12.5px}
/* the cards below: documents, counterparty, ask */
.ro .cpmini{gap:8px;padding-bottom:12px}
.ro .cpmini .cpn i{width:32px;height:32px;font-size:13px}
.ro .cpmini .cpn b{font-size:13px}
.ro .cpmini .cpc span{padding:2px 9px;font-size:11.5px}
.ro .cpmini .lnk{align-self:flex-start;padding:5px 11px;border-radius:8px;font:600 12px var(--sans);color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,transparent)}
.ro .cpmini .lnk:hover{background:color-mix(in srgb,var(--accent) 16%,transparent)}
.ro .docrow{padding:7px 0}
.ro .cin,.ro .composer input,.ro input.cin{height:32px;font-size:12.5px;border-radius:8px}
@media (max-width:1180px){.ro .body{grid-template-columns:minmax(0,1fr)}}

/* ---- v14: fixes from a real screenshot: label collision, decision card overflow ---- */
.ro .ov2 .kv>div{grid-template-columns:150px minmax(0,1fr)}
.ro .ov2 .kv dt{overflow-wrap:anywhere;line-height:1.3}
.ro .decide,.ro .decide.warn,.ro .decide.ok{padding:0;margin-top:4px;background:none;border:0}
.ro .decide .dprompt{margin:0 0 8px;font-size:12.5px;line-height:1.45}
.ro .decide .dhead{align-items:flex-start;margin:0 0 10px;padding:9px 10px;border-radius:10px;background:color-mix(in srgb,var(--c-amb) 10%,var(--surface));gap:9px}
.ro .decide.ok .dhead{background:color-mix(in srgb,var(--c-emr) 10%,var(--surface))}
.ro .decide .dtile{width:26px;height:26px;border-radius:8px}
.ro .decide .dtile .ic{width:14px;height:14px}
.ro .decide .dtx{gap:2px}
.ro .decide .dtx b{font-size:12.5px}
.ro .decide .dtx span{font-size:11.5px;line-height:1.4;color:var(--ink-2)}
.ro .decide .dbtns{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.ro .decide .dbtns .btn{min-width:0;padding:0 6px;gap:4px;overflow:hidden;text-overflow:ellipsis;font-size:12.5px}
.ro .decide .dbtns .btn:not(.pri) svg{display:none}
.ro .decide .dnote{margin:8px 0 0;text-align:left;font-size:11.5px;color:var(--ink-3)}
.ro .decide .dcomment{margin-top:6px}
.ro .decision>.card>.ch .cnt{font-size:11.5px}
.ro .reassign{grid-template-columns:1fr}
.ro .reassign .btn.wide{justify-self:stretch;width:100%}

/* ---- v15: the Workflow tab: filter bar, stage groups, step cards, motion ---- */
.ro .wf2-bar{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin:8px 0 12px}
.ro .wf2-seg{display:inline-flex;gap:2px;padding:3px;border-radius:10px;background:var(--surface-2)}
.ro .wf2-seg button{display:inline-flex;align-items:center;gap:6px;padding:5px 10px;border-radius:8px;font:600 12px var(--sans);color:var(--ink-3);transition:background .15s,color .15s,box-shadow .15s}
.ro .wf2-seg button:hover:not(:disabled){color:var(--ink)}
.ro .wf2-seg button:disabled{opacity:.45;cursor:default}
.ro .wf2-seg button[aria-pressed="true"]{background:var(--surface);color:var(--ink);box-shadow:0 1px 2px rgba(20,26,40,.12)}
.ro .wf2-seg i{font-style:normal;font-size:11px;padding:0 6px;border-radius:99px;background:color-mix(in srgb,var(--ink-3) 14%,transparent)}
.ro .wf2-tools{display:flex;gap:6px}
.ro .wf2-btn{display:inline-flex;align-items:center;gap:6px;height:30px;padding:0 11px;border-radius:9px;font:600 12px var(--sans);color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,transparent);transition:background .15s}
.ro .wf2-btn:hover{background:color-mix(in srgb,var(--accent) 16%,transparent)}
.ro .wf2-btn svg{width:14px;height:14px}
.ro .wft{display:flex;flex-direction:column;gap:14px;margin:0}
.ro .wf2-stage{display:flex;flex-direction:column}
.ro .wft .wsg{display:flex;align-items:center;gap:10px;width:100%;margin:0;padding:6px 4px;border:0;border-bottom:0;background:none;text-align:left;font:700 11px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-2);border-radius:8px;cursor:pointer}
.ro .wft .wsg:hover{background:var(--surface-2)}
.ro .wft .wsg:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.ro .wf2-chev{width:15px;height:15px;color:var(--ink-3);transition:transform .2s}
.ro .wft .wsg[aria-expanded="false"] .wf2-chev{transform:rotate(-90deg)}
.ro .wf2-sp{flex:none;width:72px;height:5px;border-radius:99px;background:var(--surface-2);overflow:hidden}
.ro .wf2-sp i{display:block;height:100%;border-radius:99px;background:var(--good);transform-origin:left;animation:wf-fill .7s cubic-bezier(.2,.9,.25,1) .15s backwards}
@keyframes wf-fill{from{transform:scaleX(0)}to{transform:none}}
.ro .wft .wsg em{margin-left:auto;font-style:normal;font-weight:600;letter-spacing:0;text-transform:none;color:var(--ink-3)}
.ro .wf2-stage[data-state="done"] .wsg em{color:var(--good)}.ro .wf2-stage[data-state="cur"] .wsg em{color:var(--accent)}
.ro .wf2-list{display:flex;flex-direction:column;gap:8px;margin-top:6px}
.ro .wft .t,.ro .wft .t.cur,.ro .wft .t.warn{position:relative;display:grid;grid-template-columns:28px minmax(0,1fr) auto;column-gap:12px;row-gap:2px;align-items:start;margin:0;padding:12px 14px;border:0;border-radius:12px;background:var(--surface);box-shadow:0 0 0 1px var(--border);transition:box-shadow .18s,transform .18s;animation:wf-rise .4s cubic-bezier(.2,.9,.25,1) backwards;animation-delay:calc(var(--i,0) * 55ms)}
.ro .wft .t:hover{box-shadow:0 0 0 1px var(--border-strong),0 8px 18px -14px rgba(20,26,40,.35);transform:translateY(-1px)}
.ro .wft .t.cur{background:color-mix(in srgb,var(--accent) 5%,var(--surface));box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 30%,var(--border))}
.ro .wft .t.warn{background:color-mix(in srgb,var(--c-amb) 7%,var(--surface));box-shadow:0 0 0 1px color-mix(in srgb,var(--c-amb) 35%,var(--border))}
.ro .wft .t.skip,.ro .wft .t.todo{background:var(--surface)}
.ro .wft .t.skip{background:transparent;box-shadow:0 0 0 1px var(--border);opacity:.8}
@keyframes wf-rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.ro .wft .tnode{width:28px;height:28px;margin:0;font-size:12px}
.ro .wft .t.cur .tnode{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);animation:wf-pulse 2s ease-out infinite}
@keyframes wf-pulse{0%{box-shadow:0 0 0 0 color-mix(in srgb,var(--accent) 45%,transparent)}100%{box-shadow:0 0 0 9px transparent}}
.ro .wft .t.warn .tnode{animation:wf-pulse 2s ease-out infinite;--accent:var(--c-amb)}
.ro .wft .t::after{padding:3px 10px;font-size:11px}
.ro .wft .t.cur::after{background:var(--accent);color:var(--accent-ink)}
.ro .wft .t b{font-size:13.5px}
.ro .wft .tmeta{margin-top:5px;gap:5px}
.ro .wft .tchip{padding:2px 9px;font-size:11px}
.ro .wft .t .ministep,.ro .wft .t .doorway{display:inline-flex;align-items:center;justify-content:center;height:32px;margin-top:10px;padding:0 14px;border-radius:9px;font:650 12.5px var(--sans);text-decoration:none;transition:filter .15s,transform .15s}
.ro .wft .t .ministep:hover{filter:brightness(1.1);transform:translateY(-1px)}
.ro .wft .t.flash{animation:card-flash 1.5s ease-out}
@media (prefers-reduced-motion:reduce){.ro .wft .t.cur .tnode,.ro .wft .t.warn .tnode{animation-duration:2s!important;animation-iteration-count:infinite!important}}
`;

export const TIMELINE_CSS = `
.ro .body{grid-template-columns:272px minmax(0,1fr) 320px}
.ro .tlcard{position:sticky;top:0;align-self:start;display:flex;flex-direction:column;min-height:0;max-height:calc(100vh - 150px);padding:14px 10px 10px 14px;background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:0 1px 2px rgba(20,26,40,.04)}
.ro .tlhead{display:flex;flex-direction:column;gap:2px;padding-right:6px}
.ro .tlhead b{font-size:14px;font-weight:700;letter-spacing:-.01em}.ro .tlhead span{font-size:11.5px;color:var(--ink-3)}
.ro .tlbar{height:4px;border-radius:4px;background:var(--surface-2);margin:10px 6px 10px 0;overflow:hidden}
.ro .tlbar i{display:block;height:100%;border-radius:4px;background:linear-gradient(90deg,var(--good,#16a34a),var(--accent));transition:width .5s cubic-bezier(.2,.9,.25,1)}
.ro .rtabs{overflow-x:auto;scrollbar-width:none}.ro .rtabs>button{flex:none;white-space:nowrap}
.ro .tlscroll{flex:1;min-height:0;overflow-y:auto;padding:8px 8px 6px 10px;margin:0 -6px 0 -10px;scrollbar-width:thin}
@media (max-width:1360px){.ro .body{grid-template-columns:240px minmax(0,1fr) 300px}}
@media (max-width:1180px){.ro .body{grid-template-columns:minmax(0,1fr)}.ro .tlcard{position:static;max-height:360px}}

/* ---- Overview: full width, field tiles, three cards side by side ---- */
.ro .ov2{display:flex;flex-direction:column;align-items:stretch;gap:14px}
.ro .ov2-stack{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px}
.ro .ov2 .card{border-radius:14px;box-shadow:0 1px 3px rgba(20,26,40,.06);transition:transform .18s,box-shadow .18s}
.ro .ov2 .card:hover{transform:translateY(-2px);box-shadow:0 10px 22px -12px rgba(20,26,40,.28)}
.ro .ov2 .kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px;padding:14px}
.ro .ov2 .kv>div{display:flex;flex-direction:column;gap:3px;padding:10px 12px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);transition:border-color .15s,background .15s}
.ro .ov2 .kv>div:hover{border-color:color-mix(in srgb,var(--accent) 45%,var(--border));background:var(--surface)}
.ro .ov2 .kv dt{font-size:10.5px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .ov2 .kv dd{font-size:13.5px}
.ro .ov2 .la li,.ro .ov2 .kd li{padding:9px 10px;margin:0 0 6px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2)}
.ro .ov2 .la,.ro .ov2 .kd{padding:4px 12px 12px}
.ro .ov2 .kd li[data-tone=done]{background:color-mix(in srgb,var(--good) 9%,var(--surface));border-color:color-mix(in srgb,var(--good) 28%,var(--border))}
.ro .ov2 .kd li[data-tone=late]{background:color-mix(in srgb,var(--crit) 8%,var(--surface));border-color:color-mix(in srgb,var(--crit) 28%,var(--border))}
.ro .ov2 .kd li[data-tone=warn]{background:color-mix(in srgb,var(--c-amb) 10%,var(--surface));border-color:color-mix(in srgb,var(--c-amb) 30%,var(--border))}

/* ---- Workflow tab: a process timeline. Time on the left, nodes on one line, each step a small card; the live step carries an info box. ---- */
.ro .wf2-stage,.ro .wf2-stage[data-state]{border:0;border-radius:0;background:none;box-shadow:none;overflow:visible;margin:0 0 22px}
.ro .wf2-stage[data-state=skip]{opacity:.7}
.ro .wf2-stage>.wsg{display:flex;align-items:center;gap:12px;width:100%;padding:0 0 12px;margin:0 0 6px;border:0;border-bottom:1px dashed var(--border-strong);border-radius:0;background:none}
.ro .wf2-stage>.wsg>span:first-of-type{display:inline-flex;align-items:center;padding:4px 12px;border-radius:8px;font:700 12.5px var(--sans);letter-spacing:.01em;background:var(--surface-2);color:var(--ink-2)}
.ro .wf2-stage[data-state=done]>.wsg>span:first-of-type{background:color-mix(in srgb,var(--good) 14%,var(--surface));color:var(--good)}
.ro .wf2-stage[data-state=cur]>.wsg>span:first-of-type{background:color-mix(in srgb,var(--accent) 14%,var(--surface));color:var(--accent)}
.ro .wf2-list{display:flex;flex-direction:column;gap:0;padding:0}
.ro .wft .t,.ro .wft .t.cur,.ro .wft .t.warn,.ro .wft .t.done,.ro .wft .t.skip,.ro .wft .t.todo{display:grid;grid-template-columns:62px 28px minmax(0,1fr) auto;column-gap:14px;align-items:start;position:relative;margin:0;padding:6px 0 22px;border:0;border-radius:0;background:none;box-shadow:none;transform:none}
.ro .wft .t>.ttime{grid-column:1;grid-row:1;padding-top:6px;text-align:right;font-size:11.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.ro .wft .t>.tnode{grid-column:2;grid-row:1;position:static;margin:0;z-index:1}
.ro .wft .t>.tbody{grid-column:3;grid-row:1;min-width:0;display:flex;flex-direction:column;gap:6px;padding-top:3px}
.ro .wft .t::after{grid-column:4;grid-row:1;align-self:start;margin-top:3px}
.ro .wft .t::before{content:"";display:block;position:absolute;left:89px;top:36px;bottom:-4px;width:2px;border-radius:2px;background:repeating-linear-gradient(to bottom,var(--border-strong) 0 4px,transparent 4px 8px)}
.ro .wft .t.done::before{background:var(--good)}
.ro .wf2-list>.t:last-child{padding-bottom:4px}.ro .wf2-list>.t:last-child::before{display:none}
.ro .wft .t:hover{background:none;box-shadow:none}
.ro .wft .t .tbody>b{font:650 14px/1.3 var(--sans);color:var(--ink)}
.ro .wft .t .tmeta{display:inline-flex;flex-wrap:wrap;gap:6px;align-self:flex-start;margin:0;padding:8px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.05);transition:border-color .15s,box-shadow .15s}
.ro .wft .t:hover .tmeta{border-color:color-mix(in srgb,var(--accent) 40%,var(--border));box-shadow:0 6px 14px -10px rgba(20,26,40,.3)}
.ro .wft .t .tinfo{display:flex;flex-direction:column;gap:3px;padding:10px 12px;border-radius:12px;background:var(--surface-2)}
.ro .wft .t.cur .tinfo{background:color-mix(in srgb,var(--accent) 8%,var(--surface));box-shadow:inset 3px 0 0 var(--accent)}
.ro .wft .t.warn .tinfo{background:color-mix(in srgb,var(--c-amb) 10%,var(--surface));box-shadow:inset 3px 0 0 var(--c-amb)}
.ro .wft .t .tinfo small{color:var(--ink-2)}
.ro .wft .t.skip{opacity:.75}
.ro .wft .t.cur .tbody>b{color:var(--accent)}

/* ---- Documents: a card per file in a grid, then the actions as a footer panel ---- */
.ro .dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px;margin-top:4px}
.ro .dcard{--dk:var(--c-sky);display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid var(--border);border-radius:14px;background:linear-gradient(160deg,color-mix(in srgb,var(--dk) 9%,var(--surface)),var(--surface) 60%);box-shadow:0 1px 3px rgba(20,26,40,.06);transition:transform .18s,box-shadow .18s,border-color .18s}
.ro .dcard[data-kind=pdf]{--dk:var(--crit)}.ro .dcard[data-kind=doc]{--dk:var(--accent)}
.ro .dcard:hover{transform:translateY(-3px);border-color:color-mix(in srgb,var(--dk) 45%,var(--border));box-shadow:0 14px 24px -14px rgba(20,26,40,.35)}
.ro .dtop{display:flex;align-items:center;justify-content:space-between;gap:8px}
.ro .dicon{position:relative;display:grid;place-items:center;width:44px;height:50px;border-radius:12px;background:color-mix(in srgb,var(--dk) 14%,var(--surface));color:var(--dk);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--dk) 26%,transparent)}
.ro .dicon svg{width:22px;height:22px;margin-top:-8px}
.ro .dicon b{position:absolute;bottom:5px;font:800 8.5px var(--sans);letter-spacing:.06em}
.ro .dst{display:inline-flex;align-items:center;gap:4px;padding:3px 9px;border-radius:99px;font-size:11px;font-weight:650;background:var(--surface-2);color:var(--ink-3);box-shadow:inset 0 0 0 1px var(--border)}
.ro .dst.ok{background:color-mix(in srgb,var(--good) 14%,var(--surface));color:var(--good);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--good) 30%,transparent)}
.ro .dst svg{width:12px;height:12px}
.ro .dname{font:650 13.5px var(--sans);color:var(--ink);line-height:1.35;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.ro .dmeta{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto;font-size:11.5px;color:var(--ink-3)}
.ro .dmeta span{padding:2px 8px;border-radius:7px;background:color-mix(in srgb,var(--ink) 5%,transparent)}
.ro .dempty{display:flex;flex-direction:column;align-items:center;gap:8px;padding:26px 16px;border:1.5px dashed var(--border-strong);border-radius:14px;background:var(--surface-2);text-align:center}
.ro .dempty svg{width:28px;height:28px;color:var(--ink-3)}
.ro .docacts{display:flex;flex-direction:column;gap:8px;margin-top:14px;padding:12px;border:1px solid var(--border);border-radius:14px;background:var(--surface-2)}
.ro .docacts .btn{justify-content:center;min-height:40px;padding:0 16px;border-radius:10px;font-size:13px;font-weight:600;text-align:center}
.ro .docacts .btn svg{display:none}
.ro .docacts .btn:not(.pri){background:var(--surface);border:1px solid var(--border-strong);color:var(--ink)}
.ro .docacts .btn:not(.pri):hover{border-color:var(--accent);color:var(--accent)}

/* ---- AI risk review: a headline card, then one card per severity ---- */
.ro .risktop{display:flex;align-items:center;gap:14px;padding:14px;margin-bottom:12px;border:1px solid var(--border);border-radius:14px;background:linear-gradient(135deg,color-mix(in srgb,var(--warn) 9%,var(--surface)),var(--surface))}
.ro .rgauge{width:54px;height:54px;border-radius:16px;font-size:24px;box-shadow:0 6px 14px -6px rgba(20,26,40,.35)}
.ro .rmeta .rb{font-size:16px}
.ro .sevgroup{margin:0 0 12px;border:1px solid var(--border);border-radius:14px;overflow:hidden;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.05)}
.ro .sevgroup>.sevhead{display:flex;align-items:center;justify-content:space-between;width:100%;padding:11px 14px;border:0;border-bottom:1px solid var(--border);background:var(--surface-2);cursor:pointer}
.ro .sevgroup>.sevhead:hover{background:color-mix(in srgb,var(--accent) 6%,var(--surface-2))}
.ro .sevgroup .risk{margin:10px 12px;padding:12px 14px;border-left-width:4px;border-radius:12px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.05);transition:transform .15s,box-shadow .15s}
.ro .sevgroup .risk:hover{transform:translateY(-1px);box-shadow:0 8px 16px -10px rgba(20,26,40,.3)}
.ro .risk.crit,.ro .risk.high{background:color-mix(in srgb,var(--crit) 5%,var(--surface))}
.ro .risk.med{background:color-mix(in srgb,var(--warn) 6%,var(--surface))}
.ro .risk.low{border-left-color:var(--good);background:color-mix(in srgb,var(--good) 5%,var(--surface))}
.ro .risk .rfix{margin-top:8px;padding:8px 10px;border-radius:9px;background:color-mix(in srgb,var(--good) 10%,var(--surface));color:var(--ink-2)}

/* ---- AI analysis: the reading, the tiles, the confidence line, each its own card ---- */
.ro .airead{padding:16px;border-radius:16px;background:linear-gradient(135deg,color-mix(in srgb,var(--ai) 9%,var(--surface)),var(--surface));box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--ai) 28%,var(--border)),0 1px 3px rgba(20,26,40,.05)}
.ro .aitile{width:34px;height:34px;border-radius:11px;box-shadow:0 6px 14px -6px color-mix(in srgb,var(--ai) 70%,transparent)}
.ro .aisum{padding:12px 14px;border-radius:12px;background:var(--surface);border:1px solid var(--border)}
.ro .rlgrid{gap:12px}
.ro .rl{padding:12px 14px;border:1px solid var(--border);border-radius:12px;box-shadow:inset 4px 0 0 var(--t),0 1px 2px rgba(20,26,40,.05);transition:transform .15s,box-shadow .15s}
.ro .rl:hover{transform:translateY(-2px);box-shadow:inset 4px 0 0 var(--t),0 10px 20px -12px rgba(20,26,40,.3)}
.ro .confline{padding:11px 14px;border:1px solid var(--border);border-radius:12px;background:var(--surface-2)}
@media (prefers-reduced-motion:reduce){.ro .ov2 .card,.ro .wft .t,.ro .dcard,.ro .sevgroup .risk,.ro .rl{transition-duration:.15s!important}}

/* ---- Comments: a chat. Other people's messages on the left with an avatar, yours on the right, the box below like a messenger. ---- */
.ro .wfcomments .clist{margin:0 0 12px;padding:14px;gap:12px;max-height:440px;overflow-y:auto;border:1px solid var(--border);border-radius:16px;background:linear-gradient(180deg,var(--surface-2),color-mix(in srgb,var(--surface-2) 70%,var(--surface)));scrollbar-width:thin}
.ro .wfcomments .clist li{display:flex;gap:9px;align-items:flex-end;max-width:100%;animation:wf-msg .3s cubic-bezier(.2,.9,.25,1) backwards}
.ro .wfcomments .cav{width:30px;height:30px;font-size:11px;font-weight:700;margin-bottom:2px}
.ro .wfcomments .cbody{flex:0 1 auto;max-width:78%;padding:9px 13px 8px;border-radius:14px 14px 14px 4px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.1)}
.ro .wfcomments .cmeta{display:flex;flex-wrap:wrap;gap:2px 6px;align-items:baseline;margin:0 0 3px;font-size:11px}
.ro .wfcomments .cmeta b{font-size:12px;color:var(--accent)}
.ro .wfcomments .cmeta .dim{font-size:10.5px}
.ro .wfcomments .ctext{font-size:13px;line-height:1.5}
.ro .wfcomments .clist li[data-mine="1"]{flex-direction:row-reverse}
.ro .wfcomments .clist li[data-mine="1"] .cav{display:none}
.ro .wfcomments .clist li[data-mine="1"] .cbody{margin-left:auto;border-radius:14px 14px 4px 14px;background:color-mix(in srgb,var(--accent) 14%,var(--surface))}
.ro .wfcomments .clist li[data-mine="1"] .cmeta b{display:none}
.ro .wfcomments .clist li.ret .cbody{background:color-mix(in srgb,var(--c-amb) 14%,var(--surface));box-shadow:inset 3px 0 0 var(--c-amb),0 1px 2px rgba(20,26,40,.1)}
.ro .wfcomments .cret{padding:1px 8px;border-radius:99px;background:color-mix(in srgb,var(--c-amb) 22%,transparent);color:var(--c-amb);font-size:10.5px;font-weight:700}
.ro .wfcomments .composer{gap:8px;padding:6px 6px 6px 16px;border-radius:99px;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.08)}
.ro .wfcomments .composer .btn{height:36px;padding:0 16px;border-radius:99px}
.ro .wfcomments .emptynote{padding:30px 16px;border:1.5px dashed var(--border-strong);border-radius:16px;background:var(--surface-2)}
@keyframes wf-msg{from{opacity:0;transform:translateY(8px) scale(.97)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.ro .wfcomments .clist li{animation-duration:.3s!important}}

/* ---- Section tabs: one full-width pill bar above the three columns ---- */
.ro .rtabswrap{margin:0 0 14px}
.ro .rtabswrap .rtabs{display:flex;gap:4px;padding:5px;margin:0;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.06);overflow-x:auto;scrollbar-width:none}
.ro .rtabswrap .rtabs::-webkit-scrollbar{display:none}
.ro .rtabswrap .rtabs>button{flex:1 0 auto;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:40px;padding:0 16px;margin:0;border:0;border-radius:10px;background:transparent;color:var(--ink-2);font:600 13px var(--sans);white-space:nowrap;cursor:pointer;position:relative;transition:background .15s,color .15s,transform .15s}
.ro .rtabswrap .rtabs>button .ic{width:16px;height:16px;flex:none;color:var(--ink-3);transition:color .15s}
.ro .rtabswrap .rtabs>button:hover{background:var(--surface-2);color:var(--ink)}
.ro .rtabswrap .rtabs>button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.ro .rtabswrap .rtabs>button[aria-selected=true]{background:color-mix(in srgb,var(--accent) 12%,var(--surface));color:var(--accent);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 30%,transparent)}
.ro .rtabswrap .rtabs>button[aria-selected=true] .ic{color:var(--accent)}
.ro .rtabswrap .rtabs>button i{font-style:normal;min-width:20px;padding:1px 7px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);font:700 11px var(--sans);box-shadow:inset 0 0 0 1px var(--border)}
.ro .rtabswrap .rtabs>button[aria-selected=true] i{background:var(--accent);color:var(--accent-ink);box-shadow:none}
@media (max-width:760px){.ro .rtabswrap .rtabs>button{padding:0 12px}}

/* ---- Workflow tab: the same look as the Progress column: a line of dots on the left, a rounded card per step on the right ---- */
.ro .wft .t,.ro .wft .t.cur,.ro .wft .t.warn,.ro .wft .t.done,.ro .wft .t.skip,.ro .wft .t.todo{grid-template-columns:28px minmax(0,1fr);column-gap:14px;align-items:start;padding:0 0 12px;background:none;box-shadow:none}
.ro .wft .t>.tnode{grid-column:1;grid-row:1;margin-top:10px}
.ro .wft .t>.tbody{grid-column:2;grid-row:1;display:flex;flex-direction:column;gap:7px;padding:12px 14px;border:1px solid var(--border);border-radius:14px;background:var(--surface-2);box-shadow:none;transition:transform .18s,box-shadow .18s,border-color .18s}
.ro .wft .t::after{display:none}
.ro .wft .t::before{left:13px;top:38px;bottom:-10px}
.ro .wft .t:hover>.tbody{transform:translateY(-1px);box-shadow:0 8px 18px -12px rgba(20,26,40,.3);border-color:color-mix(in srgb,var(--accent) 40%,var(--border))}
.ro .wft .t .tbody>b{font:700 14.5px/1.3 var(--sans);color:var(--ink)}
.ro .wft .t .tsub{font-size:12px;font-weight:550;color:var(--ink-3)}
.ro .wft .t.done>.tbody{background:color-mix(in srgb,var(--good) 8%,var(--surface));border-color:color-mix(in srgb,var(--good) 32%,var(--border))}
.ro .wft .t.done .tsub{color:var(--good)}
.ro .wft .t.cur>.tbody{background:color-mix(in srgb,var(--accent) 7%,var(--surface));border-color:color-mix(in srgb,var(--accent) 55%,var(--border));box-shadow:0 0 0 2px color-mix(in srgb,var(--accent) 20%,transparent)}
.ro .wft .t.cur .tsub{color:var(--accent)}
.ro .wft .t.cur::before{background:repeating-linear-gradient(to bottom,var(--accent) 0 4px,transparent 4px 8px)}
.ro .wft .t.warn>.tbody{background:color-mix(in srgb,var(--c-amb) 9%,var(--surface));border-color:color-mix(in srgb,var(--c-amb) 45%,var(--border))}
.ro .wft .t.warn .tsub{color:var(--c-amb)}
.ro .wft .t.todo .tbody>b,.ro .wft .t.skip .tbody>b{color:var(--ink-3)}
.ro .wft .t.skip>.tbody{opacity:.8}
.ro .wft .t .tmeta{border:0;padding:0;box-shadow:none;background:none}
.ro .wft .t .tinfo{background:var(--surface)}

/* The live step's note, as a chat message: avatar, who it is with, the message, the time it is due as the stamp. */
.ro .wft .t .tinfo.tmsg{display:flex;flex-direction:row;align-items:flex-start;gap:9px;padding:0;background:none;box-shadow:none;border-radius:0}
.ro .wft .t.cur .tinfo,.ro .wft .t.warn .tinfo{background:none;box-shadow:none}
.ro .wft .t .tmav{flex:none;display:grid;place-items:center;width:30px;height:30px;border-radius:50%;font:700 11px var(--sans);color:#fff;background:var(--c-vio)}
.ro .wft .t.warn .tmav{background:var(--c-amb)}
.ro .wft .t .tbub{position:relative;flex:1;min-width:0;display:flex;flex-direction:column;gap:3px;padding:9px 12px 8px;border:1px solid var(--border);border-radius:4px 14px 14px 14px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.1)}
.ro .wft .t .tbub::before{content:"";position:absolute;left:-6px;top:9px;width:10px;height:10px;background:var(--surface);border:1px solid var(--border);border-right:0;border-top:0;transform:rotate(45deg)}
.ro .wft .t .tbub .tmname{font:700 12px var(--sans);color:var(--c-vio);overflow-wrap:anywhere}
.ro .wft .t.warn .tbub .tmname{color:var(--c-amb)}
.ro .wft .t .tbub .tnote{margin:0;font-style:normal;font-size:12.5px;line-height:1.5;color:var(--ink)}
.ro .wft .t .tbub .tmdue{align-self:flex-end;margin-top:2px;font-size:10.5px;color:var(--ink-3);font-variant-numeric:tabular-nums}
.ro .wft .t .tbub .tmdue.over{color:var(--crit);font-weight:650}

/* ---- Workflow tab: ONE rail down the left through every stage and step; each stage is a coloured badge on the rail ---- */
.ro .wft{gap:0}
.ro .wf2-stage,.ro .wf2-stage[data-state]{--sc:var(--ink-3);position:relative;margin:0;padding:0}
.ro .wf2-stage[data-stage=intake]{--sc:var(--c-ind)}.ro .wf2-stage[data-stage=drafting]{--sc:var(--c-sky)}.ro .wf2-stage[data-stage=review]{--sc:var(--c-vio)}
.ro .wf2-stage[data-stage=approval]{--sc:var(--c-amb)}.ro .wf2-stage[data-stage=signature]{--sc:var(--c-red)}.ro .wf2-stage[data-stage=active]{--sc:var(--c-emr)}
.ro .wf2-stage::before{content:"";position:absolute;left:13px;top:0;bottom:0;width:2px;border-radius:2px;background:repeating-linear-gradient(to bottom,var(--border-strong) 0 4px,transparent 4px 8px)}
.ro .wf2-stage:last-child::before{bottom:calc(100% - 44px)}
.ro .wf2-stage>.wsg{position:relative;display:flex;align-items:center;gap:10px;width:100%;margin:0;padding:12px 0 12px 42px;border:0;border-radius:0;background:none}
.ro .wf2-stage>.wsg::before{content:"";position:absolute;left:7px;top:50%;width:14px;height:14px;margin-top:-7px;border-radius:50%;background:var(--sc);box-shadow:0 0 0 4px var(--surface),0 0 0 6px color-mix(in srgb,var(--sc) 30%,transparent);z-index:2}
.ro .wf2-stage>.wsg>span:first-of-type,.ro .wf2-stage[data-state]>.wsg>span:first-of-type{padding:5px 14px;border-radius:99px;background:color-mix(in srgb,var(--sc) 16%,var(--surface));color:var(--sc);font:800 11.5px var(--sans);letter-spacing:.08em;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--sc) 35%,transparent)}
.ro .wf2-stage>.wsg>em{margin-left:auto;font-style:normal;font:650 12px var(--sans);color:var(--sc)}
.ro .wf2-list{padding:0 0 6px}
.ro .wft .t,.ro .wft .t.cur,.ro .wft .t.warn,.ro .wft .t.done,.ro .wft .t.skip,.ro .wft .t.todo{padding:0 0 12px}
.ro .wft .t::before{bottom:-14px}
.ro .wf2-list>.t:last-child::before{display:block}
.ro .wf2-stage:last-child .wf2-list>.t:last-child::before{display:none}

/* ---- Workflow card header and controls: a title block, the progress at the right, then one control bar ---- */
.ro .wfcard>.ch{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;column-gap:14px;row-gap:3px;padding:16px 18px}
.ro .wfcard>.ch .glyph{grid-row:1/3;width:42px;height:42px;border-radius:13px}
.ro .wfcard>.ch h2{grid-column:2;grid-row:1;margin:0;font-size:17px;letter-spacing:-.01em}
.ro .wfcard>.ch .wfname{grid-column:2;grid-row:2;font-size:12.5px;color:var(--ink-3)}
.ro .wfcard>.ch .cnt{grid-column:3;grid-row:1;justify-self:end;margin:0;font:700 12.5px var(--sans);color:var(--good)}
.ro .wfcard>.ch .wfprog{grid-column:3;grid-row:2;justify-self:end;width:170px;height:8px;margin:0}
.ro .wfcard .hint{margin:0 0 12px;font-size:12px}
.ro .wfcard .flowmeta{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 12px;padding:9px 12px;border-radius:12px;background:var(--surface-2);box-shadow:inset 0 0 0 1px var(--border);font-size:12.5px}
.ro .wfcard .flowmeta b{color:var(--ink)}
.ro .wfcard .wf2-bar{gap:10px;margin:0 0 20px;padding:10px;border:1px solid var(--border);border-radius:14px;background:var(--surface-2)}
.ro .wfcard .wf2-seg{gap:6px;padding:0;background:none}
.ro .wfcard .wf2-seg button{height:34px;padding:0 14px;border-radius:99px;background:var(--surface);color:var(--ink-2);box-shadow:inset 0 0 0 1px var(--border)}
.ro .wfcard .wf2-seg button:hover:not(:disabled){background:var(--surface);box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)}
.ro .wfcard .wf2-seg button[aria-pressed="true"]{background:var(--accent);color:var(--accent-ink);box-shadow:0 4px 10px -4px color-mix(in srgb,var(--accent) 70%,transparent)}
.ro .wfcard .wf2-seg button[aria-pressed="true"] i{background:color-mix(in srgb,var(--accent-ink) 28%,transparent);color:var(--accent-ink)}
.ro .wfcard .wf2-btn{height:34px;padding:0 14px;border-radius:99px;background:var(--surface);color:var(--ink);box-shadow:inset 0 0 0 1px var(--border-strong)}
.ro .wfcard .wf2-btn:hover{background:var(--surface);box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent)}

/* Send back card (Workflow tab) */
.ro .sendback.wfpanel{display:flex;flex-direction:column;gap:12px;margin-top:14px;padding:16px;border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.06)}
.ro .sendback .sbhead{display:flex;gap:12px;align-items:flex-start}
.ro .sendback .sbico{flex:none;display:grid;place-items:center;width:36px;height:36px;border-radius:12px;background:color-mix(in srgb,var(--c-amb) 16%,transparent);color:var(--c-amb)}
.ro .sendback .sbhead .lbl{font:700 14px var(--sans);letter-spacing:0;text-transform:none;color:var(--ink)}
.ro .sendback .sbhead p{margin:2px 0 0;font-size:12.5px;color:var(--ink-3)}
.ro .sendback .sbf{display:flex;flex-direction:column;gap:5px}.ro .sendback .sbf>span{font:650 12px var(--sans);color:var(--ink-2)}
.ro .sendback .sel,.ro .sendback .sbta{width:100%;padding:10px 12px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--ink);font:500 13px var(--sans);outline:none}
.ro .sendback .sel:focus,.ro .sendback .sbta:focus{border-color:var(--c-ind);box-shadow:0 0 0 3px color-mix(in srgb,var(--c-ind) 16%,transparent)}.ro .sendback .sbta{resize:vertical}
.ro .sendback .sbact{display:flex;gap:8px}
.ro .sendback .sbact .btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:40px;padding:0 16px;border-radius:10px;font:650 13px var(--sans)}
.ro .sendback .sbact .btn.pri{flex:1;background:var(--c-amb);border-color:var(--c-amb);color:#fff}.ro .sendback .sbact .btn.pri:disabled{opacity:.5;cursor:not-allowed}
.ro .sbtn{display:inline-flex;align-items:center;gap:7px;min-height:38px;padding:0 14px;border-radius:10px;border:1px solid color-mix(in srgb,var(--c-amb) 45%,var(--border));background:color-mix(in srgb,var(--c-amb) 10%,var(--surface));color:var(--c-amb);font:650 13px var(--sans);cursor:pointer;transition:transform .15s,box-shadow .15s}
.ro .sbtn:hover{transform:translateY(-1px);box-shadow:0 6px 14px -8px rgba(20,26,40,.35)}.ro .sbtn[aria-expanded=true]{background:var(--c-amb);color:#fff}.ro .sbtn .ic{width:15px;height:15px}
`;
