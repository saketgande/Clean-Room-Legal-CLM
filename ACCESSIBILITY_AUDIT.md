# Accessibility Audit: Aegis Legal CLM (frontend)

**Standard:** WCAG 2.1 AA | **Date:** 2026-07-07 | **Method:** Static code review of `frontend/src` (~45 tsx files) | **Stage:** Refinement

### Summary

**Issues found:** 22 | **Critical:** 6 | **Major:** 8 | **Minor:** 8

The app is hand-rolled (no Radix/Headless UI), which means every modal, dropdown, tab, and toast carries its own accessibility burden. Semantic HTML (tables, headings, forms) is generally solid, and `html lang="en"` is set. The biggest gaps are dialog semantics/focus management, live-region announcements, and a handful of contrast and focus-visibility failures.

---

### Findings

#### Perceivable

| # | Issue | WCAG | Severity | Recommendation |
|---|-------|------|----------|----------------|
| 1 | Toast dismiss button is icon-only with no `aria-label` (`toast.tsx:66-71`); the `X` icon is also not `aria-hidden` | 1.1.1 / 4.1.2 | 🟡 Major | `aria-label="Dismiss notification"` on the button, `aria-hidden="true"` on the icon |
| 2 | Several icon-only buttons lack accessible names (modal close in some pages; trash/remove-row buttons in signature and review modals) | 1.1.1 | 🟡 Major | Add `aria-label` (e.g. "Remove recipient") — note many others already have labels (27 `aria-label` usages found), so this is about closing gaps |
| 3 | Muted text pairs are contrast suspects: `text-slate-400` placeholders and hint text on `bg-slate-100` inputs; `text-xs text-slate-500` uppercase table headers | 1.4.3 | 🔴 Critical | Darken placeholder/hint to slate-500+; verify pairs with a contrast checker after the palette pass (CSS-variable-driven slate ladder in `globals.css:13-61`) |
| 4 | Brand teal links/accents (`brand-400 #2DD4BF`, `brand-600 #0D9488`) on white hover around the 3:1–4.5:1 boundary for small text | 1.4.3 | 🟡 Major | Use `brand-700` for text-sized links in light mode; reserve 400/600 for large text and non-text UI |
| 5 | Form labels rendered via `Field` wrapper without `htmlFor`/`id` association (`ui.tsx:214-236`) | 1.3.1 / 3.3.2 | 🟡 Major | Generate an `id` (`useId()`) in `Field` and wire `htmlFor` — proximity alone doesn't create a programmatic association |
| 6 | Status conveyed by badge color + text — text is present, so this passes; noted as a strength | 1.4.1 | ✅ Pass | Keep the `statusTone()` text+color pattern |

#### Operable

| # | Issue | WCAG | Severity | Recommendation |
|---|-------|------|----------|----------------|
| 1 | `Modal` (`ui.tsx:410+`) has no focus trap — Tab reaches background content while the modal is open | 2.4.3 / 2.1.2 | 🔴 Critical | Trap focus within the dialog (sentinel elements or a small util); Escape already works (`ui.tsx:425-430`) |
| 2 | Focus is not returned to the trigger element when a modal closes | 2.4.3 | 🟡 Major | Store `document.activeElement` on open, restore on close |
| 3 | `focus:outline-none` without any ring replacement on inputs in `contract-document.tsx:467,558,703,709,729` (border-color change only — insufficient) | 2.4.7 | 🔴 Critical | Add `focus:ring-2 focus:ring-brand-500/20` as the shared `Input`/`Textarea` primitives already do (`ui.tsx:174,189`) |
| 4 | Assistant composer textareas rely on parent container styling for focus indication (`assistant-workspace.tsx:838,1050`) | 2.4.7 | 🟢 Minor | Confirm the wrapper shows a visible focus style via `focus-within:`; add if absent |
| 5 | No skip link to main content in `app-shell.tsx` | 2.4.1 | 🟡 Major | Add a visually-hidden "Skip to main content" link as the first focusable element |
| 6 | Account menu / custom dropdowns have no arrow-key navigation or `aria-expanded` | 2.1.1 / 4.1.2 | 🟡 Major | Add `aria-haspopup`, `aria-expanded`, ArrowUp/Down + Escape handling, or adopt Radix `DropdownMenu` |
| 7 | Small touch targets: toast dismiss (`h-3.5 w-3.5` icon in an unpadded button), various `h-4 w-4` row-action buttons | 2.5.5 | 🟢 Minor | Pad hit areas to ≥44×44 CSS px (padding, not icon size) |
| 8 | Auto-dismissing toasts after 4.5s (`toast.tsx:31-35`) with no pause-on-hover | 2.2.1 | 🟢 Minor | Pause the timer on hover/focus; errors could persist until dismissed |

#### Understandable

| # | Issue | WCAG | Severity | Recommendation |
|---|-------|------|----------|----------------|
| 1 | Form/submit errors surfaced only via toast, not inline next to the offending field (import, signature, review modals) | 3.3.1 | 🟡 Major | Add inline error text under the relevant `Field`; keep the toast as a supplement |
| 2 | Live "disabled submit + footer hint" validation (`create-review-modal.tsx:164-172`) is good UX but the hint isn't announced to screen readers | 3.3.2 | 🟢 Minor | Give the hint `aria-live="polite"` or associate via `aria-describedby` |
| 3 | `html lang="en"` present (`app/layout.tsx:20`) | 3.1.1 | ✅ Pass | — |

#### Robust

| # | Issue | WCAG | Severity | Recommendation |
|---|-------|------|----------|----------------|
| 1 | No `role="dialog"` / `aria-modal="true"` / `aria-labelledby` anywhere in the codebase (grep: zero matches) — all modals are plain divs | 4.1.2 | 🔴 Critical | Add dialog semantics to `Modal` in `ui.tsx`; every consumer inherits the fix |
| 2 | Toast container has no `aria-live` region (`toast.tsx:50`) — notifications are invisible to screen readers | 4.1.3 | 🔴 Critical | `role="status" aria-live="polite"` on the container (`role="alert"` for error kind) |
| 3 | Tab-style controls (command page stations, workspace tabs) lack `role="tablist"/"tab"` and `aria-selected` | 4.1.2 | 🟡 Major | Add tab semantics + arrow-key switching |
| 4 | Loading states lack `aria-busy`; only `SkeletonRows` has `role="status"` (`ui.tsx:367`) | 4.1.2 | 🟢 Minor | Add `role="status"` to `CenterSpinner`; `aria-busy` on refreshing regions |
| 5 | Command inspector panel (`command/page.tsx:544`) is a fixed-position div with no landmark or dialog role | 4.1.2 | 🟢 Minor | `role="complementary"` + heading, or treat as non-modal dialog |
| 6 | `suppressHydrationWarning` on `<html>` is fine; no a11y impact | — | ✅ Pass | — |

---

### Color contrast check (light mode, key pairs)

| Element | Foreground | Background | Required | Verdict |
|---------|-----------|------------|----------|---------|
| Body text | slate-900 `rgb(17 24 39)` | slate-100 (white) | 4.5:1 | ✅ ~17:1 |
| Muted/hint text | slate-500 | white | 4.5:1 | ⚠️ borderline ~4.6:1 — don't go lighter |
| Placeholder text | slate-400 | slate-100 input bg | 4.5:1 | ❌ ~2.8:1 |
| Table headers (text-xs uppercase) | slate-500 | white | 4.5:1 | ⚠️ borderline |
| Primary button | white | brand-600 `#0D9488` | 4.5:1 | ⚠️ ~3.9:1 — acceptable for large/bold only; consider brand-700 |
| Brand links | brand-600 `#0D9488` | white | 4.5:1 | ❌ ~3.9:1 |
| Badge text (e.g. emerald-700 on emerald-50) | emerald-700 | emerald-50 | 4.5:1 | ✅ |
| Dark mode body | slate-900 var `rgb(232 240 248)` | `rgb(12 17 23)` | 4.5:1 | ✅ ~15:1 |

Ratios are computed from the extracted palette; re-verify after any palette change (the CSS-variable slate ladder makes a single fix propagate everywhere).

### Keyboard navigation summary

| Element | Tab order | Enter/Space | Escape | Arrows |
|---------|-----------|-------------|--------|--------|
| Modal (all) | ❌ not trapped, escapes to background | ✅ buttons work | ✅ closes | n/a |
| Account menu | ✅ reachable | ✅ opens | ❌ no close handling | ❌ none |
| Sidebar nav | ✅ links, visible focus via buttons | ✅ | n/a | n/a (acceptable for links) |
| Command palette (⌘K) | ✅ opens | ✅ | ✅ | Verify list navigation |
| Tables | ✅ row actions reachable | ✅ | n/a | n/a |
| Toasts | ⚠️ dismiss reachable but unlabeled | ✅ | ❌ | n/a |

### Screen reader summary

| Element | Announced as | Problem |
|---------|-------------|---------|
| Modal open | Nothing — no role/label, focus stays behind | Critical: users don't know a dialog opened |
| Toast | Nothing — no live region | Critical: async success/error invisible |
| Status badges | Text content ("Pending", "Approved") | ✅ OK |
| Icon buttons (labeled) | aria-label read | ✅ OK where present |
| Tabs/stations | Generic "button" | No selected state announced |

---

### Priority fixes

1. **Add dialog semantics + focus trap + focus return to `Modal` in `ui.tsx`** — one component fix repairs every modal in the app (Robust #1, Operable #1–2). Highest leverage change available.
2. **Make the toast container a live region and label its dismiss button** (`toast.tsx`) — all async feedback (imports, signatures, approvals) is currently invisible to screen-reader users.
3. **Fix focus visibility and placeholder contrast** — restore focus rings on the five `contract-document.tsx` inputs and darken `placeholder:text-slate-400` / hint text; these fail users with low vision app-wide.
4. **Skip link + `htmlFor` wiring in `Field`** — small, mechanical, high coverage.
5. **Consider Radix UI primitives** (Dialog, DropdownMenu, Tabs) for the hand-rolled components — eliminates whole classes of these issues permanently.

> Static analysis catches roughly the automated-scan tier (~30%) plus code-level keyboard/ARIA issues. Before sign-off, do a manual pass: keyboard-only run-through of import → review → signature flows, VoiceOver/NVDA on the same flows, and a 200% zoom check of the command page and tables.
