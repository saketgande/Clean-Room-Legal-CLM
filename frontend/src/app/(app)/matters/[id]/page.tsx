"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { assistantApi, mattersApi, usersApi } from "@/lib/endpoints";
import { fmtRelative } from "@/lib/utils";
import {
  Button,
  CenterSpinner,
  ErrorState,
  Field,
  Modal,
  NotFound,
  Select,
} from "@/components/ui";
import { fmtDate, titleCase } from "@/lib/utils";
import { useToast } from "@/components/toast";
import type { MatterRollupItem, MatterType, UserResponse } from "@/lib/types";

export default function MatterDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [tab, setTab] = useState("overview");

  const { data: project, isLoading, error } = useQuery({
    queryKey: ["project", id],
    queryFn: () => mattersApi.get(id),
  });
  const { data: overview } = useQuery({
    queryKey: ["matter-overview", id],
    queryFn: () => mattersApi.overview(id),
  });
  const { data: members } = useQuery({
    queryKey: ["matter-members", id],
    queryFn: () => mattersApi.members(id),
  });
  const { data: activity } = useQuery({
    queryKey: ["matter-activity", id],
    queryFn: () => mattersApi.activity(id),
  });
  const { data: users } = useQuery({
    queryKey: ["org-users"],
    queryFn: () => usersApi.list(),
  });

  if (isLoading) return <CenterSpinner label="Loading matter…" />;
  if (error) return <ErrorState error={error} />;
  if (!project)
    return (
      <div className="p-6">
        <NotFound
          title="Matter not found"
          description="This matter may have been closed or deleted, or you may not have access to it."
          backHref="/matters"
          backLabel="Back to matters"
        />
      </div>
    );

  const c = overview?.counts;
  const statusPill =
    project.status === "active" ? "act"
    : project.status === "closed" ? "done"
    : project.status === "on_hold" ? "warn"
    : "ext";

  return (
    <div className="mw">
      <style dangerouslySetInnerHTML={{ __html: MW_CSS }} />

      <div className="crumb">
        <Link href="/matters">Matters</Link>
        <span>/</span>
        {project.client_name && (<><b>{project.client_name}</b><span>/</span></>)}
        <span className="num">{project.matter_number ?? "—"}</span>
      </div>

      <div className="mhead">
        <div className="mh-l">
          <h1 className="mtitle">{project.name}</h1>
          <div className="mmeta">
            {project.matter_number && <span className="num">{project.matter_number}</span>}
            {project.client_name && <span>{project.client_name}</span>}
            <span>{titleCase(project.matter_type)}</span>
            {project.opened_at && <span>Opened {fmtDate(project.opened_at)}</span>}
          </div>
        </div>
        <div className="spacer" />
        <span className={`pill ${statusPill}`}>{titleCase(project.status)}</span>
        <Link href="/matters" className="btn">Add to matter</Link>
        <button className="btn pri" onClick={() => setTab("assistant")}>Ask Aegis</button>
      </div>

      <div className="tabs">
        {[
          { id: "overview", label: "Overview" },
          { id: "contracts", label: "Contracts", n: c?.contracts },
          { id: "requests", label: "Intake", n: c?.intake_open },
          { id: "obligations", label: "Obligations", n: c?.obligations_open },
          { id: "approvals", label: "Approvals", n: c?.approvals_pending },
          { id: "notices", label: "Notices", n: c?.notices_open },
          { id: "assistant", label: "Assistant" },
          { id: "people", label: "People" },
          { id: "activity", label: "Activity" },
        ].map((t) => (
          <button key={t.id} className={tab === t.id ? "on" : ""} onClick={() => setTab(t.id)}>
            {t.label}
            {t.n != null && t.n > 0 && <span className="b">{t.n}</span>}
          </button>
        ))}
      </div>

      <div className="body">
        {tab === "overview" &&
          (!overview || !c ? (
            <CenterSpinner label="Loading matter…" />
          ) : (
            <>
              <div className="stats">
                <div className="stat"><div className="k">Contracts</div><div className="v">{c.contracts}</div><div className="foot">{c.contracts_active} active</div></div>
                <div className="stat"><div className="k">Obligations due</div><div className="v">{c.obligations_open}</div><div className={`foot ${c.obligations_overdue > 0 ? "rd" : ""}`}>{c.obligations_overdue > 0 ? `${c.obligations_overdue} overdue` : "on track"}</div></div>
                <div className="stat"><div className="k">Approvals</div><div className="v">{c.approvals_pending}</div><div className="foot">pending</div></div>
                <div className="stat"><div className="k">Notices</div><div className="v">{c.notices_open}</div><div className="foot">open</div></div>
                <div className="stat"><div className="k">Requests</div><div className="v">{c.intake_open}</div><div className="foot">open intake</div></div>
              </div>

              <div className="grid">
                <div className="col">
                  <ModCard icon={IC.contract} title="Contracts" count={`${overview.contracts.length} linked`} items={overview.contracts} right="pill" itemHref={(cid) => `/contracts/${cid}`} more="View all contracts" onMore={() => setTab("contracts")} empty="No contracts filed under this matter yet." />
                  <ModCard icon={IC.clock} title="Obligations & deadlines" count={`${c.obligations_open} open`} items={overview.obligations} right="meta" more="Open obligations" onMore={() => setTab("obligations")} empty="No obligations across this matter's contracts." />
                  <ModCard icon={IC.inbox} title="Intake requests" count={`${c.intake_open} open`} items={overview.intake} right="pill" more="Open Legal Intake" onMore={() => setTab("requests")} empty="No requests filed under this matter." />
                </div>
                <div className="col">
                  <ModCard icon={IC.check} title="Approvals" count={`${c.approvals_pending} pending`} items={overview.approvals} right="pill" empty="No approvals for this matter." />
                  <ModCard icon={IC.shield} title="Notices" count={`${c.notices_open} active`} items={overview.notices} right="pill" empty="No notices for this matter." />
                  <TeamCard members={members ?? []} users={users ?? []} />
                  <ActivityCard items={activity ?? []} />
                </div>
              </div>
            </>
          ))}

        {tab === "contracts" &&
          (!overview ? <CenterSpinner label="Loading…" /> : (
            <div className="pane">
              <ModCard full icon={IC.contract} title="Contracts" count={`${overview.contracts.length} filed`} items={overview.contracts} right="pill" itemHref={(cid) => `/contracts/${cid}`} empty="No contracts filed under this matter yet — file one from the Matters board." />
            </div>
          ))}
        {tab === "obligations" &&
          (!overview ? <CenterSpinner label="Loading…" /> : (
            <div className="pane">
              <ModCard full icon={IC.clock} title="Obligations & deadlines" count={`${overview.obligations.length} shown`} items={overview.obligations} right="meta" openHref="/obligations" openLabel="Open obligations board" empty="No obligations across this matter's contracts." />
            </div>
          ))}
        {tab === "notices" &&
          (!overview ? <CenterSpinner label="Loading…" /> : (
            <div className="pane">
              <ModCard full icon={IC.shield} title="Notices" count={`${overview.notices.length} shown`} items={overview.notices} right="pill" openHref="/notices" openLabel="Open notices" empty="No notices for this matter." />
            </div>
          ))}
        {tab === "approvals" &&
          (!overview ? <CenterSpinner label="Loading…" /> : (
            <div className="pane">
              <ModCard full icon={IC.check} title="Approvals" count={`${overview.approvals.length} shown`} items={overview.approvals} right="pill" openHref="/approvals" openLabel="Open approvals" empty="No approvals for this matter." />
            </div>
          ))}
        {tab === "requests" &&
          (!overview ? <CenterSpinner label="Loading…" /> : (
            <div className="pane">
              <ModCard full icon={IC.inbox} title="Intake requests" count={`${overview.intake.length} shown`} items={overview.intake} right="pill" openHref="/intake" openLabel="Open Legal Intake" empty="No intake requests filed under this matter." />
            </div>
          ))}
        {tab === "assistant" && <MatterAssistantTab matterId={id} projectName={project.name} />}
        {tab === "people" && <PeopleTab matterId={id} />}
        {tab === "activity" &&
          (!activity ? <CenterSpinner label="Loading…" /> : (
            <div className="pane"><ActivityCard full items={activity} /></div>
          ))}
      </div>
    </div>
  );
}

// --- Overview dashboard pieces (scoped `.mw`, matching the Matters artifact) ---
const IC = {
  contract: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  clock: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/>',
  inbox: '<path d="M3 7l9 6 9-6"/><rect x="3" y="5" width="18" height="14" rx="2"/>',
  check: '<path d="M9 11l3 3 8-8"/><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"/>',
  shield: '<path d="M12 3l9 4v5c0 5-4 8-9 9-5-1-9-4-9-9V7z"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>',
  bot: '<rect x="3" y="4" width="18" height="14" rx="2"/><path d="M8 21h8M12 18v3"/><circle cx="9" cy="11" r="1"/><circle cx="15" cy="11" r="1"/>',
};

function toneFor(status: string | null): string {
  const s = (status ?? "").toLowerCase();
  if (/(overdue|reject|critical|high|breach|expired)/.test(s)) return "c";
  if (/(sign|signed|done|complete|approved|managed|active|resolved|closed)/.test(s)) return "g";
  if (/(review|pending|at_risk|escalated|on_hold)/.test(s)) return "w";
  if (/(external|counterparty|their)/.test(s)) return "e";
  return "a";
}

function ModCard({
  icon,
  title,
  count,
  items,
  right,
  itemHref,
  more,
  onMore,
  openHref,
  openLabel,
  full,
  empty,
}: {
  icon: string;
  title: string;
  count?: string;
  items: MatterRollupItem[];
  right: "pill" | "meta";
  itemHref?: (id: string) => string;
  more?: string;
  onMore?: () => void;
  openHref?: string;
  openLabel?: string;
  full?: boolean;
  empty: string;
}) {
  const shown = full ? items : items.slice(0, 5);
  return (
    <div className="mod">
      <div className="mh">
        <span className="mi" dangerouslySetInnerHTML={{ __html: `<svg class="ic" viewBox="0 0 24 24">${icon}</svg>` }} />
        <span className="mt">{title}</span>
        {count && <span className="mc">{count}</span>}
      </div>
      {shown.length === 0 ? (
        <div className="rowempty">{empty}</div>
      ) : (
        shown.map((it) => {
          const inner = (
            <>
              <span className={`sq ${toneFor(it.status)}`} />
              <span className="rt">{it.title}</span>
              {right === "meta" && it.meta ? (
                <span className={`rs ${toneFor(it.status) === "c" ? "crit" : ""}`}>{it.meta}</span>
              ) : it.status ? (
                <span className={`pill ${toneFor(it.status)}`}>{titleCase(it.status)}</span>
              ) : it.meta ? (
                <span className="rs">{it.meta}</span>
              ) : null}
            </>
          );
          return itemHref ? (
            <Link key={it.id} href={itemHref(it.id)} className="row rowlink">{inner}</Link>
          ) : (
            <div key={it.id} className="row">{inner}</div>
          );
        })
      )}
      {openHref ? (
        <Link href={openHref} className="more">{openLabel ?? "Open module"} →</Link>
      ) : more && items.length > 5 ? (
        <button className="more" onClick={onMore}>{more} →</button>
      ) : null}
    </div>
  );
}

function TeamCard({
  members,
  users,
}: {
  members: { id: string; role: string; user_id: string }[];
  users: UserResponse[];
}) {
  const byId: Record<string, UserResponse> = {};
  for (const u of users) byId[u.id] = u;
  const nameOf = (uid: string) => byId[uid]?.full_name || byId[uid]?.email || "Unknown";
  const initialsOf = (uid: string) =>
    nameOf(uid).split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";
  return (
    <div className="mod">
      <div className="mh">
        <span className="mi" dangerouslySetInnerHTML={{ __html: `<svg class="ic" viewBox="0 0 24 24">${IC.users}</svg>` }} />
        <span className="mt">Matter team</span>
        <span className="mc">{members.length} {members.length === 1 ? "person" : "people"}</span>
      </div>
      {members.length === 0 ? (
        <div className="rowempty">No one added to this matter yet.</div>
      ) : (
        <div className="team">
          {members.map((m) => (
            <span key={m.id} className="who" title={`${nameOf(m.user_id)} · ${titleCase(m.role)}`}>
              <span className="av">{initialsOf(m.user_id)}</span>
              <span className="role">{nameOf(m.user_id)} · {titleCase(m.role)}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ActivityCard({ items, full }: { items: { id: string; title: string; occurred_at: string | null }[]; full?: boolean }) {
  const shown = full ? items : items.slice(0, 5);
  return (
    <div className="mod act">
      <div className="mh">
        <span className="mi" dangerouslySetInnerHTML={{ __html: `<svg class="ic" viewBox="0 0 24 24">${IC.clock}</svg>` }} />
        <span className="mt">Recent activity</span>
        {full && items.length > 0 && <span className="mc">{items.length} {items.length === 1 ? "event" : "events"}</span>}
      </div>
      {shown.length === 0 ? (
        <div className="rowempty">No activity yet.</div>
      ) : (
        shown.map((a) => (
          <div key={a.id} className="row">
            <span className="dotline" />
            <span className="rt">{a.title}</span>
            {a.occurred_at && <span className="rs">{fmtRelative(a.occurred_at)}</span>}
          </div>
        ))
      )}
    </div>
  );
}

const MW_CSS = `
.mw{--ext:#75589f;--ext-soft:#efe8f7;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--serif:"Iowan Old Style",Charter,Georgia,"Times New Roman",serif;--sans:var(--font-sans);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink);font:400 13px/1.5 var(--sans);max-width:1280px;margin:0 auto;padding:24px 28px 48px}
.dark .mw{--ext:#ab90d2;--ext-soft:#221b31;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.mw .ic{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.9;stroke-linecap:round;stroke-linejoin:round;display:block}
.mw .crumb{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3);margin-bottom:9px}
.mw .crumb a{color:var(--ink-2);text-decoration:none;font-weight:600} .mw .crumb a:hover{color:var(--accent)} .mw .crumb b{color:var(--ink-2);font-weight:600} .mw .crumb .num{font-family:var(--mono)}
.mw .mhead{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.mw .mtitle{font-family:var(--serif);font-size:23px;line-height:1.12;letter-spacing:-.01em;margin:0}
.mw .mmeta{display:flex;flex-wrap:wrap;gap:6px 14px;margin-top:7px;font-size:12px;color:var(--ink-2)} .mw .mmeta .num{font-family:var(--mono);color:var(--ink-3)}
.mw .spacer{flex:1;min-width:20px}
.mw .pill{font-family:var(--mono);font-size:10.5px;font-weight:600;padding:3px 9px;border-radius:999px;white-space:nowrap;background:var(--surface-2);color:var(--ink-2)}
.mw .pill.act,.mw .pill.a{background:var(--accent-soft);color:var(--accent)} .mw .pill.warn,.mw .pill.w{background:var(--warn-soft);color:var(--warn)} .mw .pill.done,.mw .pill.g{background:var(--good-soft);color:var(--good)} .mw .pill.c{background:var(--crit-soft);color:var(--crit)} .mw .pill.ext,.mw .pill.e{background:var(--ext-soft);color:var(--ext)}
.mw .btn{padding:7px 12px}  
.mw .tabs{display:flex;gap:2px;border-bottom:1px solid var(--border);flex-wrap:wrap;margin-bottom:18px}
.mw .tabs button{padding:8px 11px;font-size:12.5px;font-weight:600;color:var(--ink-3);border-bottom:2px solid transparent;margin-bottom:-1px;background:none;border-top:0;border-left:0;border-right:0;cursor:pointer;display:flex;gap:6px;align-items:center}
.mw .tabs button.on{color:var(--accent);border-bottom-color:var(--accent)} .mw .tabs button .b{font:700 9px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:1px 5px} .mw .tabs button.on .b{background:var(--accent-soft);color:var(--accent)}
.mw .stats{display:grid;grid-template-columns:repeat(5,1fr);gap:11px;margin-bottom:16px}
@media(max-width:820px){.mw .stats{grid-template-columns:repeat(2,1fr)}}
.mw .stat{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 13px;box-shadow:var(--shadow)}
.mw .stat .k{font-size:11px;color:var(--ink-3)} .mw .stat .v{font-size:23px;font-weight:700;margin-top:4px;letter-spacing:-.01em;font-variant-numeric:tabular-nums} .mw .stat .foot{font-size:11px;margin-top:2px;color:var(--ink-3)} .mw .stat .foot.rd{color:var(--crit)}
.mw .grid{display:grid;grid-template-columns:1.4fr 1fr;gap:14px}
@media(max-width:900px){.mw .grid{grid-template-columns:1fr}}
.mw .col{display:flex;flex-direction:column;gap:14px}
.mw .mod{background:var(--surface);border:1px solid var(--border);border-radius:13px;box-shadow:var(--shadow);overflow:hidden}
.mw .mod .mh{display:flex;align-items:center;gap:9px;padding:12px 14px;border-bottom:1px solid var(--border)}
.mw .mod .mi{width:26px;height:26px;border-radius:8px;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;flex:none}
.mw .mod .mt{font-weight:640;font-size:13px} .mw .mod .mc{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink-3)}
.mw .row{display:flex;align-items:center;gap:10px;padding:9px 14px;border-top:1px solid var(--border);font-size:12.5px;text-decoration:none;color:inherit} .mw .row:first-of-type{border-top:0}
.mw .rowlink:hover{background:var(--surface-2)}
.mw .row .rt{flex:1;min-width:0;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .mw .row .rs{font-size:11px;color:var(--ink-3)} .mw .row .rs.crit{color:var(--crit)}
.mw .row .sq{width:7px;height:7px;border-radius:2px;flex:none} .mw .sq.a{background:var(--accent)} .mw .sq.w{background:var(--warn)} .mw .sq.g{background:var(--good)} .mw .sq.c{background:var(--crit)} .mw .sq.e{background:var(--ext)}
.mw .rowempty{padding:18px 14px;font-size:12.5px;color:var(--ink-3);text-align:center}
.mw .pane{max-width:none}
.mw .more{width:100%;text-align:left;padding:9px 14px;font-size:11.5px;color:var(--accent);font-weight:600;border-top:1px solid var(--border);background:none;border-left:0;border-right:0;border-bottom:0;cursor:pointer}
.mw .team{display:flex;flex-wrap:wrap;gap:8px;padding:12px 14px}
.mw .who{display:flex;align-items:center;gap:8px;background:var(--surface-2);border:1px solid var(--border);border-radius:999px;padding:4px 11px 4px 4px;font-size:12px}
.mw .who .av{width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;display:grid;place-items:center;font-size:9.5px;font-weight:700} .mw .who .role{color:var(--ink-2)}
.mw .act .row{align-items:flex-start} .mw .act .dotline{width:8px;height:8px;border-radius:50%;background:var(--accent);margin-top:4px;flex:none} .mw .act .rt{white-space:normal}
.mw .prow{display:flex;align-items:center;gap:11px;width:100%;text-align:left;padding:10px 14px;border-top:1px solid var(--border);background:none;border-left:0;border-right:0;border-bottom:0;font:inherit;color:inherit;cursor:default}
.mw .prow:first-of-type{border-top:0}
.mw button.prow{cursor:pointer} .mw button.prow:hover{background:var(--surface-2)}
.mw .pav{width:30px;height:30px;border-radius:50%;background:var(--accent);color:#fff;display:grid;place-items:center;font-size:11px;font-weight:700;flex:none} .mw .pav.shared{background:var(--ext)} .mw .pav.ai{background:var(--accent-soft);color:var(--accent)}
.mw .pinfo{display:flex;flex-direction:column;min-width:0;flex:1} .mw .pname{font-weight:600;font-size:13px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .mw .pmail{font-size:11.5px;color:var(--ink-3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mw .premove{width:26px;height:26px;border-radius:7px;border:1px solid transparent;background:none;color:var(--ink-3);cursor:pointer;flex:none;font-size:13px} .mw .premove:hover{background:var(--crit-soft);color:var(--crit);border-color:color-mix(in srgb,var(--crit) 30%,transparent)}
`;

// ---- Assistant (project-scoped chats) ------------------------------------
function MatterAssistantTab({
  matterId,
  projectName,
}: {
  matterId: string;
  projectName: string;
}) {
  const router = useRouter();
  const { notify } = useToast();
  const [busy, setBusy] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["project", matterId, "sessions"],
    queryFn: () => assistantApi.sessions({ matter_id: matterId }),
  });

  function openChat(sessionId: string) {
    router.push(`/assistant?project=${matterId}&session=${sessionId}`);
  }

  async function newChat() {
    setBusy(true);
    try {
      const s = await assistantApi.createSession({
        session_type: "project",
        matter_id: matterId,
        title: `${projectName} — new chat`,
      });
      openChat(s.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to start chat", "error");
      setBusy(false);
    }
  }

  if (isLoading) return <CenterSpinner label="Loading chats…" />;
  const sessions = data ?? [];
  return (
    <div className="pane">
      <div className="mod">
        <div className="mh">
          <span className="mi" dangerouslySetInnerHTML={{ __html: `<svg class="ic" viewBox="0 0 24 24">${IC.bot}</svg>` }} />
          <span className="mt">Ask Aegis · grounded in this matter</span>
          <span className="mc">{sessions.length} {sessions.length === 1 ? "chat" : "chats"}</span>
        </div>
        {sessions.length === 0 ? (
          <div className="rowempty">No chats yet — start one grounded in this matter&apos;s contracts.</div>
        ) : (
          sessions.map((s) => (
            <button key={s.id} className="prow" onClick={() => openChat(s.id)}>
              <span className="pav ai">✦</span>
              <span className="pinfo">
                <span className="pname">{s.title ?? "Untitled conversation"}</span>
                <span className="pmail">{titleCase(s.session_type)} · updated {fmtRelative(s.updated_at)}</span>
              </span>
              <span className={`pill ${s.status === "active" ? "g" : "a"}`}>{titleCase(s.status)}</span>
            </button>
          ))
        )}
        <button className="more" onClick={newChat} disabled={busy}>{busy ? "Starting…" : "+ New chat"}</button>
      </div>
    </div>
  );
}

function PeopleTab({ matterId }: { matterId: string }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [addOpen, setAddOpen] = useState(false);

  const { data: members } = useQuery({ queryKey: ["matter-members", matterId], queryFn: () => mattersApi.members(matterId) });
  const { data: shares } = useQuery({ queryKey: ["matter-shares", matterId], queryFn: () => mattersApi.shares(matterId) });
  const { data: users } = useQuery({ queryKey: ["org-users"], queryFn: () => usersApi.list() });

  const byId: Record<string, UserResponse> = {};
  for (const u of users ?? []) byId[u.id] = u;
  const nameOf = (uid: string) => byId[uid]?.full_name || byId[uid]?.email || "Unknown user";
  const emailOf = (uid: string) => (byId[uid]?.full_name ? byId[uid]?.email ?? "" : "");
  const initialsOf = (uid: string) =>
    nameOf(uid).split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() ?? "").join("") || "?";

  const memberIds = new Set((members ?? []).map((m) => m.user_id));
  const shareRows = (shares ?? []).filter((s) => !s.revoked_at && !memberIds.has(s.shared_with_user_id));
  const total = (members ?? []).length + shareRows.length;

  async function removeMember(userId: string) {
    try {
      await mattersApi.removeMember(matterId, userId);
      qc.invalidateQueries({ queryKey: ["matter-members", matterId] });
      notify("Removed from matter", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to remove", "error");
    }
  }

  const loading = !members || !users;
  return (
    <div className="pane">
      <div className="mod">
        <div className="mh">
          <span className="mi" dangerouslySetInnerHTML={{ __html: `<svg class="ic" viewBox="0 0 24 24">${IC.users}</svg>` }} />
          <span className="mt">People with access</span>
          <span className="mc">{total} {total === 1 ? "person" : "people"}</span>
        </div>
        {loading ? (
          <div className="rowempty">Loading…</div>
        ) : total === 0 ? (
          <div className="rowempty">No one has access yet. Add a colleague to collaborate on this matter.</div>
        ) : (
          <>
            {(members ?? []).map((m) => (
              <div key={m.id} className="prow">
                <span className="pav">{initialsOf(m.user_id)}</span>
                <span className="pinfo">
                  <span className="pname">{nameOf(m.user_id)}</span>
                  {emailOf(m.user_id) && <span className="pmail">{emailOf(m.user_id)}</span>}
                </span>
                <span className="pill a">{titleCase(m.role)}</span>
                <button className="premove" title="Remove" onClick={() => removeMember(m.user_id)}>✕</button>
              </div>
            ))}
            {shareRows.map((s) => (
              <div key={s.id} className="prow">
                <span className="pav shared">{initialsOf(s.shared_with_user_id)}</span>
                <span className="pinfo">
                  <span className="pname">{nameOf(s.shared_with_user_id)}</span>
                  {emailOf(s.shared_with_user_id) && <span className="pmail">{emailOf(s.shared_with_user_id)}</span>}
                </span>
                <span className="pill e">Shared · {titleCase(s.access_level)}</span>
                {s.expires_at && <span className="rs">expires {fmtDate(s.expires_at)}</span>}
              </div>
            ))}
          </>
        )}
        <button className="more" onClick={() => setAddOpen(true)}>+ Add person</button>
      </div>

      {addOpen && (
        <AddPersonModal
          matterId={matterId}
          users={(users ?? []).filter((u) => !memberIds.has(u.id))}
          onClose={() => setAddOpen(false)}
          onDone={() => {
            qc.invalidateQueries({ queryKey: ["matter-members", matterId] });
            notify("Person added", "success");
            setAddOpen(false);
          }}
        />
      )}
    </div>
  );
}

function AddPersonModal({
  matterId,
  users,
  onClose,
  onDone,
}: {
  matterId: string;
  users: UserResponse[];
  onClose: () => void;
  onDone: () => void;
}) {
  const { notify } = useToast();
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!userId) return;
    setBusy(true);
    try {
      await mattersApi.upsertMember(matterId, userId, role);
      onDone();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to add", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Add person to matter"
      footer={
        <>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={submit} loading={busy} disabled={!userId}>Add person</Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Person">
          <Select value={userId} onChange={(e) => setUserId(e.target.value)}>
            <option value="">Select a colleague…</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.full_name} ({u.email})</option>
            ))}
          </Select>
        </Field>
        <Field label="Role" hint="Owner and manager can edit the matter; member and editor collaborate.">
          <Select value={role} onChange={(e) => setRole(e.target.value)}>
            {["member", "editor", "manager", "owner"].map((r) => (
              <option key={r} value={r}>{titleCase(r)}</option>
            ))}
          </Select>
        </Field>
      </div>
    </Modal>
  );
}

