# Docstudio — Build Plan

> **Status (2026-10-06): docstudio has been removed.** It was a stand-alone
> prototype for trying the editor; once the ONLYOFFICE editor moved into the
> contract page, the rest went. What survives: the reader (parsers, clause
> builder, tree, `carry_ids`) as `backend/app/documents/reader/`, used by every
> upload, and the AI hierarchy step as `backend/app/documents/hierarchy.py`,
> now through the AI gateway and not yet switched on. The `ds_*` tables
> (migrations 0045–0047) are still in the database until a drop migration is
> approved. The plan below is kept as history.


A ground-up rebuild of how AEGIS represents, displays, annotates and versions
legal documents.

**This is not a refactor.** Nothing in the existing document pipeline is reused,
extended or migrated into. Docstudio is a new subsystem with its own tables, its
own API, its own frontend and its own parsing. The old pipeline keeps running,
untouched, until Docstudio replaces it.

**Status:** Phase 1 is built, tested against real client documents, and has
had ten bugs found and fixed in it. `backend/app/docstudio/` — 2,238 lines,
migration `0045_docstudio_tables`, **111 tests**. Phases 2-5 are still plan only.
See §13 for what is built and §14 for what the real documents taught us.

---

## 0. Why from scratch

The existing pipeline's central decision is that **a contract is a string, and
everything points at character offsets into that string**. Clauses, citations,
comments and embeddings all anchor that way.

That decision is the cause, not a symptom. Every version rewrites the string and
silently invalidates everything pointing into it. You cannot fix that by
improving the code around it — the flaw is the data model, and it is baked into
`ContractTextSnapshot`, `ContractDocumentElement` and every annotation that
references them.

So: new model, new tables, built alongside. When it is better, switch over.

### What this costs, honestly

- You re-implement fuzzy text matching. (Small — `rapidfuzz` does the work.)
- You re-implement OOXML tracked-change writing. (~1,000 lines. There is a
  reference implementation to study — see §3.)
- **Two systems coexist** for a while. Plan the cutover (§9) from day one.
- More total work than patching. You are buying a correct foundation.

---

## 1. Scope

**In — built new:**
document representation · parsing and structure · clause identity · anchoring ·
annotations (comments, citations, proposals) · document versions · tracked
changes · the viewer and clause UI

**Out — deliberately not built:**

| | Why |
|---|---|
| **Authoring** — free-form typing with a cursor, bold, indents | the only feature needing a paid or AGPL editor. The Word add-in covers it |
| Real-time collaboration | needs a CRDT plus a sync server. Sequential editing is enough |
| Character-level anchoring | lawyers annotate clauses, not characters |

**Untouched — Docstudio talks to these across a narrow interface, and never
imports their internals:** authentication, users, organisations, permissions, and
whatever business record represents "a contract" to the rest of the app.

### The one interface to the outside world

Docstudio takes a file. It does not care where the file came from.

```python
# The ONLY thing Docstudio needs from the rest of AEGIS.
class FileSource(Protocol):
    def read(self, external_ref: str) -> tuple[bytes, str, str]:
        """Return (content, filename, mime_type)."""
```

One adapter implements this against existing storage. That is the entire coupling
surface. Keep it that way — no other import from `app.contract_files`,
`app.contracts` or `app.contract_brain`.

---

## 2. What we are building, in one paragraph

A screen that shows a contract **as it actually looks**, lets you click any clause
to see its comments, citations and risk, lets you **propose** new wording, and
turns accepted proposals into a **new version carrying real Word tracked
changes**. Annotations stay attached to their clause even after the text changes,
because each one stores three independent ways to find itself again.

---

## 3. Research this is built on

Read these before Phase 2. They are why the design looks like this.

| Source | Contribution |
|---|---|
| [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) | the standard: store **several** selectors per annotation, not one |
| [Hypothesis — Fuzzy Anchoring](https://web.hypothes.is/blog/fuzzy-anchoring/) | the fallback ladder, proven against a web that changes underneath it |
| [Peritext, CSCW 2022](https://www.inkandswitch.com/peritext/) | why character positions fail; keep annotations outside the text |
| [Yjs relative positions](https://docs.yjs.dev/ecosystem/editor-bindings/prosemirror) | a stable anchor is "like a commit SHA, not a position that shifts" |
| [ProseMirror guide](https://prosemirror.net/docs/guide/) | marks vs decorations — comments must not live inside the document |
| [CKEditor track changes](https://ckeditor.com/docs/ckeditor5/latest/features/collaboration/track-changes/track-changes.html) | a suggestion is a record with its own id, stored in your database |
| [Relativity viewer](https://help.relativity.com/RelativityOne/Content/Relativity/Viewer/Viewer.htm) | keep both representations and say openly that they differ |
| [PAWLS, ACL 2021](https://aclanthology.org/2021.acl-demo.31/) | flattening a PDF loses which words were headings |
| [OpenContracts](https://github.com/Open-Source-Legal/OpenContracts) | parsing as a swappable stage, never welded into upload |
| [Docling technical report](https://arxiv.org/html/2408.09869v1) | layout-aware structure extraction, when a parser upgrade is wanted |
| [Magic Markup, arXiv 2024](https://arxiv.org/abs/2403.03481) | LLM re-anchoring reaches ~90%. **No anchoring is perfect** |
| [Apryse: native OOXML vs HTML](https://apryse.com/capabilities/docx-editor/comparison) | HTML round-trips destroy multi-level legal numbering |
| Measured in this deployment | a configured OCR provider that 403s on every file will shadow a working one forever if the choice is "is it configured" rather than "did it work" |
| `mike-main/backend/src/lib/docxTrackedChanges.ts` | 1,179 lines proving the free server-side tracked-change model works. **Read this before Phase 5** |

---

## 4. Core design

### 4.1 Four layers

```
1. SOURCE FILE      the .docx / .pdf bytes. Never edited in place.
                    Content-addressed by SHA-256. The legal record.
        │
2. STRUCTURE        ordered clauses. Each has a permanent clause_id,
                    its number ("2.1(a)"), level, type, and text.
        │
3. FLAT TEXT        derived from structure, for AI and search.
                    READ-ONLY. Safe to delete and rebuild at any time.
        │
4. ANNOTATIONS      comments, citations, proposals, risks.
                    Three anchors each. Never offsets alone.
```

The rule that makes this different from what exists today: **layer 3 is
disposable.** Re-parsing a document must never break an annotation.

### 4.2 Three anchors per annotation

From the W3C model:

```jsonc
{
  "clause_id": "cl_7f3a91c4",
  "quote": {
    "exact":  "Neither party shall be liable for indirect damages",
    "prefix": "LIMITATION OF LIABILITY. ",      // ~32 chars before
    "suffix": " Aggregate liability shall not"   // ~32 chars after
  },
  "position": { "start": 4312, "end": 4380 }     // fast path; may go stale
}
```

`prefix`/`suffix` are what distinguish two identical sentences in one contract.
Hypothesis uses 32 characters — start there, measure, adjust.

### 4.3 The re-anchoring ladder

Runs after every new version. **Stop at the first rung that succeeds.**

```
1. clause_id exists AND its text is unchanged      → done, instant
2. position still lands on the stored exact quote  → done, fast
3. fuzzy match the quote WITH prefix/suffix        → done, rewrite position
4. fuzzy match the quote alone                     → done, mark "moved"
5. nothing matched                                 → mark ORPHANED
```

**Rung 5 is a feature.** An orphan goes to a "needs re-linking" queue a human
resolves. It is never silently dropped — silent dropping is the failure pattern
this whole rebuild exists to end.

**Log which rung every re-anchor lands on.** That distribution tells you how good
your clause identity actually is, and it is the number that decides §8 ①.

### 4.4 Two surfaces, one writer

```
┌──── DOCUMENT ───────────────┬──── CLAUSE PANEL ───────┐
│  the real file, rendered    │  selected clause:       │
│  READ-ONLY                  │  text · comments ·      │
│  click a clause to select   │  citations · risk ·     │
│                             │  proposals              │
└─────────────────────────────┴─────────────────────────┘
                 ↓
      accepted proposals → server writes a NEW .docx
```

Nothing types into the document. **Proposals are the only way text changes.**

---

## 5. Module layout

### Backend — `backend/app/docstudio/`

```
docstudio/
├── models.py        ds_* tables (§6)
├── schemas.py       API shapes
├── routes.py        thin handlers
├── service.py       orchestration
├── parsing/
│   ├── base.py      Parser protocol: bytes -> ParsedDocument
│   ├── pdf.py       PyMuPDF — text, boxes, pages, page-furniture stripping
│   ├── docx.py      python-docx — paragraphs, tables, numbering
│   ├── text.py      plain text + OCR output -> blocks
│   └── registry.py  mime -> parser. Swappable. NOT an if/elif in upload
├── ocr.py           scanned pages -> text. Provider protocol + fallback
├── structure.py     ParsedDocument -> clauses with ids and numbers
├── anchoring.py     the three-anchor model + the five-rung ladder (pure)
├── annotations.py   create, re-anchor, the lost queue, re-link (the database side)
├── versions.py      carry clause ids across versions; later: apply proposals
├── tracked.py       write <w:ins>/<w:del> into a .docx
└── filesource.py    the one interface to the rest of AEGIS
```

No imports from `contract_files`, `contracts` or `contract_brain`. If you need
something from them, it goes through `filesource.py` or it gets re-implemented.

### Frontend — `frontend/src/features/docstudio/`

```
docstudio/
├── DocstudioWorkspace.tsx    the screen
├── DocumentView/
│   ├── index.tsx             dispatch on mime type
│   ├── PdfRenderer.tsx       pdf.js: canvas + transparent text layer
│   ├── DocxRenderer.tsx      docx-preview
│   └── SimpleRenderer.tsx    images, plain text, unsupported
├── ClausePanel/
├── api.ts                    typed client for /docstudio/*
└── types.ts
```

New folder. No import from the existing contract components.

---

## 6. Data model

New tables, `ds_` prefix, no foreign keys into existing document tables.

```
ds_document          one logical document
  id, external_ref, title, created_at
  current_version_id

ds_version           an immutable snapshot of the file
  id, document_id, version_number
  sha256, mime_type, filename, byte_size
  parser_name, parser_version          -- which parser produced the structure
  created_at, created_by
  UNIQUE(document_id, version_number)

ds_clause            one clause of one version
  id                                   -- row id
  clause_id                            -- STABLE identity, carried across versions
  version_id, seq, parent_clause_id
  number_label        "2.1(a)"
  level               1, 2, 3...
  clause_type         heading | clause | table | signature
  text
  char_start, char_end                 -- into this version's flat text
  page_number, bbox                    -- PDF only; populate from day one
  INDEX(version_id, seq), INDEX(clause_id)

ds_annotation        comment | citation | proposal | risk
  id, document_id, version_id, kind
  anchor_clause_id
  anchor_quote_exact, anchor_quote_prefix, anchor_quote_suffix
  anchor_start, anchor_end
  anchor_state        ok | moved | orphaned
  anchor_rung         1..5                  -- which rung last resolved it
  body                                      -- comment text / citation / rationale
  proposed_text                             -- proposals only
  status              open | accepted | rejected | resolved
  parent_annotation_id                      -- threading
  author_kind, author_user_id, author_name
  created_at, resolved_at
  INDEX(document_id, anchor_state)

ds_event             append-only audit of everything that happened
  id, document_id, version_id, event_type, details, actor, created_at
```

Notes:

- **`clause_id` is not a content hash.** A hash changes when the text does, which
  is exactly when you most need identity to survive. Generate it fresh on first
  sight and carry it forward (§8 ①).
- `bbox` and `page_number` are populated **from day one**. PyMuPDF returns them
  for free; not storing them is how the current system ended up unable to
  highlight a citation on a page.
- `parser_name` / `parser_version` on the version row is what makes re-parsing
  safe and auditable.

---

## 7. Phases

Each phase ships on its own. Do not start one before the previous works.

---

### Phase 1 — Parse and store  ✅ BUILT

**Goal:** a document goes in, a structured document comes out, nothing is
displayed yet.

> Built and tested against real client documents. The tasks below are the
> original plan; **§13 is what actually exists** and **§14 is what the real
> documents changed about it**. Two things below were understated: page
> furniture had to be detected before OCR could be judged at all, and reading
> the file format correctly turned out to be harder than anything to do with
> clause patterns.

**Tasks**

1. `ds_document` + `ds_version` tables; ingest via `FileSource`.
2. Parser protocol and registry — mime type to parser, config-driven.
3. PDF parser (PyMuPDF): text, per-word boxes, page numbers.
   - PyMuPDF measures real glyph advances. Simpler PDF libraries reconstruct
     spaces from positions and split words apart on documents that set
     character spacing — a failure mode that scores *high* on naive quality
     checks, so it passes unnoticed.
4. DOCX parser (python-docx): paragraphs **and tables** in document order, plus
   **reconstructed automatic numbering**.
   - Word does not store "2.1" anywhere. It stores "item of list N at level L"
     and counts while drawing the page. You must resolve `numPr` on the
     paragraph **and** walk the style's `basedOn` chain, read formats from
     `numbering.xml`, and count statefully — incrementing a level resets deeper
     ones. Without this a numbered contract has no clause boundaries at all.
5. `structure.py` — clauses with `clause_id`, `number_label`, `level`, offsets,
   and (PDF) `bbox`.
6. **Strip page furniture.** A line repeating on most pages is a running
   header, footer or e-signature stamp, not content. Normalise digits before
   comparing, or "Page 3 of 7" looks unique on every page.
   - This is load-bearing, not tidiness. A real 14-page scanned MSA carried a
     DocuSign envelope stamp on every page; those 14 identical lines were **798
     of its 860 characters**, enough to clear a naive "is there text here?"
     check. The file parsed cleanly, warned about nothing, and produced sixteen
     "clauses" of which fourteen were the same stamp.
7. **Detect that a document needs OCR** — measured against content only, after
   furniture is removed. The parser reports `needs_ocr`; it does not decide what
   to do about it.
8. **OCR step.** When `needs_ocr` is set, read the pages through an OCR
   provider and use that text instead.
   - Try **every** configured provider in order, not just the best-configured
     one. "Enabled" means "has credentials" and says nothing about whether it
     works.
   - Record **every** provider's failure reason. An operator looking at an empty
     contract has to tell "one key is wrong" from "this document is genuinely
     unreadable".
   - Record the provider in `parser_name`, because it is what produced the text.
   - On total failure, **keep the original warnings**. A document nobody could
     read must not quietly become a short document.
   - Skip it entirely when the document already has text. OCR is charged per
     page.
   - OCR returns a string, so page numbers and coordinates do not survive it.
     Carry none rather than the ones belonging to the text just discarded.
9. Flat text derived from clauses, stored on the version.

**Acceptance**
- A real client PDF and a real client `.docx` both produce sensible clauses.
- An auto-numbered Word contract yields numbered clauses, not one giant block.
- Offsets round-trip: `flat_text[c.char_start:c.char_end] == c.text` for every
  clause. Assert this — it is cheap and catches most parser bugs.
- `bbox` populated for every PDF clause.
- A scanned PDF is **detected**, sent to OCR, and comes back with real clauses.
- A scanned PDF whose OCR fails still says it is unreadable.
- A repeated page header does not appear in the clause list.

**Risks**
- Real client paper is far messier than generated test files. **Test on genuine
  contracts.** A test corpus of documents your own system generated proves
  nothing, because they satisfy your own assumptions by construction.

---

### Phase 2 — Anchoring  ✅ BUILT

**The foundation.** Everything else depends on it. Built and measured — §18.

**Tasks**

1. `ds_annotation` with the three-anchor shape.
2. `anchoring.py` — one `resolve()` function implementing the five-rung ladder.
   **Every consumer calls it.** Never let a caller invent its own matching.
3. `create_annotation()` captures all three anchors at creation time.
4. Orphan queue: list and re-link UI.
5. Record `anchor_rung` on every resolve, and expose the distribution.

**Acceptance**
- Reword a clause → its comments still attach (rung 3).
- Insert a paragraph **above** a comment → it still attaches. This is the classic
  offset-shift failure; test it explicitly.
- Delete a clause → its comments become `orphaned` and **appear in the queue**.
- A duplicated sentence resolves to the right one, because of prefix/suffix.

---

### Phase 3 — Document view, read-only  🟡 BUILT, CHECKED — 2 GAPS OPEN

**Checked 2026-09-21 on the dev page**, which draws with the app's exact
libraries, versions and options (`devui.py`, `vendor/`):

| Check | File | Result |
|---|---|---|
| A scan displays | Lockton MSA, 18 scanned pages | ✅ every page, sharp at 2× density; says plainly it has no words |
| Hidden text sits on the printed words | typed NDA, 2 pages | ✅ 17 words across both pages vs PyMuPDF's boxes: left/right edge within 0.6 pt (median 0.1), about 0.5 pt low (worst 1.0) on ~12 pt words |
| Word draws as Word does | `software-license-agreement.docx` | ✅ two columns, table, every tracked insertion and deletion · ❌ **numbering** (below) |

Fixed in the app's viewer because of these checks: the canvas was painted at
CSS size, so blurry on every 2× screen; the text layer lacked pdf.js's
`markedContent` rule, which a tagged PDF (most Word exports) needs.

**Gap 1 — docx-preview's numbering is one low for a list that starts at a
sub-level.** The contract types "9.1" by hand and continues with an automatic
list declaring level 0 starts at 9 and level 1 at 2. Word shows 9.2 (a level
never used shows its start value), and so does our parser; docx-preview shows
**8.2**, because its CSS counters begin at start − 1 and only a level-0 item
would step them. Fix: show the parser's number on each rendered paragraph —
which needs the rendered-paragraph → clause link Phase 4 builds for selection.

**Gap 2 — a scan has no text layer: paragraph-level built on the dev page
(2026-09-21), not yet in the app's viewer.** pdf.js reads the file's own words,
and a scan has none (Lockton, Hearst and Employment: 0 words each). Now, on a
page with no words of its own, each OCR paragraph is laid invisibly over its
box, sized to fill it (`ocr.page_text` → the dev page's `layOcr`). On the
Lockton scan: all 170 paragraphs on 18 pages placed; search finds
"Severability" on page 17; selecting clause 16.17 paints its two printed lines.
Limits, by design: the words sit in the right paragraph, not on each printed
word (word boxes would fix that); they are only as right as the OCR (the
handwritten "Said" reads "Saif" underneath — the picture still shows the
truth). Only printed words go in: the provider's markup is stripped by name,
because templates print fill-ins such as `<Date>`, and "Figure" blocks are
left out, because they are the provider's *description* of a picture.
A natively read PDF with one scanned page (a signed signature page) still gets
nothing on that page — OCR runs per document, not per page.

Not looked at: the app screen itself (it needs a sign-in in the browser pane),
though it now runs the same drawing code as the dev page.

**Goal:** the contract on screen, looking like itself.

**Libraries** — both MIT, no licence decision:
- `pdfjs-dist` — canvas plus a transparent text layer, so text stays selectable
  and searchable while looking exactly like the page.
- `docx-preview` — pass `renderChanges: true` so existing Word tracked changes
  display.

**Tasks**

1. `GET /docstudio/versions/{id}/file` — authenticated bytes.
   Serve as `Content-Disposition: attachment` and parse the bytes in JS. **Never
   hand a blob to an `<iframe>`** — an HTML or SVG file wearing an allowed MIME
   type would execute in your origin.
2. Renderer dispatch by mime:

   | MIME | Renderer |
   |---|---|
   | `application/pdf` | pdf.js |
   | `...wordprocessingml.document` | docx-preview |
   | `image/png`, `image/jpeg` | `<img>` |
   | `text/plain` | `<pre>` |
   | anything else | honest "cannot display" panel + download |

   Match image types **by exact name**. A `startsWith("image/")` test also
   accepts SVG, which is a scripting format wearing an image MIME type.
3. A `Text | Original` toggle. Follow Relativity: keep both, and state plainly
   that they can differ.
4. **Resolve which version to show from stored bytes, never from extracted
   text.** The tempting shortcut — "the version whose text is non-empty" — hides
   the original exactly when parsing failed, which is when it is most needed.
   With no text, open on Original.

**Acceptance**
- A real client PDF renders identically to opening the file.
- A scanned PDF with no text still displays.
- Reveal the pdf.js text layer temporarily (colour the spans red): every word
  must sit exactly on the drawn text.

**Risk:** docx-preview fidelity on real client paper is unverified. Spend a day
on genuine contracts before building on it.

**The dev page draws with the app's viewer code** (`devui.py` + `vendor/`),
because that page is where every phase is tried and a second sign-in to look
at the same file is friction with no purpose. It first framed the file for the
browser's own viewer, which showed the page but checked nothing: the browser's
viewer is not the one the product ships, and a page cannot see what is
selected inside it. Now pdf.js 4.10.38, docx-preview 0.3.7 and JSZip 3.10.2 —
the frontend's exact builds — are vendored and served from the backend, so the
page loads nothing remote. Its policy allows script from this server and its
one inline script by hash, never inline or remote script or eval; styles are
`'unsafe-inline'` because docx-preview writes the Word file's own stylesheet
into the page as it draws. The file route serves `attachment`, like the
product's: the viewer draws the bytes, the browser never renders them.

The page also opens a document already read (`GET /dev/documents/{id}`), rather
than showing one only in the seconds after a run — the viewer was there and
invisible, which is the same as absent. The page is served `no-store`: it
changes under the developer reading it.

Dedup (`service.ingest`) now backfills `storage_key` when the same bytes come
back for a version stored before files were kept — otherwise the early return
meant such a version could never get its original, however often it was re-run.

---

### Citations on the page — "Ask this document" (dev page)  ✅ BUILT 2026-09-21

The part of Mike, Harvey and Legora that makes them feel the way they do: ask a
question beside the document, get an answer whose every point is cited, click
a citation and the page lights up where it is printed. Built on the dev page
(`ask.py`, `POST /docstudio/dev/ask`); not yet in the app, and not yet Ask
Aegis — which still reads the old pipeline (§9).

* **One document, sent whole.** A contract fits the model with room to spare
  (Lockton: 147 clauses), so there is no retrieval step to miss a clause.
  Refused above 300,000 characters, saying so.
* **Every citation is checked before it is shown.** Its words must be in the
  clause it names — compared with markup, entities, curly quotes, dashes and
  whitespace ignored. Words in the wrong clause are re-pointed to the one
  clause that holds them; words in none are shown marked "not verified".
* **Labelled as a lawyer cites it.** OCR'd contracts number the heading, not
  the paragraph under it, so a paragraph is labelled by its nearest numbered
  ancestor in the tree: "16.16", not "This Agreement is entered…".
* **Lit up by the clause's stored boxes** (`source_regions`) — a box per page
  piece, so a clause rejoined across a page break lights up on both pages. A
  Word or text file has no page geometry; there the quoted words are found in
  the drawn text and selected.
* **Citations are flat strings, `#50 | exact words`.** A list of objects came
  back from the model as its own call syntax (`<parameter name="citations">…`)
  on a real contract; the list inside is read from the first `[`, and a reply
  with no list in it shows none and says so, instead of one empty citation per
  character.

Measured on real files: Lockton (scan) — 4 of 4 citations verified, and the
answer found a real conflict: governing law Missouri (p15) against New York for
disputes (16.16, p17). Software licence (Word, tracked changes) — 2 of 2
verified, both 8.3, the quoted words found in the drawn document.

Known limits: paragraph-level on scans (as the text layer); one version at a
time; answers are not stored — nothing yet records who asked what.

### Comments in the margin, as Word takes them (dev page)  ✅ BUILT 2026-09-21

Select words on the drawn page, click **Comment**, and a card opens in a margin
beside the page, level with the words; saved, the words stay highlighted.
Clicking a card lights up its words, clicking the words lights up the card, and
Delete asks once before it acts. Built on Phase 2's annotations, so a comment
follows its words into every later version like any other note.

* **A selection is found in the stored text with everything but the words
  ignored** (`annotations.locate`): OCR markup, `&amp;`, curly quotes, and the
  line breaks pdf.js joins with no space. The text either side of the
  selection decides between identical passages; with nothing to tell them
  apart the comment is refused rather than attached to a guess.
* **The page finds its comments again the same way** (`page.js` `findRange`),
  from the quote and context the anchor stores — nothing geometric is saved,
  so zoom, a resized panel or a new version cannot misplace a highlight.
* **Exact words on typed PDFs and Word files; the paragraph on a scan**, whose
  words are laid by paragraph (Phase 3, gap 2).
* **Deleting is recorded** (`annotation.deleted` on the event log), unlike an
  orphan, which is never deleted at all.
* Struck-through words in a Word file are drawn but are not its text, so they
  cannot carry a comment, and the page says so.
* The margin is always beside the page, as in Word: the page shrinks to the
  room left (a Word page too, drawn at paper width and zoomed to fit), the
  margin narrows from 260px to 180px in a narrow panel, the panel never goes
  below 600px by drag, and the three columns never overflow the window.
  Measured: 1180px window — page 334px, margin 180px; 1920px — page 622px,
  margin 260px; no sideways scroll, every card level with its words.

**Threads, as Word keeps them** (2026-09-21): choosing a card shows a Reply
box; replies sit under their comment; ✓ resolves the thread (grey card, faint
underline, Reopen); deleting a comment takes its thread, deleting a reply only
itself. A reply is an annotation with `parent_annotation_id` and **no anchor**:
it goes wherever its comment goes, so re-anchoring and the lost-notes list skip
it — put through the ladder it would come out orphaned on every new version.
Replying to a reply joins the same thread (Word's are one level deep). Replies,
resolving, reopening and deleting are all on the event log.

Not yet: authors (the dev page has no sign-in: everyone is "You"), editing a
comment, and the app's viewer.

### Phase 4 — Clause panel

**Tasks**
1. Clause selection in the document view — text layer for PDF, rendered elements
   for DOCX.
2. Selection to `clause_id`.
3. Panel: clause text and number, comments, citations, risk, proposals, history.
4. Clicking a panel item scrolls to and highlights the clause.
5. Comment creation captures all three anchors.

**Acceptance**
- Clicking clause 4.2 shows only its annotations.
- Clicking a comment scrolls the document to its clause.
- A clause with nothing attached shows a clean empty state.

---

### Phase 5 — Proposals and versions  ✅ BUILT ON THE DEV PAGE 2026-09-21 (redlining)

**Built as Word works, not as first written below.** A suggested edit *is* a
real `<w:del>`/`<w:ins>` tracked change in the file, written at once as the
document's next version; accepting or rejecting it resolves it in the file,
again as a new version. The design below kept proposals outside the file until
an "apply" step, which meant accepting twice — once to apply, once for the
tracked change it became. One concept instead: every change, whoever made it —
you, the AI, or the other side's own tracked changes in a Word file they sent —
is listed, accepted and rejected the same way.

* `redline.py` — the engine, after Mike's `docxTrackedChanges.ts`: matches the
  accepted view of each paragraph, marks only the words that changed (word
  level: "twelve (12) months" → "twenty-four (24) months" marks the numbers,
  not "months"), splits runs by their own children so tabs and breaks survive,
  and gives new words their neighbours' formatting. Refuses — never overwrites
  — words inside someone else's change, and edits across two paragraphs.
  Resolving handles insertions, deletions, moves, paragraph marks (accepting a
  removed mark joins the paragraphs, as Word does) and formatting changes. A
  replacement is one review item whichever order the file stores its two halves.
* Each action is `ingest` of the new bytes as the next version, so clause ids
  carry and every comment is re-found — a redline is never a special case.
* `drafting.py` — the AI marking up: an instruction becomes edits (clause, exact
  old words, new words, a margin reason), each checked against the contract as
  citations are, written by "AEGIS AI". Reasons live on the event log keyed by
  change id *and date*, since a Word file has nowhere to put them and an id can
  be reused once an earlier change is accepted away.
* A PDF or text file cannot hold tracked changes: **Make an editable Word
  version** makes one as the next version; the PDF stays as the version
  before. A typed PDF keeps its look (below); one that fails its checks, and a
  text file, get their clauses' words only, with a warning saying why.
* **A scan is not redlined** (decided 2026-09-21, as Harvey, Legora, Ironclad
  and Mike work): it is almost always the signed contract, which changes by a
  separate amendment, and a Word copy would carry every OCR misreading as its
  text. The page shows the scan with its OCR text layer for comments and Ask,
  offers no Suggest edit, and says why in the Changes tab.
* The dev page: **Suggest edit** beside **Comment** on a selection (type over the
  words, or delete them); a **Changes** tab with Accept/Reject per change,
  Accept all/Reject all (asked once), and "Ask the AI to redline"; clicking a
  change finds it on the page. Download gives the .docx with its redline.

Measured on real files: the software licence's 16 tracked changes (the other
side's) list as 9 review items and resolve singly or all at once; an AI
instruction produced 2 correct edits with reasons; the downloaded file carries
the remaining change. **Opened in Microsoft Word itself (2026-09-21), which
found a bug python-docx never would:** saving tidied away the namespace
declarations that `mc:Ignorable` names, and Word called every redlined file
"unreadable". Fixed in `_write`, with a test; a converted and redlined contract
now opens clean, the change struck through in red with a bar in the margin.
Files redlined before the fix are still stored broken — redlining them again
writes a good one. **Not yet:** headers, footers and footnotes; edits across
paragraphs; a version history to step back through; the app's viewer.

#### A typed PDF as a Word file that keeps its look (`redline.layout_copy`)

pdf2docx (MIT; runs on PyMuPDF, AGPL; pulls in opencv, ~90 MB; **no longer
maintained**, so pinned at 0.5.13) rebuilds each page — fonts, sizes, bold,
page size and margins, ruled tables, pictures — then five of its faults found
on real contracts are repaired, each with a test that fails without it:

| Fault, as measured | Repair |
|---|---|
| "Stream tables" rebuild layout from invisible tables: one word per cell of a sentence | turned off |
| A justified line comes out with a tab between every word (83 lines in one contract) | words under 20pt apart are one line |
| A space written as its own span is dropped: "1.DEFINITIONS" 41 times in the licence | kept (patches `Spans.restore`) |
| A line gap 1.1pt off the page's usual one starts a new paragraph: recitals one paragraph *per line*, which does not re-wrap when edited (115 sentences split in IT Services) | a line stopping mid-sentence joins a next line starting lower-case; the gap becomes line spacing, the last line's space-after is kept, indents come from the PDF's own coordinates (a clause's "(1)" hangs), and it is justified — pdf2docx calls any lone full-width line "centred" |
| A link written inside a run: not valid Word, and its words read by nobody | moved out |
| Fonts named as PostScript does ("ArialMT", "CIDFont+F1") — Word draws its default font | the family from the font file inside the PDF ("CIDFont+F1" is Times New Roman), else from the name |

And two checks, where a Word copy would be wrong rather than ugly — failing
either gives the words-only copy and says why: **98% of the PDF's words** in the
file (measured 99.0–99.9%), and **90% of them in our reader's order** (97–100%
on single-column contracts; **68% on a two-column licence** whose columns
pdf2docx interleaved line by line). Each page's bottom margin is its whole
empty space, not half: a 36-page contract went 48 → 42 pages in Word, the
6-page TCS agreement to exactly 6. `ignore_page_error` is off: a page that
fails to convert fails the conversion instead of silently vanishing.

**Verified in Microsoft Word** on the TCS agreement (stamp image, Calibri,
double spacing, the two signature lines side by side, justified recitals, 6
pages as the PDF) and the 36-page IT Services agreement (ruled tables, bold
defined terms, italics, hanging "(1)" clauses; 42 pages — a few PDF pages run
slightly long in Word, and each spills onto a page of its own). Edits land as
often as in the words-only copy (29/40, 27/31 on random sentences).

On the dev page the Original tab offers **Beside the PDF**: the PDF version it
came from and the Word version, page beside page. docx-preview starts a page at
every section break and draws no columns, so the Word side runs longer there
than in Word — the page says so. `@font-face` aliases Calibri to itself or a
local stand-in: without Office's fonts the browser drew Calibri in its serif.

Not kept: page headers and footers stay body text on each page (Word's own
header/footer is not rebuilt); a heading number can land at the end of the line
above it; lone full-width lines not followed by a lower-case line stay
"centred" (identical unless Word wraps them differently).


#### Editing, as in a document editor (dev page, 2026-09-22)

**Two engines under one screen**, decided after research (Juro keeps uploaded
PDFs text-uneditable; Ironclad converts a PDF to Word to edit it, warning of
formatting loss; Harvey and Legora edit in Word; Acrobat reflows only inside one
text box). A PDF is a printout and cannot re-flow, so:

* **Word files** are edited in the file: Suggest edit, **Edit clause** (retype a
  clause; only the words that changed become tracked changes — `clause_edits`),
  and **B / I / U / Highlight** as tracked formatting changes (`w:rPrChange`,
  schema order kept, toggles like Word's buttons, reject restores exactly).
* **PDFs** take **marks** beside the page (`marks.py`: proposal annotations,
  anchored like comments, never in the file) — Suggest edit, Edit clause, the
  AI's "Suggest edits", Highlight — agreed, rejected or reopened in the Changes
  tab. The agreed marks become, by the reader's choice:
  1. **a marked-up PDF** — standard StrikeOut / Caret / Highlight / note
     annotations as an incremental update: the file begins with the original's
     exact bytes (checked), so a signature over them still verifies;
  2. **a Word version** with the marks as tracked changes by their authors
     (typed PDFs; `carry`, one new version, reasons kept);
  3. **words written into the PDF** — only inside the text instruction that
     draws the old words (`pdftext.py`), a spacing adjustment holding all that
     follows in place, new letters only those the file already prints in that
     font; refused unless it fits its line and leaves at most a space's gap.
     Then read back: pixel-identical outside the changed words, every other
     page identical, **our own reader** sees the original with exactly the
     agreed words changed and the same blocks, original bytes kept — or nothing
     is written. The first version (new text added at the end of the page)
     passed the pixel check and was read by our reader as "…within days of
     invoice." plus "forty": reading order is what the final check exists for;
  4. **an amendment** (.docx) listing each agreed change clause by clause — the
     only output for a scan.
* **History**: every version with what made it; view any read-only, download
  it, **compare** it with the latest by words shown in the latest's clauses (a
  PDF and its Word version split paragraphs differently; a split is not a change).

Measured on real Word-made contracts: TCS "Twelve"→"Eleven" and IT Services
"must"→"shall" written in place and passing every check; "must"→"will" refused
(a visible gap), "a"→"the" refused (no room), "Orica" refused (378 places),
words in the definitions table sent to cell-by-cell suggestion. In real Word the
carried changes and the tracked bold open clean and show as Word shows its own.

**Full-canvas editing** (type anywhere, like Word): SuperDoc was the
recommended component, but its Word engine (`@superdoc/docx-engine`) is now a
separate **proprietary** package whose licence binds on install, allows only
AGPL-style evaluation without a paid agreement, and **forbids benchmarking or
validating it**, including with AI tools. Not installed. Open alternatives:
ONLYOFFICE (AGPL; Developer Edition for the Automation API; the Euro-Office
dispute) or EigenPal docx-editor (Apache core; tracked changes only in its paid
Pro). The clause editor above gives Word-like typing without either.

#### One viewer to edit in: ONLYOFFICE in the Original tab (2026-09-22)

The user asked for one viewer that edits like a word processor and looks like
the original. **Edit document** in the Original tab opens the file in
ONLYOFFICE Docs Community 9.4 (AGPL, run unmodified in its own container,
`docker compose --profile editor up -d onlyoffice`), taking the whole window
until **Done editing**:

* A Word file opens with its own formatting and edits as in Word; Track
  Changes is on, so every edit is a tracked change by You (accept/reject
  balloons as Word's). A typed PDF opens in its PDF editor (text boxes,
  highlight, strike-out, redact, comments). A scan is not opened: amendment.
* **Save** (the save icon; Ctrl/Cmd+S) sends the file back to
  `/editor/saved`, which believes it only if signed with the shared JWT secret
  and fetches it only from the editor's own address (never one it is told),
  then ingests it as the next version — History keeps every one. The save sent
  again when the editor closes is skipped when every part of the file is the
  same as the latest version (only its zip timestamps differ).
* Measured: an edit saved as version 5, word compare showing exactly the words
  typed and 70 other clauses unchanged. ONLYOFFICE re-saves the whole file: it
  rewrote how one two-column section was stored and kept an earlier tracked
  bold as plain bold. PDF text boxes re-wrap inside their own box and overlap
  what is below, as in Acrobat — a PDF cannot re-flow.
* First start: the container builds its font list and restarts itself once
  (~2 minutes); an editor open at that moment reports "connection lost".
* Licensing: AGPL, used unmodified as a separate service. A product needs a
  decision (Developer Edition for the Automation API; the Euro-Office dispute).

**Goal:** accepted proposals become a new version with real Word tracked changes.

**Tasks**

1. Proposals are `ds_annotation` rows with `kind='proposal'`. Creating one
   changes no text.
2. One review queue. Every proposal, whatever its origin (human, AI, playbook),
   is accepted or rejected the same way.
3. `POST /docstudio/documents/{id}/apply`:
   - load the source file
   - insert `<w:ins>`/`<w:del>` per accepted proposal
   - save as a **new** `ds_version`; never mutate the old one
   - re-parse structure, **carry `clause_id` forward** for unchanged clauses
   - run the ladder for every annotation; orphans to the queue
4. `tracked.py` — the OOXML writer. **Study
   `mike-main/backend/src/lib/docxTrackedChanges.ts` first.** Its approach:
   locate the target by `find` text plus surrounding context, build a
   character→run index over `document.xml`, splice the revision tags in. Its
   edit vocabulary is deliberately narrow — find and replace text. That is
   enough for clause rewording and it is why no editor licence is needed.

**Acceptance**
- Accept three proposals → one new version with three tracked changes.
- The export opens in Word and accept/reject works natively.
- Comments on untouched clauses survive with zero orphans.
- The previous version's bytes are unchanged.

---

## 8. Decisions to resolve

### ① Clause identity across versions — the hard one

When a clause is reworded, is it the same clause? If yes, its history follows. If
no, history is lost.

- A content hash breaks on any edit.
- Position alone breaks when a clause is inserted above.
- Likely answer: **position + text similarity**. Above a threshold, carry the id;
  below it, mint a new one and orphan the old annotations.

**Run a spike before Phase 5.** Take real version pairs, try thresholds, measure.
Do not guess the number — your own data has it.

### ② Where `ds_document` attaches to the business record

`external_ref` points at whatever the rest of AEGIS calls a contract. Decide the
direction of that reference before Phase 1, and keep it one-way.

### ③ How long both systems run in parallel

See §9.

### ④ `.doc` (Word 97–2003)

A different binary format; neither python-docx nor docx-preview can read it.
Either convert on ingest (`libreoffice-writer --no-install-recommends` measured
at 142 packages / 113 MB download; `antiword` is <1 MB but text-only, no tables)
or refuse it with a clear message. **Do not accept it and produce an empty
document.**

---

## 9. Coexistence and cutover

Two systems will run at once. Plan it, don't discover it.

1. **Phases 1–2: shadow mode.** Docstudio ingests every new upload and builds its
   structure. Nothing in the UI changes. Compare its clause counts against the
   old pipeline's and investigate the gaps — this is free validation.
2. **Phase 3: opt-in.** A flag puts the new viewer behind a toggle for a few
   contracts.
3. **Phase 4–5: default for new documents.** Old documents keep the old screen.
4. **Cutover: backfill.** Run Docstudio ingest over historical files. Old
   annotations do not migrate automatically — they anchor differently. Decide
   per kind whether to re-anchor them through the ladder or leave them on the old
   screen read-only.
5. **Only then** remove the old pipeline.

Do not delete anything from the existing pipeline until step 5.

---

## 10. Later phases

- **Citation highlighting on the page** — `bbox` is populated from Phase 1, so
  this becomes UI work rather than a data migration.
- Clause library — insert approved fallback language from the panel.
- [Docling](https://arxiv.org/html/2408.09869v1) as an additional parser. The
  registry makes it a config change.
- LLM re-anchoring for orphans (~90% accurate; a queue-shrinker, not a guarantee).
- Authoring, if ever genuinely wanted. Evaluate **OnlyOffice Docs Community
  Edition** — AGPL, free, self-hosted, the 20-connection limit removed in 9.4 —
  before paying for anything.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **Anchor** | how an annotation finds its place in a document |
| **Annotation** | anything attached to a clause: comment, citation, proposal, risk |
| **Authoring** | free-form typing with a cursor. Out of scope |
| **`clause_id`** | permanent identity for a clause; survives rewording |
| **Ladder** | the five-rung fallback for re-finding an annotation |
| **OCR** | reading text off a picture of a page |
| **Page furniture** | headers, footers and stamps repeating on most pages. Not content |
| **Audit** | counting what the file holds against what was extracted (§14.3) |
| **Reflow** | rejoining a sentence a page break cut in half |
| **`w:ind`** | a Word paragraph's indentation — how the document shows nesting |
| **Layout table** | a table used as a border or a two-column clause layout, not to hold data |
| **OOXML** | the XML inside a `.docx` |
| **Orphaned** | an annotation whose clause could not be found. Shown, never dropped |
| **Proposal** | a suggested change; applied only when a human accepts |
| **Rung** | which step of the ladder resolved an anchor. Logged |
| **Tracked change** | `<w:ins>` / `<w:del>` in a `.docx`; Word shows it as a redline |

---

## 12. Rules to hold to

1. **The source file is never edited in place.** New version, always.
2. **Flat text is derived, never authoritative.** It must be safe to rebuild.
3. **No annotation is ever silently dropped.** Orphan it and surface it.
4. **Exactly one code path writes document text.**
5. **Comments never end up inside the file** sent to a counterparty.
6. **When the rendered file and the extracted text disagree, say so.**
7. **Docstudio imports nothing from the old document pipeline** except through
   `filesource.py`.
8. **Measure before deciding.** The ladder-rung distribution and the
   clause-similarity threshold are numbers your own database can give you. Get
   them instead of guessing.
9. **Test on real client documents.** A corpus your own system generated will
   always satisfy your own assumptions.
10. **"The parser worked" and "we have the contract" are different claims.**
    Every step must be able to say which one it is reporting.
11. **Prefer a fact in the file over an inference about it.** Indentation, table
    shape and coordinates are facts; "this paragraph probably belongs to that
    clause" is a guess, and the guesses are what had to be reverted (§14.4).
12. **A check that cannot fail is not a check.** Every fix here was reverted
    once to confirm its test actually caught the bug.

---

## 13. What is built (Phases 1 to 3)

```
backend/app/docstudio/
├── models.py        223   ds_document · ds_version · ds_clause · ds_annotation · ds_event · ds_ocr_result
├── service.py       414   ingest: parse -> OCR if needed -> cleanup -> join -> tree -> audit -> save
├── structure.py     160   blocks -> clauses with stable ids and offsets; the tree from tree.py
├── tree.py          364   which clause sits inside which, strongest evidence first  (§16)
├── hierarchy.py     468   the AI settles only the undecided, from closed options  (§16)
├── report.py        956   the per-version markdown page  (§15)
├── runner.py        101   one file end to end — shared by the CLI and the dev page
├── __main__.py       85   `python -m app.docstudio` / `make phase1`
├── devui.py         996   the dev page's routes: run, open, ask, notes, comments, redline, marks, history, file, vendor — non-prod only
├── devpage/              the dev page itself, laid out like Mike: documents · chat · document panel
│   ├── body.html          (read fresh per request: uvicorn reloads on .py only)
│   ├── page.css
│   └── page.js            the viewer (pdf.js, OCR text layer, docx-preview) and the cited chat
├── ask.py           226   "Ask this document": whole-document answer, every citation checked
├── redline.py      1100   Word tracked changes and tracked formatting; clause edits → minimal edits; PDF → Word keeping its look
├── marks.py         443   a PDF's changes as marks beside it; marked-up PDF, Word version, verified in-place patch, amendment
├── pdftext.py       286   words changed inside a PDF page's own text instruction, reading order kept
├── drafting.py      154   the AI redlining: an instruction → checked edits by "AEGIS AI"
├── routes.py        215   Phase 3 — the four endpoints the document view reads
├── schemas.py        77   Phase 3 — what those endpoints return
├── audit.py         165   counts what the file HOLDS against what came out  (§14.3)
├── ocr.py           198   provider protocol + fallback + stored readings, for scanned pages
├── filesource.py     41   the ONE interface to the rest of AEGIS
├── anchoring.py     336   Phase 2 — the three anchors and the five-rung ladder  (§18)
├── annotations.py   229   Phase 2 — create, re-anchor on a new version, lost queue, re-link
├── versions.py      146   which clause in a new version is which in the last; history; compare by words
└── parsing/
    ├── base.py      103   Parser protocol, ParsedBlock, ParsedDocument
    ├── registry.py   42   mime -> parser. A lookup, never an if/elif
    ├── labels.py    202   what a clause number looks like, and what it means (scheme + path)
    ├── docx.py      502   numbering (with each list's own levels), tables, indentation, tracked changes
    ├── pdf.py       330   PyMuPDF: text, page numbers, bboxes
    ├── cleanup.py   122   removes what is not contract text, with a reason for each  (§16.1)
    ├── furniture.py  76   running headers, footers, e-signature stamps
    ├── reflow.py    126   rejoins a sentence a page break cut in half, with a reason for each
    └── text.py      138   plain text and OCR output -> blocks
```

```
frontend/src/features/docstudio/
├── DocstudioWorkspace.tsx  203   one version on screen: Original | Text, warnings, clause text
├── DocumentView/index.tsx   58   renderer dispatch by exact media type
├── DocumentView/PdfRenderer.tsx  canvas + transparent text layer, zoom
├── DocumentView/DocxRenderer.tsx docx-preview, tracked changes shown
├── DocumentView/SimpleRenderer.tsx  image, plain text, and the honest "cannot show" panel
├── api.ts / types.ts             the four calls and their shapes
└── renderer.test.ts              3 tests — an SVG is never drawn as a picture
```

Migrations `0046_docstudio_structure`, `0047_docstudio_original_file`. **289
backend tests across 22 files** (940 in the whole suite) and 19 frontend — every
bug in §14.1 has one.

Phases 1 and 2 are built. Nothing in the package is written-but-unwired.

### Verified on real client documents

| Document | Shape | Clauses | Numbered |
|---|---|---|---|
| Goodwill notice (`.docx`) | native Word, numbers typed by hand | 75 | 27 |
| Employment Agreement (`.pdf`) | printed web page, scanned | 93 | 73 |
| Legal Services Agreement (`.pdf`) | WordPerfect, real text | 70 | 27 |
| Franklin Madison MSA (`.pdf`) | paper scan, DocuSigned | 141 | 68 |

The spread is not a defect. Published benchmarks report parser accuracy varying
by **55 percentage points** across document types; this is the normal range.

---

## 14. What the real documents taught us

Three real files found **ten bugs** in code that passed its own tests. Recorded
because the pattern matters more than the individual fixes.

### 14.1 The bugs

| # | Bug | Found by |
|---|---|---|
| 1 | Word's automatic numbering not reconstructed | generated fixture |
| 2 | `Document.paragraphs` silently omits every table | generated fixture |
| 3 | **Tracked-change insertions dropped.** `<w:ins>` text invisible, so a redline read "Liability is capped at  of fees." | adversarial test |
| 4 | **Nested tables dropped whole.** A fee schedule inside a layout table lost every figure | adversarial test |
| 5 | Clauses inside tables unaddressable | adversarial test |
| 6 | `Section 4.2` unmatched, and the ones that matched came out flat | adversarial test |
| 7 | Reflow runaway — 10 fragments merged into 1 clause | adversarial test |
| 8 | Lettered lists broke past `(z)` | adversarial test |
| 9 | `2026. The year...` read as clause 2026 | adversarial test |
| 10 | A clause number split from its clause became an empty clause | adversarial test |

Plus two found by uploading real files: **typed numbering ignored** — the parser
asked Word for automatic numbering and stopped there, so a document where the
author typed "(i)" and "A." arrived with nothing numbered — and **page furniture
counted as content**, where 14 DocuSign stamps were 798 of a 14-page scan's 860
characters, enough to clear the "is there any text here?" check so a contract
with nothing readable in it reported a clean parse.

**Note which are which.** The three worst — 3, 4, 5 — have nothing to do with
pattern matching. They are file-format reading bugs, and no amount of AI would
have prevented or fixed them: the text was already gone before anything looked
at it.

### 14.2 This is normal, and everyone pays it

The same bugs are open or historical in tools with decades of work behind them:

- python-docx [#85](https://github.com/python-openxml/python-docx/issues/85) (hyperlink text) and [#1025](https://github.com/python-openxml/python-docx/issues/1025) (tracked changes); a separate library, [`docx-revisions`](https://github.com/balalofernandez/docx-revisions), exists solely to add `w:ins`/`w:del` support
- Pandoc [#6983](https://github.com/jgm/pandoc/issues/6983) — *"docx: unreadable content error for **nested tables**"*
- LibreOffice bug 161001 (a **nested table** escaping its parent); bug 104444 is a *META* bug covering the whole category of DOCX table issues
- [`docxaudit`](https://github.com/GuoCheng24/docxaudit) exists purely to find what converters silently drop: *"Pandoc — or any pipeline — **reports success and still loses things**"*

### 14.3 So the audit exists

`audit.py` counts the text the file **holds**, by a different route from the
parser that read it, and reports the gap. Over 2% unexplained raises a warning.

Reverting fixes 3 and 4 makes it say:

> *94 characters of 180 (52.2%) were in the file but not extracted. The document uses tracked insertions, tables.*

It would have caught both on day one, without anyone knowing to look for `w:ins`.

Four properties it needs and has:

* **shares no code with the parser** — a check built on the same reader agrees
  with it by construction and finds nothing, which is why it reads the raw XML
  rather than python-docx's object model. The object model *was* the bug.
* **ignores whitespace** — extraction collapses spaces and re-joins lines, so
  counting raw length would flag every document ever parsed.
* **subtracts deliberate drops** — parsers declare them via
  `ParsedDocument.dropped_chars`, or every document with a running header looks
  like a parser failure.
* **stays silent after OCR** — the native text is precisely what was rejected,
  so there is nothing meaningful to compare against.

The number is recorded on every ingest, clean or not: one row means little, the
series is the point. A document that starts dropping text after a library
upgrade shows up as a trend.

### 14.4 Use geometry, not cleverer patterns

The two structural fixes that worked best both read a *fact in the file* instead
of guessing from words:

* **`w:ind`** — paragraph indentation. Eight quoted statutory provisions nested
  correctly under the sentences introducing them.
* **table shape** — a single-column table is a border; three columns is data.

Compare the heuristic that had to be reverted: *"an unnumbered paragraph belongs
to the last numbered clause"* read correctly on a prose contract and put 44 body
paragraphs underneath one partner's name on a document listing partners by name.

This is also what the layout-model literature does — [LayoutLM](https://www.klippa.com/en/blog/information/layoutlm-explained/) trains on text,
coordinates and the page image together. **`bbox` is populated on every PDF
clause and still unused.** It is the same signal for PDFs that `w:ind` is for
Word, and the cheapest remaining structural win.

### 14.5 OCR is not deterministic

The same scanned PDF ingested three times produced three different texts:

```
39,209 chars | 39,916 chars | 39,207 chars
similarity ignoring whitespace: 100.00%   (two stray colons)
```

The words are stable; the whitespace is not — which shifts every character
offset and changes where blocks split.

Two consequences. The dedup rule (same bytes **and** same parser returns the
existing version) is load-bearing rather than an optimisation: without it,
re-ingesting a scanned contract would mint fresh clause ids and orphan every
annotation. And it is a concrete argument for §4.2 — on a scanned document,
offsets cannot be trusted across a re-parse **even when nothing has changed**.

### 14.6 Rules versus AI, measured

For *segmentation*, rules are competitive. A [corpus study of Australian
contracts](https://aclanthology.org/U10-1005.pdf) reports 100% accuracy from machine learning and adds that
"an equally accurate regular expression based segmenter was also able to be
crafted".

For *meaning*, it is genuinely unsolved. On [CUAD](https://github.com/The-Atticus-Project/cuad) — 510 contracts, 13,000
expert labels — transformer performance is described as "nascent", and the
scores were low enough that CUAD was excluded from [LexGLUE](https://arxiv.org/pdf/2110.00976).

So the division in §4.4 stands: **the parser fixes boundaries, an LLM labels
meaning on top, and it never moves a clause.**

---

### 14.7 The geometry was already in the response

Twelve of the thirteen corpus documents are scans, and every clause from every
one of them had `page_number = None` and `bbox = None`. A citation into a
scanned contract could be quoted but never *shown* — no page to open, no
rectangle to highlight. For the documents this system mostly holds, the entire
point of a viewer was missing.

The cause was not that OCR loses geometry. Reducto returns
`result.chunks[].blocks[]`, and every block carries a page and a rectangle. The
client read `chunk.content` and dropped the rest of the object on the floor.
The data had been arriving, and being discarded, the whole time.

The fix is three small pieces:

- `OCRResult.blocks` — a new field on the shared result, kept **separate from
  `elements`** because `page_map_from_elements` depends on that field's
  granularity and re-using it would have changed an existing page map.
- `_blocks_from(chunks)` in the Reducto client — converts the provider's
  `{page, left, top, width, height}` into the `{x0, y0, x1, y1}` the rest of
  the code uses.
- `_blocks_from_ocr(result)` in docstudio — builds `ParsedBlock`s from those
  blocks instead of re-splitting the flat string. Splitting the text again
  would have thrown the geometry away a second time.

On `MSA_2020_6_000676`, an 11-page scan: **83 clauses, 83 with a page number, 83
with a rectangle** — from 0 and 0. Pages 1 through 11 all covered.

Both paths now report boxes as a **fraction of the page**, not points. A
consumer cannot tell two coordinate systems apart by looking at the numbers,
and fractions are what a viewer needs anyway: they hold at any zoom and any
rendered resolution.

**What this unlocks, and has not been used for yet.** In the hand-scored
accuracy run, 8 of 12 errors were OCR reading a rubber stamp as clause text.
With geometry those fragments are now visibly distinguishable: they cluster at
x 0.75-0.90, y 0.85-0.94 — the bottom-right corner, on five separate pages —
while real clauses span x 0.17-0.83 starting from y 0.15. `furniture.py`
currently detects repetition by *text*, which works on a DocuSign stamp that
OCRs identically every time and fails on a rubber stamp that does not. Position
repeats even when the characters do not. That is §14.4's lesson again, and it
is now available to act on.

---

## 15. The per-version report (`report.py`)

Phase 1 recorded everything it learned across five tables and a warnings list,
so the only way to ask *"did this document come out cleanly?"* was to query
rows. Nothing assembled the answer, which is how OCR reading a rubber stamp as
clause text sat in the database looking exactly like a clause.

`report(db, version_id)` renders one markdown page holding **everything the
version contains**: the file's own identity (including the full SHA-256), what
extraction produced as numbers, the findings, a truncated outline to orient by,
then every clause in full — text, `clause_id`, parent, level, kind, char
offsets and bounding box — and the event history with the audit counts the
ingest recorded. It stores nothing and is regenerable, so the checks can change
without a migration or a backfill.

The page is deliberately **larger than the document it describes** (1,665 lines
for a 62KB contract). An earlier draft truncated the text on the reasoning that
a report should not duplicate its source; that was wrong for the actual reader.
A model asked whether clause 9.1 contradicts 9.4 needs both clauses, and an
outline forces a second lookup for every question worth asking. The clause id
matters for the same reason: it is the stable identity an annotation anchors to,
and the one field a reader cannot derive from anything else on the page.

### 15.1 Why the checks are code and not a model

The two defects worth finding most — a clause cut in half, a missing clause
number — are both **absences**, and absence is the one thing language models
measurably cannot see:

- [AbsenceBench](https://arxiv.org/pdf/2506.11440) — *"Language Models Can't Tell What's Missing"*. Claude 3.7
  Sonnet scores **69.6% F1** on spotting removed content while being
  near-superhuman at needle-in-a-haystack retrieval of content that is present.
  The explanation is architectural: attention has no token to attend to for
  something that is not there.
- [Omission blindness in LLM judges](https://arxiv.org/abs/2608.31016) — **0.79-0.94** on added or altered
  content against **0.50-0.63** on omissions. 0.50 is a coin flip.
- [When Absence Is Evidence](https://arxiv.org/abs/2608.04591) — models cannot reliably separate *"this is
  missing"* from *"I lack the evidence to say"*.
- [LLMs Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) — without external feedback,
  performance often **degrades** after self-correction.

A report generated from parser output also inherits every parser bug — the same
trap `audit.py` was built to avoid. The eight stamp fragments would appear in it
*as clauses*, and a model reading it would have no way to know.

What the same research found does work is stating the gap outright, so the page
reports conclusions rather than leaving them to be inferred. It is the split
[Great Expectations](https://docs.greatexpectations.io/docs/0.18/reference/learn/terms/data_docs/) uses — expectations are code, Data Docs are the render
— and the one [Litera's Contract Companion](https://www.litera.com/products/contract-companion) has shipped for twenty years,
checking *"defined terms, cross-references, numbering, amounts, dates, names,
and citations"* as rules. It auto-fixes numbering, which nobody would risk on
inference.

**The division that holds:** something *missing* or *malformed* → code. Two
things both present that *disagree* → a model, which is superhuman at exactly
that and has nothing to be blind to.

### 15.2 Tuning against the corpus, which was the whole job

The checks took an hour. Making them trustworthy took the rest. First run over
21 documents and 4,101 clauses: **1,015 findings**. Final: **164**, median 5 per
document — and the survivors are real.

| Check | First run | Final | What the corpus taught |
|---|---|---|---|
| `FRAGMENT` | 308 | 60 | A short block is only damage when it **repeats** (furniture stripping missed a DMS stamp, `MDC\757175_1`, and a footer, `Page 4 of 13`) or **is not words**. `WITNESSETH`, `SAP ABAP HR`, `Representative` are real content the parser typed as a paragraph |
| `CUT OFF` | 203 | 32 | "No full stop" is not truncation — contracts are full of table cells, template instructions and addresses. Fires only on a word that **cannot end a sentence** (`the`, `of`, `shall`). Precision over recall on purpose: a missed truncation is still visible in the text, 150 false alarms get the report ignored |
| `NO TARGET` | 40 | 23 | Statutory citations are written `section 45(1) of the Criminal Finances Act` — the subsection defeated an `of`-only guard. `Section 409A` in a 13-section contract is the IRC, caught by range. `Section 6.0` must find clause `6.` Below 10% of clauses numbered the check is **switched off**, because "the reference is broken" then cannot be told from "we never read the target" |
| `DUPLICATE NUMBER` | 17 | **0** | Every one was a recital, exhibit or sub-list restarting — one was the date `16 JAN 2019` read as clause 16. Restricted to *dotted* labels: nothing legitimately has two clause 4.2s |
| `REPEATED BY POSITION` | 4 | 1 | Three were legacy rows holding PDF **points**, where a 0.05 page-fraction tolerance groups anything |
| `MARKUP IN TEXT` | — | 13 | New. Reducto's `<table>`, `<b>`, `<empty>` were hiding inside `CUT OFF`, which named the wrong problem |

**`REPEATED BY POSITION` is the one §14.7 predicted.** On
`MSA_2020_6_000676` it names all five stamp fragments in a single finding —
x 0.81, y 0.90, pages 4-6, 9, 11 — the error class that was 8 of the 12 mistakes
in the hand-scored run. `furniture.py` matches repeats by *text*, so a DocuSign
stamp that OCRs identically is caught and a rubber stamp that does not is
missed. Position repeats when the characters do not.

That document's report went from **31 findings to 8**, and all 8 are real.

### 15.3 The lesson, which is the same one as §14

Six of the seven checks were **weakened** by contact with real documents, and
the one thing that survived unweakened was geometry. Half the test file now
guards against crying wolf rather than against missing a defect — because a
checker nobody trusts is worse than no checker: it trains a reviewer to skip the
one finding that mattered.

**The report is the render, never the detector.** 830 tests, 35 of them here.

---

## 16. Hierarchy: evidence first, the AI settles only what is left (`tree.py`, `hierarchy.py`)

Which clause sits inside which. Measured against an answer key built by hand
from the employment agreement's PDF, and targeted checks built from the
Franklin Madison MSA's PDF — the two documents whose defects drove this design:

| Method | Employment: child clauses correct | Franklin Madison MSA |
|---|---|---|
| Old level-stack in `structure.py` | 23/63 = 36.5% | 5.2–5.5 under a page header; 3.2 cut in two by a stamp |
| One model call placing every clause (`hierarchy/1`) | 63/63 | put 5.5 inside 5.4 |
| **Rules from evidence (`tree.py`), no AI** | **60/63 = 95%** — the other 3 are undecided, with the right answer among the options of each | all checks pass; 49 unnumbered paragraphs undecided |
| **The same, plus the AI on the undecided only (`hierarchy/2`)** | **63/63 = 100%**; 88/89 overall — the one difference is a signature line placed under "EXECUTIVE:" | all checks pass; 0 likely mistakes |

Temperature 0: the same run twice gives the same tree.

A third document, read by a user, found what the first two could not: a
30-page patent licence holding two agreements, recitals numbered "1." and
"2.", a schedule numbered "Item 1"–"Item 9", and design certificates in
Chinese and Malay. Before the fixes in §16.1 (the rules marked ★), four of its
checks failed and 15 clauses were left undecided — eight because the AI's
*correct* answer had been cut from its options, seven because the report's
stamp check called page titles stamps. After them: all 12 checks pass, 82 of 82
questions answered, none refused — with Employment still 63/63 and Franklin
still passing everything.

A fourth, the Lockton MSA, broke the rule that numbering going back to 1 starts
a new part: 5.2 defines its formula's terms as "1. PO = …" to "4. …", and
16.10 lists its order of precedence as "1." to "3.". Each list was taken for
a new part, so the terms went to the top level, 5.3 lost 5, and everything
from 16.11 on lost 16 — eight correct AI answers refused because the right
parent was outside the "part". Now (★★ below) all 13 of its checks pass, and
the other three documents are unchanged.

### 16.1 The order of evidence

Each step may only decide what the steps before it left open, and marks what it
cannot decide instead of guessing.

1. **Cleanup, before anything is joined** (`cleanup.py`). What the source says
   a block is — OCR's "Header", "Footer", "Page Number", "Figure" (never a
   numbered block on a label alone) — then text repeated on most pages, short
   blocks in one spot on three or more pages (stamps), and debris. On Franklin:
   26 page headers, 28 footers, 29 figures. The stamp between the two halves of
   3.2 had kept them apart; removed first, the halves are neighbours again.
   ★ Page counters too — "1 of 4", "Page 7 of 19" — whatever they were
   labelled: "1 of 4" read as clause 1 and looked like the numbering restarting.
2. **Joining** (`reflow.py`). Never onto a numbered block, a heading or a table;
   never after a sentence end or after "…; or" / "…, and" (the next list item);
   then a hyphen, a dangling word ("…rules in"), or a lower-case start.
3. **Reading numbers** (`labels.parse_number`) as a scheme and a path: `5.2` is
   decimal (5, 2); `(iv)` is roman 4; ★ "Item 5" numbers a schedule the way
   "Section 5" numbers an agreement; ★ "12.3.LICENSOR", where OCR dropped the
   space, is still clause 12.3 (a digit before the dot, so "U.S." is not "U."). `(i)`, `(v)`, `(x)` are settled by their
   neighbours — "4.(i) Withholding" after (h) is a letter, "(h) …the following:
   (i) … (ii)" a numeral — and an OCR "(1)" between (k) and (m) is read as (l).
4. **The tree** (`tree.build_tree`): Word's own level within its own list →
   decimal prefix (5.5 sits in 5) → list continuity ((c) follows (b), whatever
   paragraph sits between; 2. follows 1. the same way) → a new list belongs to
   what introduces it (the numbered clause above, a heading, a sentence ending
   ":" or "the following") → Word indentation → **undecided**. Parts: an exhibit
   or schedule title once numbering has begun — a work order numbering from
   16.17 is not clause 16 of the agreement.
   * ★★ Numbering that starts again at 1 with no title is a question, not a new
     part: "5.2 … 1. … 2." is a list inside 5.2, a second agreement's "1." new
     top-level numbering. Top-level numbers are tracked as runs; a run the next
     number does not sit inside has ended, so 6. still follows 5. after 5.2's
     own 1.–4.
   * ★ A top-level run right after a sentence ending ":" is a question, not a
     rule: "WHEREAS: 1. … 2. …" is a list inside the sentence, "…the parties
     agree as follows: 1. DEFINITIONS" starts the main clauses.
   * ★ "Introduces" means a closing colon, or "the following" / "as follows".
     A bare "below" or "then" is not: "…referenced in Item 2 below." had put
     Items 2–9 inside Item 1's text.
   * ★ Only a numbered clause, a heading, or a sentence introducing what follows
     can hold other clauses. Offered the line before, the AI had stacked a
     certificate's thirteen lines each inside the one before, and "NOW,
     THEREFORE" inside the last recital.
5. **The AI** (`hierarchy.arrange_version`), once per version, on the
   undecided clauses only. It reads the whole outline as placed so far; each
   question comes with a closed list of options (`tree.options`: every clause
   still open at that point, and the top of its part — never outside the
   exhibit it is printed in). ★ The open clauses are always offered in full;
   only nearby unplaced lines are capped — a cap on the whole list had cut the
   right answer for six certificate lines. ★ Nothing else is left out: stamps
   and debris were removed at ingest, and the report's second guess at what is
   a stamp had kept seven schedule and exhibit titles from ever being asked. An answer outside the options is refused and
   recorded. Answers go back through `build_tree`, which applies them only to
   undecided clauses, so no answer can move a clause the numbering placed.
   `tree.violations` is checked before anything is written.

Every clause records who placed it in `structure_source`: `document`,
`numbering`, `list`, `part`, `top`, `layout`, `ai` or `undecided`.

### 16.2 What is stored

| Where | What |
|---|---|
| `ds_clause` | `number_scheme`, `number_path`, `structure_source`, and `source_regions` — every page piece of a rejoined clause, so a viewer lights up both halves |
| `ds_version.artifacts` | `{"removed": [...], "joined": [...]}`, each with its reason and pages — shown in the report's Cleanup section |
| `ds_ocr_result` | each file's OCR reading, by hash and provider. OCR is paid and not deterministic (§14.5), so any bytes are read once |
| `structure.arranged` event | the questions asked, answers taken, answers refused, and every before → after |

The report's Structure section counts clauses by who placed them, lists likely
mistakes first (`broken_runs`, recomputed from the stored tree whoever placed
it), then every placement the AI made and anything still undecided.

### 16.3 Known limits

* **"NOW, THEREFORE" is pinned to the top level by the prompt**, the legal
  convention: it ends the recitals and begins the agreement, and is not one of
  them. Unpinned, the AI put it inside BACKGROUND on one run and beside it on
  the next. A reader expecting it inside the recitals is a one-line change.
* **Drawing pages stay loose.** Under the patent licence's "Item 7" the OCR
  labelled one country title a heading and the next two plain text, so "Hong
  Kong (HK)" and "Malaysia (MY)" sit inside "European Union (EU)", not beside it.

* **Two documents measured.** Employment has a full answer key; Franklin has
  targeted checks, and its 49 AI placements were read, not scored. Two look
  debatable — a paragraph placed inside the paragraph before it rather than
  beside it. A prompt stating that rule was tried: Franklin did not change and
  Employment's signature block got worse, so it was reverted.
* **Word levels and indentation are not stored**, so arranging a Word file
  after ingest keeps what they decided rather than re-deriving it.
* **Versions ingested before this rebuild** have no `structure_source` and are
  not arranged; uploading the file again rebuilds them (the parser versions were
  bumped, so it is not deduplicated away).
* `broken_runs` still false-alarms where an inner list reuses the top level's
  number style ("2 personal information…" against "1 Definitions").

---

## 17. Next

1. **Bring the dev page's viewer work into the app** — the scan text layer,
   "Ask this document" with citations, and margin comments exist only on the
   dev page.
2. **Connect Ask Aegis to docstudio**, so its answers cite into this viewer:
   docstudio must read every upload (§9, shadow mode — not built).
3. **Phase 4 — the clause panel** (§7): clause selection in the document view,
   and each clause's notes, citations and history beside it.
4. **Answer keys for more contracts** (§16.3) — a full key for the Franklin
   Madison MSA first, then more. Word files with automatic numbering carry the
   author's own levels and are free answer keys; convert them to PDF to test
   that path too.
5. **More real version pairs** (§18.3) — two were enough to change the design;
   a dozen would say whether the thresholds hold.
6. **Persist findings as rows** when the question becomes "which of my 300
   documents have numbering gaps?" — today that means grepping markdown.
7. **Grow the corpus.** Four real documents found twelve bugs. The literature
   suggests 50-100 documents including known failures; every new shape has paid
   for itself so far.

---

## 18. Anchoring: notes that follow their words (`anchoring.py`, `annotations.py`, `versions.py`)

A note — comment, citation, proposal, risk — saves three ways to find its words:
its clause, the exact words with 64 characters either side, and its position.
When a new version of the document is ingested, every note is looked for again:

| Rung | Found by | State |
|---|---|---|
| 1 | its clause (identity carried from the last version) still holds its words | ok |
| 2 | its old position still holds its words | ok |
| 3 | its words, verbatim, where its clause *should* be — or anywhere the text on both sides still matches | ok |
| 4 | words close to its own (≥ 75% alike as a whole): in its clause, where its clause should be, or — for a quote of 40+ characters with both sides alike — anywhere | moved |
| 5 | nothing | **lost**: kept, listed, re-linkable, tried again on every version |

**Clause identity** (`versions.carry_ids`): the two versions' clauses are aligned
in order; unchanged bodies keep their ids (renumbering is not a change), and in
each changed stretch the most alike pairs keep theirs down to 70% similar.
**Where a clause should be** (`anchoring.expected_window`): between the nearest
clauses around it that kept their identity — position *and* text, the answer §8 ①
predicted.

### 18.1 What the first build got wrong, found by measuring

* **Twins.** Contracts repeat themselves — boilerplate, definitions stated
  twice, headings one digit apart. A note whose clause was deleted moved onto
  such a twin 11 times in 60. Away from where its clause should be, a quote now
  moves only if the text on *both* sides of it still matches.
* **Restructured drafts.** A real second draft split 39 clauses into 82 and
  repeated the agreement after a review memo. The both-sides rule then refused
  sentences still there at 99%. Where the clause *should be* — between its
  surviving neighbours — a match is trusted without it.
* **Cut-off spans.** A fuzzy window the old quote's length turned "indirect
  damages" into "indirect or cons". The quote is re-aligned over a wider
  stretch, and the chosen words must themselves be 75% alike — a window had
  matched a redrafted sentence on shared vocabulary alone, 56% alike.

### 18.2 Measured

Simulated: 2,400 notes on random words of the four real contracts, each followed
through a generated second version (paragraphs inserted, clauses reworded,
deleted and moved), the right answer known:

| Settings | Right place | **Wrong place** | Lost but still there |
|---|---|---|---|
| Hypothesis's (32 chars, fuzzy 82), no side checks | 2,221 | 12 | 40 |
| **As built** (64 chars, fuzzy 75, both sides, expected window) | **2,230** | **8** | **32** |

The 8 left are text a contract states twice with the same words around it,
which no amount of context separates — they are reported, not hidden: the
report shows every note's rung.

Real version pairs, a note on every sentence of the first draft:

| Pair | Notes | Found (rung 1 / moved) | Lost | Read by hand |
|---|---|---|---|---|
| Consulting agreement, draft → draft 3 (restructured, 39 → 82 clauses) | 22 | 15 / 4 | 3 | every moved note on the right words; 2 lost were rewritten, 1 sits in a copy of the agreement inside a review memo |
| Goodwill observation → its redraft | 103 | 40 / 4 | 59 | the lost sentences' closest text in the redraft is 45–68% alike: rewritten, not moved |

### 18.3 Known limits

* **Two real pairs.** The simulation is large but generated; the real pairs are
  what changed the design twice. More would say whether 70 / 75 / 12 hold.
* **Duplicated passages** are resolved by position, then by context; two copies
  with identical surroundings cannot be told apart and one is chosen.
* **Notes are added on the current version only**, from the dev page, by quoting
  words that must appear exactly once. The document view (Phase 3) replaces the
  quoting with selecting.

## 19. One viewer, and what a PDF becomes in it (2026-09-23)

### 19.1 Finding a clause inside the editor

The editor draws the document itself, so the page can no longer lay a box over
words as it does in its own viewer. Free ONLYOFFICE has no way in from outside
— that is its Developer Edition — but a plugin runs inside it and a plugin can
fetch. The page leaves the words on the server (`POST /editor/find`); a hidden
plugin asks for them every second (`/editor/plugin/command`) and runs the
editor's own `Search` → `Select`. Clicking a citation, a tracked change or a
mark while the editor is open now jumps to those words in it.

Two dead ends, recorded so they are not tried again:

* A plugin served from elsewhere (`editorConfig.plugins.pluginsData`) is
  registered but started only now and then since 8.2 — it ran once and never
  again. The plugin is therefore **mounted into the editor's own plugin folder**
  (`docker-compose.override.yml`), named by its guid, and calls back across
  origins (the two answers carry `Access-Control-Allow-Origin`).
* A background plugin's on/off state lives in the editor origin's
  `localStorage` (`asc_plugins_background`), which is why a remote one, once
  closed, stayed closed.

### 19.2 Two conversions, and why only one makes a version

Measured on five real contracts — each conversion rendered back to PDF by the
same engine and compared with the original, page by page:

| Contract | Ours: pixels differ / words in order / pages | ONLYOFFICE's: same |
|---|---|---|
| Basic NDA (2p) | 5.0% / 100.0% / 2→2 | 14.1% / 95.6% / 2→2 |
| IT Services (36p) | 12.1% / 97.1% / 36→42 | 9.0% / 94.6% / 36→36 |
| NDA Form (5p) | 16.4% / 99.7% / 5→8 | 12.9% / 84.8% / 5→5 |
| TCS (6p) | 5.5% / 99.7% / 6→6 | 4.9% / 86.8% / 6→6 |
| Software licence, two columns (8p) | refused (68% in order) | 12.5% / 63.3% / 8→8 |

ONLYOFFICE keeps the page count because it lays the page out as **691 floating
text boxes** (719 anchored drawings, no font names, one Normal style): it looks
right and cannot be edited, redlined or cut into clauses — our reader gets
63–95% of the words in order out of it. Ours makes real paragraphs in the PDF's
own fonts and sizes and reads back at 97–100%, and rebuilds the page.

So: **ours makes the working version**, and the box conversion is a download
only ("Download a look-alike Word copy"), which never becomes a version and so
cannot cost the document its structure. For a PDF that must look exactly right
while being worked on, the answer is not to convert at all — open the PDF
itself in the editor.

### 19.3 The page growth, and what fixed part of it

pdf2docx reads a signature row or a form line as two columns and ends it with a
column break; a column break in a section Word lays out in columns starts a new
page when the columns do not fit. `_one_column` puts short column regions back
into one: **TCS 11 pages → 6**, IT Services 43 → 42, the rest unchanged.

What is left is overflow: a page's content comes out about 3% taller than the
paper (Word wraps a line the PDF did not), so its last lines fall onto a page of
their own — the NDA Form's 5 pages become 8. Shaving the space between
paragraphs to fit was measured and changed nothing on any of the five, because
the height cannot be estimated well enough from outside to know when to shave.
Left as it is.

## 20. Every document in Word, and read properly (2026-09-23)

The decision: the editor works on Word documents, so every PDF becomes one —
typed PDFs read as documents, scans rebuilt from what OCR read. Opening a PDF
with **Edit document** converts it first and edits that version; the PDF stays
as the version before it.

### 20.1 Why the page-by-page conversion was replaced

pdf2docx rebuilds each PDF page as a box of its own with every paragraph pinned
to a point on it. One line that Word wraps differently — a font it does not
have, a space it measures at a hair more — pushes the box past the paper and
the page splits in two, and every repair fights the next: undoing its spurious
two-column sections fixed the page count and scattered a signature grid;
putting the grid in a table fixed the grid and cost two pages; moving the
running footer to the margin cost two more.

`toword.py` reads the PDF for what a Word document is made of and lets Word lay
it out: the font of every run (read out of the font file the PDF carries, so a
subset named "ABCDEF+CIDFont" is written as the Times New Roman it is), its
size, weight, slant and colour, the paragraph's alignment, indentation and the
space above it, ruled tables, pictures, the tab stops a form line uses, and the
running header and footer — in Word's header and footer, where they belong.

Measured on five contracts (letters kept / printed lines whole / pages):

| Contract | Page by page | Flowing |
|---|---|---|
| Basic NDA (2p) | 100% / — / 2 | **100% / 100% / 2** |
| IT Services (36p) | 99.0% / — / 56 | **100% / 100% / 43** |
| NDA Form (5p) | 100% / — / 10 | **100% / 100% / 6** |
| TCS (6p) | 100% / — / 10 | **99.9% / 100% / 6** |
| Software licence, two columns (8p) | refused | **99.9% / 100% / 9** |

Across the whole dev library: **33 of 33 PDFs convert** — 19 typed, 14 scans.
pdf2docx is gone, and with it opencv (~90 MB).

Four things it took measuring to get right:

* **A line ends a paragraph only when the next word would have fitted on it.**
  Splitting on any short line turned 2,495 printed lines into 3,244 paragraphs.
  The measure is the block's own, not the page's, or a paragraph set narrower
  than the column splits at every line.
* **Columns are found before rows are assembled**, from a gutter nothing
  crosses, decided for the document but applied region by region — a contract
  opens full width with the parties and turns to two columns for the
  definitions on the same page — and a single page in columns finds its own.
* **A row is what was printed on one line**, gathered from the fragments a PDF
  reader hands back, with a wide gap inside it becoming a tab stop. Read block
  by block instead, one justified line arrives as four paragraphs pinned right.
* **The check is that every printed line survives whole**, and in letters, not
  words: a PDF prints "( 18 )" where Word writes "(18)", and counting words
  called that a loss of 10%.

### 20.2 Scans: markdown as the record of the reading

A scan has no text at all, so there is nothing to convert. `scanmd.py` writes
what OCR read as a markdown file — one block per paragraph with the page, the
place, the measured size, its kind and the confidence — and builds the Word
document from that file. The size comes from the height of each block (a line
of mixed-case text inks about four fifths of its type size, and each line after
the first adds a line's pitch); the family is an assumption, named in the front
matter. Figures are cut out of the page at 200 dpi, so signatures, stamps and
logos survive. The header and footer are kept once, matched on letters alone
because OCR reads the same header differently on different pages.

The markdown is downloadable ("Download what was read"), and it is the point:
it is the record of what the machine read, it can be corrected by hand, and the
Word document is built from it — so a correction reaches the document.
