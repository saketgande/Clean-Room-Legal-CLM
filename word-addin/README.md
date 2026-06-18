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
FastAPI backend at `http://localhost:8000`. Keeping everything on one HTTPS
origin avoids browser CORS and mixed-content blocks inside Word's webview.

Backend endpoints used: `app/word_addin/` (`/word/ping`, `/word/review`,
`/word/ask`) plus the existing CLM API — `/contracts`, `/contracts/{id}/versions`
(incl. the new **POST** to push a version from Word), `/contracts/{id}/edits`,
and `/playbooks/{id}/runs`.

---

## Prerequisites

- **Node.js 18+** (`node -v`).
- **The Aegis backend running** on `http://localhost:8000`:
  ```bash
  cd ..            # repo root
  ENVIRONMENT=development ./start.sh backend
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

1. **Sign in** with your Aegis email + password (calls `/api/v1/auth/login`).
2. Use a **chip** or type into the **command bar**:
   - `Review` → `/word/review`: risk-ranked issue cards for the open contract.
   - `Obligations` / `Summarize` / any free-text question → `/word/ask`: reads
     the document and answers inline.
   - `Playbook` → pick a playbook and run it against the **linked** contract.
3. On any issue card / redline / deviation:
   - **Locate** — selects, scrolls to, and briefly highlights the exact clause.
   - **Insert redline** — track changes on, replaces the clause with the
     suggestion (falls back to your current selection if it can't auto-locate).
   - **Insert clause** / **Comment** / **Copy** as applicable.
4. The **Track changes** toggle (in the command bar) controls whether edits are
   recorded as redlines. Leave it on so a reviewer can accept/reject each change.

### Contract chip → panel

5. The chip under the header shows the linked contract (or **Link this
   document…**). Tap it to open the Contract panel and either **Save to Aegis**
   (uploads the `.docx` as a new contract) or **Link to an existing contract**.
   The link is stored inside the `.docx`, so it persists for anyone who reopens it.
6. In the panel: the contract's **stage**, **version history**, **Save current
   draft as new version** (`POST /contracts/{id}/versions`), **Open in Aegis**,
   and **proposed redlines** with **Locate / Apply in Word / Accept / Reject**
   (Accept/Reject sync to the repository).

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

## Going to production (later)

This setup is for local development (sideloading). To ship to real users:

1. **Host the panel** (`public/`) on your own HTTPS domain instead of
   `localhost:3001`, and replace every `https://localhost:3001` in
   `manifest.xml` with that URL.
2. **Point at the real API.** Either keep a same-origin proxy, or call the
   backend directly and add your panel's origin to `CORS_ORIGINS` on the backend.
3. **Auth:** swap email/password for Microsoft 365 SSO (Nested App
   Authentication) or the Office Dialog API against your login page.
4. **Distribute** via **Centralized Deployment** (Microsoft 365 admin center →
   Integrated apps → upload `manifest.xml`) for a whole firm, or **AppSource**
   for public self-serve.
