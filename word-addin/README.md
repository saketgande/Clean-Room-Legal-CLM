# Aegis Legal CLM — Word add-in

A Microsoft Word task-pane add-in that turns Word into a Legora-style assistant
for your Aegis CLM. The **input bar sits at the bottom** and the conversation
flows **above** it; quick-action chips (Review / Playbook / Obligations /
Summarize) sit just above the input. The open document **auto-links** to a
contract in the backend on open — it matches an existing contract by file
content, or creates one, with no manual "Link" step. Reviews render a
**risk-summary hero** plus prioritized issue cards. Clean and professional,
light + dark.

- **Command bar + chips** — `Review` the open contract (risk-ranked issue
  cards), run a `Playbook`, extract `Obligations`, `Summarize`, or type any
  question. Suggestions insert as **tracked changes**.
- **Locate** — every finding / redline selects and highlights the exact clause
  in the document.
- **Contract chip → panel** — link the open `.docx` to a contract record in
  Aegis (saved *inside* the document), see its lifecycle stage and **version
  history**, save the current draft as a **new version**, and accept/reject the
  contract's **proposed redlines** — all synced with the app.

Dark-first premium aesthetic, with a light toggle (top-right) that also follows
a clearly light Office theme.

```
word-addin/
├── manifest.xml        # the "address card" Word loads (XML add-in-only manifest)
├── server.mjs          # local HTTPS dev server + /api proxy (for Word)
├── preview-server.mjs  # plain-HTTP variant to preview the UI in a browser
├── package.json        # dev scripts + Office tooling
└── public/
    ├── taskpane.html/.css/.js   # the panel UI + Office.js logic
    ├── commands.html/.js        # ribbon command function file
    └── assets/icon-*.png        # ribbon / store icons
```

How it connects: the panel is served from `https://localhost:3001`. It calls
`/api/v1/...` on that same origin, and `server.mjs` proxies those calls to the
FastAPI backend — by default the Aegis VM at `http://10.1.128.137:8000` (override
with `AEGIS_BACKEND`, e.g. `http://localhost:8000` for a local backend). Keeping
everything on one HTTPS origin avoids browser CORS and mixed-content blocks
inside Word's webview, and lets the panel reach the VM's plain-HTTP API from an
HTTPS page.

Backend endpoints used: `app/word_addin/` (`/word/ping`, `/word/review`,
`/word/ask`) plus the existing CLM API — `/contracts`, `/contracts/{id}/versions`
(incl. the new **POST** to push a version from Word), `/contracts/{id}/edits`,
and `/playbooks/{id}/runs`.

---

## Prerequisites

- **Node.js 18+** (`node -v`).
- **The Aegis backend reachable.** By default the add-in proxies to the Aegis VM
  at `http://10.1.128.137:8000`, so you just need network access to it — nothing
  to start locally. To run against a **local** backend instead, start it and set
  `AEGIS_BACKEND`:
  ```bash
  cd ..            # repo root
  ENVIRONMENT=development ./start.sh backend
  AEGIS_BACKEND=http://localhost:8000 npm run serve
  ```
  Tip: to test the wiring without spending Claude tokens, start the backend with
  `MOCK_CLAUDE=true` — `/word/review` will return mock findings.
- **An Aegis account** (email + password) that is approved/active.
- **Microsoft Word** — desktop (Microsoft 365) on this Mac.

---

## Run it (sideloading, macOS)

First, one-time setup from this `word-addin/` folder:

```bash
npm install     # install the Office dev tooling
npm run certs   # install a trusted local HTTPS cert (may prompt for your Mac
                # password / keychain — say yes)
```

Then pick **one** of the two paths below (don't mix them — both start the
server, and running two would fight over port 3001).

### Option A — manual sideload (recommended, fewest moving parts)

```bash
npm run serve   # start the add-in at https://localhost:3001  (leave running)
```

In a second terminal, copy the manifest into Word's add-in folder:

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
cp manifest.xml ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/
```

Fully quit and reopen Word, then **Insert** → **Add-ins ▾** → **My Add-ins** →
**Developer Add-ins** → **Aegis Legal CLM**.

### Option B — one command

```bash
npm run sideload   # starts the server AND opens Word for you
```

Don't run `npm run serve` as well with this option.

---

Either way: in Word, open the **Home** tab → click **Contract review** (Aegis
group) to open the panel. Sign in, open or paste a contract, and click **Review
this contract**.

### Stopping

- `Ctrl-C` the server terminal.
- `npm run stop` to unregister a sideloaded add-in.

---

## Using the panel

The panel opens on the main co-pilot view: a command bar with quick-action
chips on top, results below.

1. **Sign in** with your Aegis email + password (calls `/api/v1/auth/login`). A
   short-lived access token is kept in memory; when it expires, the panel
   silently refreshes it via the same HttpOnly-cookie session the web app
   uses — no re-login unless the underlying session itself has actually
   expired or been revoked.
2. Use a **chip** or type into the **command bar** — every one of these calls
   the *same* backend the web app's Ask Aegis / risk / playbook engines use,
   not a separate implementation:
   - `Review` → `GET/POST /contracts/{id}/risk` + `/deviations`: the real
     weighted risk score and open playbook deviations for the linked contract.
   - `Obligations` / `Summarize` / any free-text question → a real, streamed
     Ask Aegis turn (`/assistant/sessions/{id}/stream`) with tool use, inline
     "Reading the contract…"-style status, and multi-turn memory across the
     conversation.
   - `Explain selection` → highlight a clause in the document first; this asks
     Ask Aegis about exactly that text, no typing required.
   - `Playbook` → pick a playbook, then either **run it** against the linked
     contract, or **browse its clause library** to insert a standard clause
     directly.
3. On any issue card / redline / deviation / clause:
   - **Locate** — selects, scrolls to, and briefly highlights the exact clause.
   - **Insert redline** — track changes on, replaces the clause with the
     suggestion (falls back to your current selection if it can't auto-locate).
   - **Insert clause** / **Comment** / **Copy** as applicable.
4. The **Track changes** toggle (in the command bar) controls whether edits are
   recorded as redlines. Leave it on so a reviewer can accept/reject each change.
5. In the contract panel, **"Save current draft as new version"** has a
   checkbox — **"This is the counterparty's redline coming back"** — check it
   when uploading a revision *they* sent, so Aegis logs it as a counterparty
   revision and automatically re-reviews it (fresh risk score + playbook pass)
   once analysis finishes, instead of a plain manual upload.

### Contract chip → panel

6. The chip under the header shows the linked contract (or **Link this
   document…**). Tap it to open the Contract panel and either **Save to Aegis**
   (uploads the `.docx` as a new contract) or **Link to an existing contract**.
   The link is stored inside the `.docx`, so it persists for anyone who reopens it.
7. In the panel: the contract's **stage**, **version history**, **Save current
   draft as new version** (`POST /contracts/{id}/versions`, or `.../counterparty-revision`
   when the toggle from step 5 is checked), **Open in Aegis**, and **proposed
   redlines** with **Locate / Apply in Word / Accept / Reject** (Accept/Reject
   sync to the repository).

The **moon/sun** button (top-right) switches light/dark; it defaults to your
Office theme.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| "Add-in could not be loaded" / certificate warning | Run `npm run certs`, then fully quit and reopen Word. |
| Panel shows `502 backend_unreachable` | The backend isn't running. Start it (`./start.sh backend`) or point at another with `AEGIS_BACKEND=http://host:port npm run serve`. |
| Sign-in fails | Confirm the account exists and is approved; check backend logs. The panel hits `/api/v1/auth/login`. |
| **Track changes** is greyed out | Your Word build doesn't support WordApi 1.4. Edits still apply, just not as redlines. |
| Nothing happens on "Insert redline" | The exact clause text wasn't found — select the clause in the document and click again (it falls back to your selection). |
| Want a different port | `PORT=4001 npm run serve`, and change `3001` → `4001` everywhere in `manifest.xml`. |

---

## Production hosting — already configured

The steps below aren't speculative future work — this is what `deploy/nginx/aegis.conf`
already does on the production VM, matching `manifest.xml`'s hardcoded
`https://aegis.ctpsandbox.com/word-addin/...` URLs:

1. **The panel is hosted, same-origin, already.** nginx's `location /word-addin/`
   block serves this directory's `public/` straight from disk
   (`/opt/aegis/word-addin/`) under the same origin as the API. Deploying an
   update is `rsync`/copy `word-addin/public/` to that path on the VM — there's
   no separate hosting setup to build.
2. **The API is already proxied same-origin**, not called cross-origin — the
   same `location /api/` block the Next.js frontend uses also serves this
   add-in, so there's no CORS configuration and no cross-site cookie
   complication for the silent-refresh session cookie described in "Using the
   panel" above; it behaves exactly like the web app's own session.

What's still genuinely open, if you want it:

3. **Auth:** swap email/password for Microsoft 365 SSO (Nested App
   Authentication) or the Office Dialog API against your login page — a real
   feature to build, not a config step.
4. **Distribute** via **Centralized Deployment** (Microsoft 365 admin center →
   Integrated apps → upload `manifest.xml`) for a whole firm, or **AppSource**
   for public self-serve.

## Ready for Microsoft Marketplace submission

What's done in this repo:

- `public/support.html` — a real support page (`manifest.xml`'s `SupportUrl` now
  points here instead of at the panel itself). **Replace its placeholder email
  before submitting** — a real one is required for certification.
- `public/privacy.html` — a privacy policy drafted from the add-in's actual data
  flows (auth, document content sent for AI review, what's stored where). It's
  marked as a draft throughout — **have it reviewed by legal/compliance before
  publishing**, and fill in the `[bracketed]` placeholders (your legal entity
  name, hosting region). This isn't optional: Microsoft's validation checks that
  the privacy policy specifically describes this app, not just a generic
  company page.
- `STORE_LISTING.md` — short/long description copy, a category suggestion, a
  shot list for screenshots, and a template for the mandatory reviewer test-
  account notes (this add-in uses email/password, not SSO, so a working test
  account in the certification notes is required or the submission auto-fails).
- Accessibility: fixed several text/background color pairs in `taskpane.css`
  that failed WCAG AA contrast (status messages, empty states, and — worst —
  dark-mode severity badges), and added `aria-label`s to the sign-in and
  command-bar inputs.

What's still yours to do — none of this is something to build, it's business/
legal ownership:

- Enroll in the **Microsoft 365 and Copilot program** via Partner Center (a
  developer/publisher account, separately from any Aegis account).
- Have counsel finalize `privacy.html` and decide on a EULA (Microsoft's
  standard EULA is the simplest default — a checkbox in Partner Center, no file
  needed — unless you want your own).
- Set the real support contact in `support.html`.
- Create a real test account for reviewers and fill in `STORE_LISTING.md`'s
  certification-notes template with it (don't commit real credentials to this
  repo — see the note at the bottom of that file).
- Take the actual screenshots (needs a live Word session, so it's not something
  automatable here) and write/paste the listing into Partner Center.
- Budget 4-6 weeks for review, and expect at least one round of feedback —
  that's normal, not a sign something's wrong.

## A note on `manifest.xml`'s WordApi version

The manifest declares `WordApi MinVersion="1.3"` (the floor Word needs to load
the add-in at all), while `taskpane.js` separately runtime-checks for WordApi
**1.4** before enabling Track Changes and Comments. That's intentional
progressive enhancement, not a bug: an older Word that only satisfies 1.3 still
loads the panel — it just gets those two features disabled gracefully (see the
troubleshooting table above). Don't "fix" this by bumping the manifest's floor
to 1.4 — that would stop the add-in from loading at all on older Word builds
for no benefit.
