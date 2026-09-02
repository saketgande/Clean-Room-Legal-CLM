# Aegis — UI/UX Review Checklist

A page-by-page review against a fixed rubric. Kept as a living document.

**Legend:** ✅ pass · ❌ fail (bug) · ⚠️ partial / needs attention · ⏳ pending · 🔍 needs live browser (can't verify from code alone)

**Two evidence types:**
- **Pass 1 — code evidence** (static): states handled, data wiring, dead controls, hardcoded colors, a11y attributes, responsive classes, nav wiring. Verifiable by reading source; cited as `file:line`.
- **Pass 2 — live evidence** (browser, serial, single-session): actual rendering, real interaction behavior, responsive rendering, contrast, dark mode visual. Cited by screenshot/DOM read.

## Rubric (per route)

| # | Dimension | What "pass" means |
|---|---|---|
| 1 | **States** | Loading, empty, and error states handled; detail pages handle not-found. No silent `return null` / blank screen. |
| 2 | **Data** | Wired to a real backend endpoint; renders real fields. No mock/stub/hardcoded data. |
| 3 | **Interactions** | Primary controls (buttons, forms, tabs) invoke real handlers/mutations. No dead no-ops. |
| 4 | **Responsive** | Uses responsive utilities; no fixed-width layout that breaks on mobile. |
| 5 | **Dark mode** | No hardcoded `bg-white`/hex; uses theme tokens (slate scale / CSS vars). |
| 6 | **A11y** | Accessible names on controls, labels on inputs, alt on images, aria-label on icon-only buttons. |
| 7 | **Wiring** | Reachable (nav or intentional deep-link); route resolves; consistent auth/perm gating. |

## Checklist

Columns: States · Data · Interact · Responsive · Dark · A11y · Wiring

### In-app routes

| Route | St | Da | In | Rs | Dk | A11 | Wr | Notes |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| `/` (Ask Aegis) | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | resp OK @375 (live); landing send button (ArrowUp) no aria-label |
| `/intake` | ❌ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | resp OK @375 (live); dark FIXED (governance-ladder → slate-100); board still swallows fetch errors (M1 open) |
| `/my-work` | ✅ | ✅ | ✅ | ✅ | ✅ | 🔍 | ✅ | resp OK @375 (live); clickable cards — keyboard focus still needs live check |
| `/contracts` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | resp OK @375 (live); search input no label; `<tr onClick>` (has Open fallback) |
| `/contracts/[id]` | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | **dead:** Save = fake toast; format toolbar + track-changes toggle inert |
| `/matters` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | rows use `window.location.href` (full reload) + not keyboard-accessible |
| `/matters/[id]` | ✅ | ✅ | ⚠️ | ✅ | ✅ | ✅ | ✅ | **dead:** "Add to matter" is just a Link back to `/matters` |
| `/search` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | resp OK @375 (live); `<label>`s not associated — SR can't announce fields |
| `/brain` | ✅ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ | Ask can double-submit (no `busy` guard); ask input + selects no label |
| `/assistant` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | resp OK @375 (same component as `/`); deep-link target (not dead), redundant |
| `/tabular-reviews` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | clean |
| `/tabular-reviews/[id]` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | remove-column Trash icon has no aria-label |
| `/playbooks` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | clean |
| `/playbooks/[id]` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | nit: runs table shows raw contract UUID |
| `/playbooks/build` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | send + doc-remove icon btns no aria-label; no in-page perm gate |
| `/prompts` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | resp OK @375 (live); search input placeholder-only (minor) |
| `/approvals` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | resp OK @375 (live); dead `--bg` CSS var (unused) |
| `/signatures` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | loading is text not spinner (handled, not blank) |
| `/obligations` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | resp OK @375 (live); owner = raw UUID text input (should be picker) |
| `/sla` | ✅ | ✅ | 🔍 | ✅ | ✅ | 🔍 | ⚠️ | nav shows for all users but body gates on `intake:read` (graceful dead-end) |
| `/notices` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | search + selects no label; `<tr onClick>` no keyboard fallback |
| `/notices/[id]` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | nits: empty timeline no all-clear; escalate banner links generic `/intake` |
| `/renewals` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | clean; AI recommendation wired in decide modal |
| `/workflow-builder` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | clean; self-gates on admin_panel:access |
| `/workflow-builder/[id]` | ⚠️ | ✅ | ⚠️ | ✅ | ✅ | ⚠️ | ✅ | no NotFound boundary; **dead:** "Test run" fake toast; canvas icon btns title-only |
| `/jobs` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **FIXED** — added to Admin rail group (live-verified in sidebar) |
| `/notifications` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | **FIXED** — added to Workspace rail group (live-verified in sidebar) |
| `/admin` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | dark FIXED (chips → slate-100); team edit/delete icon btns no aria-label |
| `/ai-usage` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ⚠️ | no in-page perm guard (rail-only); chart has no text alt |

### Public routes

| Route | St | Da | In | Rs | Dk | A11 | Wr | Notes |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|---|
| `/login` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | intentional light (public); mode toggle lacks role=tab (cosmetic) |
| `/invitations/accept` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | intentional light (public); token-invalid state handled |
| `/approve/[token]` | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | intentional light (public); comment textarea placeholder-only; all 4 token states handled |
| `/s/[token]` (external share) | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ | intentional light (public); name input + comment textarea placeholder-only |

## Findings (bugs & fixes)

### Pass 2 — live discoveries (browser)

- **DARK MODE — ✅ FIXED (wired up, live-verified).** Was: `.dark` CSS existed but never activated (theme script defaulted to light + ignored OS; body used a compile-time `theme()` value that couldn't flip). Fix: (1) theme script now follows `prefers-color-scheme` when no explicit choice is stored, and reacts to OS changes (`layout.tsx`); (2) `body` background/color switched from `theme("colors.slate.N")` to `rgb(var(--color-slate-N))` so they flip (`globals.css:235-236`); (3) the two `bg-white` spots → `bg-slate-100` (`admin/page.tsx:1967`, `_governance-ladder.tsx:214`). **Live-verified:** under emulated dark OS, `body` = `rgb(20,21,24)`, `.dark` applied, **0 white cards**; light mode unchanged (`rgb(244,246,249)`, no regression).
- **Responsive verified at 375px (mobile), no horizontal body overflow, on:** `/contracts`, `/obligations`, `/approvals`, `/`, `/assistant`, `/prompts`, `/my-work`, `/search`, `/sla`, `/intake`, `/admin`. All PASS (content reflows / scrolls inside its own container). Detail `[id]` pages and modals not yet live-checked.

### HIGH — reachability

- **H1 · `/jobs` · orphan route.** No nav path in the rendered sidebar (`AegisRail`, `components/aegis-rail.tsx`); the only link lives in dead `SidebarNav` (`components/app-shell.tsx:392`). Users can't reach it. → Add a rail item (e.g. under Admin) or delete the page.
- **H2 · `/notifications` · unreachable on desktop.** No `AegisRail` entry; the desktop link is in dead `SidebarNav` (`app-shell.tsx:289`). Only the mobile top bar links it (`app-shell.tsx:514`). → Add to `AegisRail` (ideally with an unread badge).

### MEDIUM — wrong behavior / dead chrome / dark mode

- **M1 · `/intake` · queue board swallows fetch errors.** `LegalIntakeBoard` (`_legal-intake.tsx:166`) has no loading/error branch; a failed `intakeApi.list()` renders "No requests match these filters" (`:353`) — an error disguised as empty. The state-handling `InboxCockpit` (`intake/page.tsx:950`) exists but is never rendered. → Handle `isLoading`/`error`, or render `InboxCockpit`.
- **M2 · `/contracts/[id]` · Save button is a fake toast.** `onClick={() => notify("Draft saved")}` persists nothing (`_clm-workspace.tsx:134`). ✅ **Live-confirmed:** clicking Save fired **0 network requests** (fetch + XHR both hooked). → Wire to a real save mutation or remove (editor already persists edits).
- **M3 · `/contracts/[id]` · format toolbar + track-changes are dead.** Heading/B/I/U/list buttons have no handlers; `track` state never reaches `ContractDocument` (`_clm-workspace.tsx:150-160,169`). ✅ **Live-confirmed:** the editor is a plain `<textarea>` — clicking Bold with text selected changed nothing and fired 0 network; a textarea cannot support rich formatting or track-changes at all. → Wire to a rich editor or remove the chrome.
- **M4 · `/matters/[id]` · "Add to matter" does nothing.** Primary action is `<Link href="/matters">` (`matters/[id]/page.tsx:95`). ✅ **Live-confirmed:** the control is `<a href="/matters">` — bounces to the list, adds nothing. → Real add flow, or rename "Back to matters".
- **M5 · `/admin` · dark-mode break.** ✅ **FIXED** — `Chips` unselected `bg-white` → `bg-slate-100` (`admin/page.tsx:1967`), which flips under `.dark`.
- **M6 · `/intake` · dark-mode break.** ✅ **FIXED** — `_governance-ladder.tsx:214` `bg-white` → `bg-slate-100`.
- **M7 · `/search` · form labels not associated.** All `<label>`s are siblings with no `htmlFor` and don't wrap inputs (`search/page.tsx:125-160,211,297`) → screen readers announce unnamed fields. → wrap or add `htmlFor`/`id`.
- **M8 · `/tabular-reviews/[id]` · unlabeled icon button.** Remove-column Trash has no `aria-label` (`tabular-reviews/[id]/page.tsx:352-361`). → `aria-label="Remove column"`.

### LOW / nits

- `/workflow-builder/[id]` — ✅ **live-confirmed:** a bogus id renders a **blank designer with no NotFound and no error** (`page.tsx:26`); "Test run" click fired **0 network requests** (fake toast, `_designer.tsx:221`). Canvas icon buttons use `title` not `aria-label` (`:187,188,234,245`).
- `/brain` — Ask can double-submit (no `busy` guard: `page.tsx:47,105,106`); ask input + matter/contract selects lack labels (`:105,110,116`).
- `/` & `/assistant` — landing composer send button (ArrowUp) has no `aria-label` (`assistant-workspace.tsx:938`); in-chat button is labeled (`:1262`).
- `/ai-usage` — no in-page perm guard (rail-only gating, backend still enforces); daily bar chart has no text alternative.
- `/matters` (list) — rows use `window.location.href` (full reload, drops SPA nav) and aren't keyboard-focusable (`matters/page.tsx:176`).
- `/obligations` — Edit modal "owner" is a raw UUID text input (`:446-448`) → user picker.
- `/notices` (list) + `/contracts` (list) — search inputs / selects placeholder-only, no `aria-label`; `<tr onClick>` rows (contracts has an Open-button fallback, notices doesn't).
- `/notices/[id]` — empty timeline renders no all-clear line (`:404`); escalate banner links generic `/intake` not the specific ticket (`:217`).
- `/playbooks/[id]` — runs table shows raw contract UUID (`:767`).
- `/playbooks/build` — send + doc-remove icon buttons no `aria-label`; no in-page perm gate.
- `/approve/[token]`, `/s/[token]` — comment textareas + share name input placeholder-only (public pages counterparties use).
- `/approvals` — dead `--bg` CSS custom property (`page.tsx:796,805`), never referenced.
- `/admin` — team edit/delete icon buttons no `aria-label` (`:2076,2091`). (Modal `grid-cols-2` concern **dismissed** — live at 375px the modal fits, inputs ~140px, no overflow.)
- `/matters` + `/my-work` rows — ✅ **live-confirmed:** rows are `<tr tabIndex="-1">` (not keyboard-focusable as rows), but each contains a focusable name link, so a keyboard path exists — mitigated low nit, not a blocker.
- **Dead code** — `SidebarNav` + user-menu nav (`app-shell.tsx:~95-430`) and `InboxCockpit` (`intake/page.tsx:950`) are defined but never rendered; they hold the missing jobs/notifications links and the intake error-handling. Reconcile against `AegisRail` or delete.

## Pass log

- **Pass 1 (code evidence): COMPLETE** — all 33 routes statically reviewed against the 7-dimension rubric with `file:line` evidence (5 parallel reviewers). 2 high, 8 medium, ~18 low/nits.
- **Pass 2 (live browser): SUBSTANTIALLY DONE.**
  - ✅ Reachability HIGH findings confirmed live (`/jobs` orphan, `/notifications` desktop-hidden).
  - ✅ Responsive verified at 375px — no horizontal body overflow — on 11 top-level pages **+ 3 detail pages** (`/tabular-reviews/[id]`, `/notices/[id]`, `/playbooks/[id]`). 14 pages, all pass.
  - ✅ Dark-mode activation checked live — **does not turn on** (see Pass 2 discoveries).
  - ✅ Dead-chrome mediums proven by real behavior: `/contracts/[id]` **Save → 0 network requests**; `/matters/[id]` **"Add to matter" = `<a href="/matters">`**; `/workflow-builder/[id]` **"Test run" → 0 network** and **bogus id → blank designer, no NotFound/no error**.
  - ✅ All 6 detail `[id]` pages render real records (none fall to NotFound): contracts (NDA), matters (Acme Corp), workflow, tabular, notices, playbooks.
  - ✅ `/contracts/[id]` toolbar effect: editor is a plain `<textarea>`; Bold changed nothing + 0 network → B/I/U + track-changes confirmed dead.
  - ✅ Keyboard focus (`/my-work`, `/matters`): rows `tabIndex="-1"` but each has a focusable name link → keyboard path exists (mitigated).
  - ✅ `/admin` team modal responsive: opened at 375px, fits (359px wide, inputs ~140px, no overflow) → dismissed.
  - ⏳ **Only remaining, left honest:** forced empty/error states (loading/error branches were code-verified in Pass 1 for every route; I did not inject synthetic API failures to see them render). This is the one dimension still on code evidence alone.
