# Design Critique: Aegis Legal CLM (frontend)

**Scope:** Whole app — shell, primitives, all key screens | **Stage:** Refinement | **Date:** 2026-07-07 | **Method:** Code-level review of `frontend/src`

### Overall impression

This is a mature, disciplined interface for its stage: a real design system (`ui.tsx`, 555 lines of strict primitives), centralized status semantics, and complete empty/loading/error states on every list page. The biggest opportunities are small-screen behavior (fixed-width panels, cramped 2-col form grids) and a light consistency pass on overlay spacing — plus wayfinding depth (no breadcrumbs on detail pages).

---

### Usability

| Finding | Severity | Recommendation |
|---------|----------|----------------|
| Command inspector is a fixed `w-[300px]` floating panel (`command/page.tsx:544`); palette is fixed `w-[520px]` (`:582`) — both overflow small viewports | 🟡 Moderate | `max-w-[min(300px,90vw)]` or make dismissible/docked below `md:` |
| Approval routing rule form uses `grid grid-cols-2` with no mobile override (`approvals/page.tsx:568`) — cramped fields on phones | 🟡 Moderate | `grid-cols-1 md:grid-cols-2` |
| No breadcrumbs on detail pages (e.g. `/contracts/{id}`) — users deep in a record lose the trail back | 🟡 Moderate | Add a breadcrumb line above `PageHeader` on detail routes |
| Tables fall back to `overflow-x-auto` on mobile (`ui.tsx:244`) with no column priority | 🟢 Minor | Acceptable for a desktop-first legal tool; consider card layout for the 2–3 most-used tables |
| "Contract Brain" and "Command" are brand-y labels that rely on tooltips to disambiguate (`app-shell.tsx:43,52`) | 🟢 Minor | Fine for daily users; add one-line descriptions on the dashboard/onboarding for new users |
| Reject-approval flow requires a reason and confirms via modal (`approvals/page.tsx:310-359`) | ✅ Good | Keep — destructive action handled correctly |

### Visual hierarchy

- **What draws the eye first**: the 26px serif `PageHeader` title (`ui.tsx:385-407`), then StatCards with 28px serif values (`ui.tsx:472-519`). Correct — page identity, then key numbers.
- **Reading flow**: header → KPI row → table/content. Consistent top-down scan across jobs, signatures, renewals, approvals. Tables use comfortable density (`py-3` cells) suited to legal review work rather than data-ops cramming.
- **Emphasis**: the serif/sans/mono trichotomy (serif for titles and numbers, sans for body, mono for IDs) creates hierarchy without weight inflation. One caution: the scale leans hard on `text-xs`/`text-sm` — there's almost no mid-scale step, so long text-heavy panels (assistant, contract view) read flat. Consider `text-[15px]` body in reading contexts (already done in `contract-document.tsx:558` — extend that thinking).

### Consistency

| Element | Issue | Recommendation |
|---------|-------|----------------|
| Overlay padding | Modal body is `px-5 py-4` (`ui.tsx:450-466`) vs command inspector `p-4` (`command/page.tsx:544`) | Standardize overlay padding token across modal/inspector/popover |
| Buttons | 5 strict variants, zero hand-rolled duplicates found | ✅ None — unusually clean |
| Status colors | 18+ statuses map through a single `statusTone()` helper (`utils.ts:140-169`); "pending" is amber and "approved" is green on every page checked | ✅ None — exemplary |
| Dates | All pages use `fmtDate`/`fmtDateTime`/`fmtRelative` — no ad-hoc formatting found | ✅ None |
| Tables | All six list pages use the same `Table/TH/TD` primitives; only the command page has an intentional custom mini-table | ✅ None |
| Command page styling | The dark "command-canvas" aurora treatment (`globals.css`) is a deliberate divergence; ensure it stays contained to that route | 🟢 Watch it doesn't leak into new pages |

### Accessibility (summary — full audit in ACCESSIBILITY_AUDIT.md)

- **Color contrast**: placeholder text (slate-400 on slate-100) and brand-600 links fail 4.5:1; body text is fine in both themes.
- **Touch targets**: toast dismiss and some row-action icons are well under 44px.
- **Text readability**: 12–14px dominates; fine for chrome, thin for reading surfaces.
- **Critical**: modals lack `role="dialog"`, focus trap, and focus return; toasts lack a live region.

### What works well

- **Design system discipline** — strict Button/Badge/Card/Table variants with no rogue re-implementations across ~45 files; rare at this stage.
- **Centralized semantics** — `statusTone()` and the three date formatters mean statuses and dates can never drift between pages.
- **Complete state coverage** — every list page has skeleton loading, an `ErrorState`, and an actionable `EmptyState` with icon + CTA (`ui.tsx:315-382`); no dead-end blank pages.
- **Dark mode done properly** — CSS-variable slate ladder (`globals.css:13-61`) swaps the whole theme without per-component edits; the Command dark theme (teal on deep ink) is a genuinely distinctive touch.
- **Live validation UX** — submit buttons disable with an explanatory footer hint ("Add a review name to continue", `create-review-modal.tsx:164-172`) instead of letting users hit errors.
- **Navigation IA** — 12 items grouped into Workspace / Intelligence / Lifecycle with clear active states (`app-shell.tsx:36-69, 204-234`); secondary items (Jobs, Admin) correctly demoted to the footer menu.

### Priority recommendations

1. **Fix small-screen behavior of fixed-width panels and 2-col form grids** — the command inspector, palette, and approval rule form are the only places the otherwise-solid responsive strategy (1→2→3 column grids, sidebar drawer) breaks down. Cheap fixes, removes the worst mobile failures.
2. **Ship the accessibility critical set alongside this refinement pass** — dialog semantics + toast live region + focus rings. These are design-system-level fixes (`ui.tsx`, `toast.tsx`) that repair every page at once and belong in the same refactor.
3. **Add breadcrumbs to detail routes and standardize overlay padding** — the two remaining wayfinding/consistency gaps. A single `Breadcrumb` primitive and one padding token close them out.
