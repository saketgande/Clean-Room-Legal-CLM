(() => {
  // The page lives at <prefix>/docstudio/dev; the API prefix is whatever precedes it.
  const BASE = location.pathname.replace(/\/docstudio\/dev\/?$/, "");
  const $ = (id) => document.getElementById(id);
  const state = { file: null, result: null };
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  async function detail(res) {
    try {
      const body = await res.json();
      return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      return `${res.status} ${res.statusText}`;
    }
  }

  // --- The documents, on the left ---------------------------------------------

  function typeBadge(name) {
    const ext = (name || "").split(".").pop().toLowerCase();
    const badge = document.createElement("span");
    badge.className = `ft ${ext}`;
    badge.textContent = ext === "docx" ? "W" : ext === "pdf" ? "PDF" : "TXT";
    return badge;
  }

  async function loadDocuments() {
    try {
      const res = await fetch(`${BASE}/docstudio/dev/documents`);
      if (!res.ok) return;
      const documents = await res.json();
      const target = $("target"), chosen = target.value, list = $("doc-list");
      target.replaceChildren(new Option("a new document", ""));
      list.replaceChildren();
      for (const d of documents) {
        target.add(new Option(`a new version of: ${d.title} (now version ${d.version ?? "?"})`, d.id));
        const item = document.createElement("button");
        item.type = "button";
        item.dataset.id = d.id;
        item.title = d.title || "";
        const name = document.createElement("span");
        name.className = "nm";
        name.textContent = d.title || "Untitled";
        const version = document.createElement("span");
        version.className = "v";
        version.textContent = `v${d.version ?? "?"}`;
        item.append(typeBadge(d.title), name, version);
        item.addEventListener("click", () => { showList(false); openDocument(d.id); });
        list.append(item);
      }
      if (!documents.length) {
        const none = document.createElement("p");
        none.className = "side-empty";
        none.textContent = "None yet — add one above.";
        list.append(none);
      }
      target.value = [...target.options].some((o) => o.value === chosen) ? chosen : "";
      markOpen();
    } catch { /* a convenience: adding a document still works without it */ }
  }

  function showList(open) {
    $("doc-list").hidden = !open;
    $("doc-switch").setAttribute("aria-expanded", String(open));
  }
  $("doc-switch").addEventListener("click", () => showList($("doc-list").hidden));
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".switcher")) showList(false);
  });

  function markOpen() {
    for (const item of $("doc-list").querySelectorAll("button")) {
      item.classList.toggle("on", item.dataset.id === state.result?.document_id);
    }
  }

  async function openDocument(id) {
    const title = $("doc-title");
    title.textContent = "Opening…";
    try {
      const res = await fetch(`${BASE}/docstudio/dev/documents/${encodeURIComponent(id)}`);
      if (!res.ok) { title.textContent = await detail(res); return; }
      state.result = await res.json();
      showUpload(false);
      paint();
      startEditing(false);   // the editor opens with the document, as Word does
    } catch (err) {
      title.textContent = `Could not reach the server: ${err.message}`;
    }
  }

  // --- Adding a document --------------------------------------------------------

  function showError(message) {
    $("error").textContent = message;
    $("error").hidden = !message;
  }

  // The start screen: it stands in the document's place until there is one,
  // and "New document" brings it back.
  function showUpload(open) {
    $("start").hidden = !open;
    $("viewer").hidden = open;
    $("vbar").hidden = open || !state.result;
    if (open) showError("");
  }
  // Cancel is disabled only while a file is being read — the one time the box
  // must stay open, since the result lands in it.
  const reading = () => $("cancel-upload").disabled;
  $("new-doc").addEventListener("click", () => { stopEditing(false); showUpload(true); });
  $("cancel-upload").addEventListener("click", () => {
    showUpload(false);
    if (state.result) startEditing(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("start").hidden && !reading() && state.result) {
      showUpload(false);
      startEditing(false);
    }
  });

  function choose(file) {
    if (!file) return;
    state.file = file;
    $("drop-title").textContent = file.name;
    $("drop-sub").textContent = `${Math.max(1, Math.round(file.size / 1024))} KB — reading it now`;
    showError("");
    readFile();     // a file chosen is a file to read; there is nothing else to ask
  }

  const drop = $("drop");
  $("file").addEventListener("change", (e) => choose(e.target.files[0]));
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => choose(e.dataTransfer.files[0]));

  $("run").addEventListener("click", () => readFile());

  async function readFile() {
    if (!state.file) return;
    $("cancel-upload").disabled = true;
    showError("");
    const started = Date.now();
    const tick = () => { $("status").textContent = `Reading… ${Math.round((Date.now() - started) / 1000)}s`; };
    tick();
    const timer = setInterval(tick, 1000);
    const body = new FormData();
    body.append("file", state.file);
    body.append("ai", $("ai").checked ? "true" : "false");
    body.append("version_of", $("target").value);
    try {
      const res = await fetch(`${BASE}/docstudio/dev/run`, { method: "POST", body });
      if (!res.ok) { showError(await detail(res)); return; }
      state.result = await res.json();
      state.file = null;
      $("drop-title").textContent = "Drop a contract here";
      $("drop-sub").textContent = "a .pdf, .docx or .txt — or click to choose one. It opens ready to edit.";
      $("target").value = "";
      $("file").value = "";
      showUpload(false);
      paint();
      showTab("ask");
      loadDocuments();
      // Straight into the document, which is what was asked for by adding it.
      startEditing(false);
    } catch (err) {
      showError(`Could not reach the server: ${err.message}`);
    } finally {
      clearInterval(timer);
      $("status").textContent = "";
      $("cancel-upload").disabled = false;
    }
  }

  // --- One document on screen -----------------------------------------------------

  function paint() {
    const r = state.result;
    $("doc-title").textContent = r.name;
    $("doc-meta").textContent = [
      plural(r.clauses, "clause"), r.pages ? plural(r.pages, "page") : null, plural(r.findings, "finding"), r.how,
    ].filter(Boolean).join(" · ");
    $("doc-meta").title = r.structure || "";
    // Rendered by the server with raw HTML escaped; contract text never becomes markup.
    $("doc").innerHTML = r.report_html;
    paintLost();
    // The conversation belongs to the document, not to one version of it: every
    // accepted or suggested change is a new version, and clearing the chat at
    // each would lose it mid-review.
    if (view.askedAbout !== r.document_id) {
      view.askedAbout = r.document_id;
      view.historic = null;
      if (view.editing) stopEditing(false);
      view.active = null;
      $("answers").replaceChildren();
      closeCitation();
    }
    $("welcome-h").textContent = "What would you like to know?";
    $("welcome-p").textContent = `Ask anything about ${r.name}. Every answer cites the clauses it comes from.`;
    $("suggest").hidden = false;
    $("welcome").hidden = $("answers").childElementCount > 0;
    $("q").disabled = false;
    $("ask").disabled = false;
    $("q").placeholder = `Ask about ${r.name}…`;
    markOpen();
    paintChanges();
    paintOriginal();
  }

  // The sidebar's tabs are the whole of what can be done to the document; the
  // document itself is always on screen beside them, never behind a tab.
  const PANES = { ask: "ask", changes: "changes", report: "report-pane", notes: "notes",
                  history: "history", files: "files-pane" };

  function showTab(which) {
    if (which === "original") return;   // the document is not a tab any more
    for (const [name, pane] of Object.entries(PANES)) {
      $(`tab-${name}`).classList.toggle("on", name === which);
      $(`tab-${name}`).setAttribute("aria-selected", String(name === which));
      $(pane).hidden = name !== which;
    }
    if (which === "history" && state.result) loadHistory();
  }
  for (const name of Object.keys(PANES)) $(`tab-${name}`).addEventListener("click", () => showTab(name));

  // --- The Original tab: the app's own viewer code, from vendor/ -----------
  // Mirrors DocumentView/PdfRenderer.tsx and DocxRenderer.tsx: same libraries,
  // same versions, same options — a check made here is a check of those.
  const VENDOR = `${BASE}/docstudio/dev/vendor`;
  const DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
  const ZOOMS = [0.75, 1, 1.25, 1.5, 2, 3];
  const view = { zoom: 1, token: 0, bytes: null, bytesFor: null, drawn: null, compare: false,
                 pdf: null, pdfFor: null, drawing: Promise.resolve(), cited: null, askedAbout: null, active: null };
  const fileUrl = (r) => `${BASE}/docstudio/dev/versions/${encodeURIComponent(r.version_id)}/file`;

  let pdfjs = null;
  async function pdfLib() {
    if (!pdfjs) {
      pdfjs = await import(`${VENDOR}/pdf.min.mjs`);
      pdfjs.GlobalWorkerOptions.workerSrc = `${VENDOR}/pdf.worker.min.mjs`;
    }
    return pdfjs;
  }

  // Classic scripts, loaded once and in order: docx-preview expects JSZip.
  const loading = {};
  const script = (name) => (loading[name] ??= new Promise((resolve, reject) => {
    const tag = document.createElement("script");
    tag.src = `${VENDOR}/${name}`;
    tag.onload = resolve;
    tag.onerror = () => { delete loading[name]; reject(new Error(`could not load ${name}`)); };
    document.head.append(tag);
  }));

  // A scan's words laid over its picture: the "searchable image" Acrobat
  // makes of a scan. The stored reading has one box per paragraph, so each
  // paragraph's words are sized to fill its box — they sit in the right
  // paragraph, not on each printed word. Not in the app's viewer yet.
  const OCR_FONT = "Helvetica, Arial, sans-serif";
  const OCR_LINE = 1.15;  // the .ocr line-height; the fit below assumes it
  let ruler = null;

  // The largest font size at which the text, wrapped to the box's width,
  // still fits its height — measured, so it holds for any font the browser picks.
  function fitSize(text, width, height) {
    if (!ruler) {
      ruler = document.createElement("canvas").getContext("2d");
      ruler.font = `100px ${OCR_FONT}`;
    }
    const lines = text.split("\n").map((line) =>
      line.split(" ").filter(Boolean).map((word) => ruler.measureText(`${word} `).width / 100));
    const rows = (size) => lines.reduce((total, words) => {
      let x = 0, count = 1;
      for (const w of words) {
        if (x && x + w * size > width) { count += 1; x = 0; }
        x += w * size;
      }
      return total + count;
    }, 0);
    let low = 1, high = Math.max(1, height / OCR_LINE);
    for (let step = 0; step < 14; step += 1) {
      const mid = (low + high) / 2;
      if (rows(mid) * mid * OCR_LINE <= height) low = mid; else high = mid;
    }
    return low;
  }

  function layOcr(layer, regions, viewport) {
    if (!regions?.length) return false;
    for (const { bbox, text } of regions) {
      const width = (bbox.x1 - bbox.x0) * viewport.width;
      const height = (bbox.y1 - bbox.y0) * viewport.height;
      if (width < 2 || height < 2) continue;
      const piece = document.createElement("div");
      piece.className = "ocr";
      piece.style.left = `${bbox.x0 * 100}%`;
      piece.style.top = `${bbox.y0 * 100}%`;
      piece.style.width = `${(bbox.x1 - bbox.x0) * 100}%`;
      piece.style.height = `${(bbox.y1 - bbox.y0) * 100}%`;
      piece.style.fontSize = `${fitSize(text, width, height)}px`;
      piece.textContent = text;  // OCR output is untrusted: text, never markup
      layer.append(piece);
    }
    return true;
  }

  async function drawPdf(bytes, host, token, result) {
    const lib = await pdfLib();
    // A copy: pdf.js hands the buffer to its worker, which would leave the
    // cached one empty for the next zoom. No eval: the policy forbids it.
    const doc = await lib.getDocument({ data: bytes.slice(0), isEvalSupported: false }).promise;
    try {
      const pages = document.createElement("div");
      pages.className = "pages";
      host.replaceChildren(pages);
      const first = await doc.getPage(1);
      const natural = first.getViewport({ scale: 1 }).width;
      // Fit the room beside the comment margin, then the reader's zoom on top.
      // Never below a readable width: past that the panel scrolls, as Word does.
      const scale = (Math.max(host.clientWidth - 24, 280) / natural) * view.zoom;
      const ratio = window.devicePixelRatio || 1;
      const ocrFor = new Map();
      for (const region of result.ocr_text || []) {
        if (!ocrFor.has(region.page)) ocrFor.set(region.page, []);
        ocrFor.get(region.page).push(region);
      }
      let withText = 0, withOcr = 0;
      for (let number = 1; number <= doc.numPages; number += 1) {
        if (token !== view.token) return null;  // a newer drawing has started
        const page = await doc.getPage(number);
        const viewport = page.getViewport({ scale });
        const sheet = document.createElement("div");
        sheet.className = "pg";
        sheet.style.width = `${viewport.width}px`;
        sheet.style.height = `${viewport.height}px`;
        // Painted at the screen's real pixel density. Painted at its CSS size,
        // a high-density screen stretches the canvas and every letter goes soft.
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        sheet.append(canvas);
        pages.append(sheet);
        await page.render({
          canvasContext: canvas.getContext("2d"),
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        }).promise;
        const layer = document.createElement("div");
        layer.className = "tl";
        layer.style.setProperty("--scale-factor", String(scale));
        sheet.append(layer);
        await new lib.TextLayer({
          textContentSource: page.streamTextContent(), container: layer, viewport,
        }).render();
        if (layer.querySelector("span:not(.markedContent)")) withText += 1;
        else if (layOcr(layer, ocrFor.get(number), viewport)) withOcr += 1;
      }
      // One short line on screen; the why in its tooltip, for whoever hovers.
      const count = plural(doc.numPages, "page");
      const bare = doc.numPages - withText - withOcr;
      const ocrWhy = "The words OCR read are laid invisibly over each paragraph, so they can be selected, copied and searched. They sit by paragraph, not on each printed word, and are only as right as the OCR — tick “Hidden text in red” to see where they are.";
      if (withText === doc.numPages) {
        return [`${count} · the file's own words, selectable`, "Tick “Hidden text in red” to check each word sits on its printed word."];
      }
      if (withOcr === doc.numPages) return [`${count} · scanned — OCR words laid over each paragraph`, ocrWhy];
      if (!withText && !withOcr) {
        return [`${count} · pictures only, nothing to select`, "The file holds no words and no OCR reading is stored for it."];
      }
      const parts = [];
      if (withText) parts.push(`${withText} with their own words`);
      if (withOcr) parts.push(`${withOcr} scanned`);
      if (bare) parts.push(`${bare} pictures only`);
      return [`${count} · ${parts.join(", ")}`, withOcr ? ocrWhy : ""];
    } finally {
      doc.destroy();
    }
  }

  async function drawDocx(bytes, host) {
    await script("jszip.min.js");
    await script("docx-preview.min.js");
    // Measured before drawing: once the pages are in, this column is as wide
    // as they are, not as wide as the room beside the comment margin.
    const room = Math.max(host.clientWidth - 24, 280);
    const box = document.createElement("div");
    host.replaceChildren(box);
    await window.docx.renderAsync(new Blob([bytes], { type: DOCX }), box, undefined, {
      inWrapper: true, breakPages: true, renderChanges: true,
      renderHeaders: true, renderFooters: true, ignoreFonts: false,
    });
    // A Word page is drawn at its paper width; shrink it to the room left
    // beside the comments, as Word does, then apply the reader's zoom.
    const page = box.querySelector("section.docx");
    const fit = page ? Math.min(1, room / page.offsetWidth) : 1;
    box.style.zoom = String(fit * view.zoom);
    return ["Word file · tracked changes shown", "Only page breaks written into the file are shown — Word works out the rest itself — so the pages can differ from Word's."];
  }

  async function drawText(bytes, host) {
    const pre = document.createElement("pre");
    pre.className = "textv";
    pre.textContent = new TextDecoder("utf-8").decode(bytes);  // shown as text, never parsed as markup
    host.replaceChildren(pre);
    return ["Plain text, as it is", ""];
  }

  const DRAWERS = { "application/pdf": drawPdf, [DOCX]: drawDocx, "text/plain": drawText };

  // One drawing at a time. Whoever needs the pages — a citation, say — waits
  // for the one under way instead of starting another.
  function drawOriginal() {
    if (view.editing) return view.drawing;  // the editor is on screen; drawing would close it
    const old = view.historic;
    // An older version is drawn as it was, with nothing to comment on: notes
    // and changes belong to the latest.
    const r = old ? { ...state.result, version_id: old.version_id, mime: old.mime, original: old.kept,
                      ocr_text: [], comments: [], converted: null } : state.result;
    const compare = !old && view.compare && !!r.converted;
    const key = `${r.version_id}@${view.zoom}@${compare}`;
    if (view.drawn !== key) {  // already on screen: keep the reader's place
      view.drawn = key;
      view.drawing = (compare ? drawCompare : drawFile)(r, ++view.token);
    }
    return view.drawing;
  }

  // What just happened, over the document. It clears itself: these are notes
  // about a moment — converted, saving, the editor came back — and left up they
  // take the document's room, which is the one thing the screen is for.
  let saying = null;
  function say(text, why = "") {
    const note = $("orig-note");
    note.textContent = text;
    note.title = why;
    note.hidden = !text;
    clearTimeout(saying);
    if (text) saying = setTimeout(() => { note.hidden = true; }, 9000);
  }

  async function fetchBytes(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await detail(res));
    return res.arrayBuffer();
  }

  async function drawFile(r, token) {
    const host = $("viewer");
    const draw = DRAWERS[r.mime];
    if (!r.original || !draw) {
      host.replaceChildren();
      say(!r.original
        ? "The file itself was not kept for this version — it was read before files were stored. Add the same file again to keep it; nothing is re-read."
        : `A ${r.mime} file cannot be drawn here. Download it to see it.`);
      return;
    }
    say("Drawing…");
    try {
      if (view.bytesFor !== r.version_id) {
        view.bytes = await fetchBytes(fileUrl(r));
        view.bytesFor = r.version_id;
      }
      if (token !== view.token) return;
      // The pages, and beside them the margin comments sit in, as in Word.
      const row = document.createElement("div");
      row.className = "doc-row";
      const main = document.createElement("div");
      main.className = "doc-main";
      const margin = document.createElement("div");
      margin.className = "margin";
      margin.id = "margin";
      // Word's margin narrows with its window rather than leaving the page or
      // its comments: 180–260px, about a third of the panel.
      margin.style.width = `${Math.round(Math.min(260, Math.max(180, host.clientWidth * 0.3)))}px`;
      row.append(main, margin);
      host.replaceChildren(row);
      const said = await draw(view.bytes, main, token, r);
      if (token === view.token && said) say(...said);
      if (token === view.token) {
        markCitation(view.cited, false);  // redrawn at a new size
        paintComments();
      }
    } catch (err) {
      if (token === view.token) {
        view.drawn = null;
        say(`The file could not be drawn: ${err.message}`);
      }
    }
  }

  // The PDF and the Word version made from it, page beside page, to judge what
  // the conversion kept. No margin: commenting is the normal view's job.
  async function drawCompare(r, token) {
    say("Drawing both…");
    try {
      if (view.pdfFor !== r.converted.version_id) {
        view.pdf = await fetchBytes(fileUrl(r.converted));
        view.pdfFor = r.converted.version_id;
      }
      if (view.bytesFor !== r.version_id) {
        view.bytes = await fetchBytes(fileUrl(r));
        view.bytesFor = r.version_id;
      }
      if (token !== view.token) return;
      const row = document.createElement("div");
      row.className = "compare";
      const column = (caption) => {
        const col = document.createElement("div"), head = document.createElement("p"), host = document.createElement("div");
        head.className = "cmp-h";
        head.textContent = caption;
        col.append(head, host);
        row.append(col);
        return host;
      };
      const left = column(`The PDF — version ${r.converted.version_number}`), right = column("The Word version");
      $("viewer").replaceChildren(row);
      await drawPdf(view.pdf, left, token, {});
      if (token === view.token) await drawDocx(view.bytes, right);
      if (token === view.token) {
        say("Page beside page. This previewer starts a page at every section break and cannot draw columns, so the Word side runs longer here than it does in Word.",
            "Comments are hidden while comparing: untick “Beside the PDF” to comment or edit.");
      }
    } catch (err) {
      if (token === view.token) {
        view.drawn = null;
        say(`The files could not be drawn: ${err.message}`);
      }
    }
  }

  function paintOriginal() {
    const r = state.result, link = $("orig-download"), made = r.converted, conv = $("conv-note");
    paintHistoric();
    const editable = r.original && !view.historic && (r.editable || r.mime === "application/pdf");
    $("v-edit").hidden = view.editing || !editable;
    $("v-done").hidden = !view.editing;
    $("v-compare-box").hidden = !made;
    conv.hidden = !made;
    if (made) {
      conv.classList.toggle("warn", made.look !== "kept");
      conv.textContent = made.look === "kept"
        ? `Converted from the PDF (version ${made.version_number}) with its fonts, sizes and layout. A copy to redline — the PDF is the original.`
        : made.note || `Made from the PDF's words (version ${made.version_number}); its look was not kept.`;
    }
    link.href = fileUrl(r);
    link.setAttribute("download", r.name);
    link.hidden = !r.original;
    const take = $("out-current");   // the same file, under Files
    take.href = link.href;
    take.setAttribute("download", r.name);
    take.classList.toggle("off", !r.original);
    // The editor holds a Word document; the PDF's own outputs are for a PDF.
    const pdf = r.mime === "application/pdf";
    for (const id of ["out-marked", "out-word-row", "out-patch-row", "out-look-row", "out-amend"]) {
      const row = $(id)?.closest(".out") || $(id);
      if (row) row.hidden = !pdf;
    }
    $("out-reading-row").hidden = !r.scanned;
    // After a note is added this runs again: the same version keeps its pages
    // and the reader's place, a different one is drawn afresh. Either way the
    // comments are laid out again, since they may have changed.
    if (!view.editing) drawOriginal().then(paintComments);
  }

  function zoomBy(step) {
    const at = ZOOMS.indexOf(view.zoom);
    const next = ZOOMS[Math.min(ZOOMS.length - 1, Math.max(0, at + step))];
    if (next === view.zoom) return;
    view.zoom = next;
    $("v-zoom").textContent = `${Math.round(next * 100)}%`;
    if (state.result && !view.editing) drawOriginal();
  }
  $("v-out").addEventListener("click", () => zoomBy(-1));
  $("v-in").addEventListener("click", () => zoomBy(1));
  $("v-reveal").addEventListener("change", (e) => $("viewer").classList.toggle("reveal", e.target.checked));
  $("v-compare").addEventListener("change", (e) => {
    view.compare = e.target.checked;
    if (state.result && !view.editing) drawOriginal();
  });

  // The sidebar's width, dragged, and kept for the next visit.
  const SIDE_MIN = 300;
  function setSide(width) {
    const most = Math.max(SIDE_MIN, window.innerWidth - 480);
    const fitted = Math.round(Math.min(most, Math.max(SIDE_MIN, width)));
    document.documentElement.style.setProperty("--side-w", `${fitted}px`);
    return fitted;
  }
  try {
    const kept = Number(localStorage.getItem("docstudio.sidebar"));
    if (kept) setSide(kept);
  } catch { /* a private window: the default width will do */ }

  $("grip").addEventListener("pointerdown", (e) => {
    e.preventDefault();
    const grip = $("grip");
    try { grip.setPointerCapture(e.pointerId); } catch { /* a pointer the browser no longer tracks */ }
    grip.classList.add("drag");
    let width = null;
    const move = (m) => { width = setSide(window.innerWidth - m.clientX); };
    const up = () => {
      grip.classList.remove("drag");
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
      if (width === null) return;
      try { localStorage.setItem("docstudio.sidebar", String(width)); } catch { /* not kept */ }
      // The pages were drawn for the old width: draw them again to fit.
      if (state.result && !view.editing) { view.drawn = null; drawOriginal(); }
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
  });

  // --- Asking: answers from this version's clauses, every point cited ---------

  function askError(message) {
    $("ask-error").textContent = message;
    $("ask-error").hidden = !message;
  }

  function grow() {
    const box = $("q");
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 200)}px`;
  }

  const scrollThread = () => { $("thread").scrollTop = $("thread").scrollHeight; };

  async function send(text) {
    if (!state.result || $("ask").disabled) return;
    const question = (text ?? $("q").value).trim();
    if (!question) { askError("Type a question first."); return; }
    askError("");
    $("welcome").hidden = true;
    const turn = document.createElement("div");
    turn.className = "turn";
    const you = document.createElement("div");
    you.className = "you";
    const asked = document.createElement("p");
    asked.textContent = question;
    you.append(asked);
    const pending = document.createElement("div");
    pending.className = "thinking";
    pending.textContent = "Reading the contract…";
    turn.append(you, pending);
    $("answers").append(turn);
    $("q").value = "";
    grow();
    scrollThread();
    $("ask").disabled = true;
    const started = Date.now();
    const timer = setInterval(() => {
      pending.textContent = `Reading the contract… ${Math.round((Date.now() - started) / 1000)}s`;
    }, 1000);
    try {
      const body = new FormData();
      body.append("version_id", state.result.version_id);
      body.append("question", question);
      const res = await fetch(`${BASE}/docstudio/dev/ask`, { method: "POST", body });
      if (!res.ok) {
        pending.className = "error";
        pending.textContent = await detail(res);
        return;
      }
      pending.replaceWith(answerOf(await res.json()));
    } catch (err) {
      pending.className = "error";
      pending.textContent = `Could not reach the server: ${err.message}`;
    } finally {
      clearInterval(timer);
      $("ask").disabled = !state.result;
      scrollThread();
    }
  }

  $("ask").addEventListener("click", () => send());
  $("q").addEventListener("input", grow);
  $("q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  for (const button of $("suggest").querySelectorAll("button")) {
    button.addEventListener("click", () => send(button.textContent));
  }

  // The answer is model output about untrusted text: built from text nodes and
  // buttons, never parsed as markup.
  function answerOf(reply) {
    const box = document.createElement("div");
    box.className = "reply";
    const prose = document.createElement("p");
    prose.className = "answer";
    const byNumber = new Map(reply.citations.map((cite) => [cite.n, cite]));
    const citeOf = (part) => byNumber.get(Number((/^\[(\d+)\]$/.exec(part ?? "") || [])[1]));
    const parts = reply.answer.split(/(\[\d+\])/);
    parts.forEach((part, i) => {
      const cite = citeOf(part);
      // "USA [1]." — the pill sits against its word, as Mike's does.
      prose.append(cite ? pill(cite) : citeOf(parts[i + 1]) ? part.trimEnd() : part);
    });
    box.append(prose);
    if (reply.warning) {
      const warning = document.createElement("p");
      warning.className = "error";
      warning.textContent = reply.warning;
      box.append(warning);
    }
    if (reply.citations.length) {
      const sources = document.createElement("div");
      sources.className = "sources";
      const heading = document.createElement("div");
      heading.className = "sources-h";
      heading.textContent = `Sources · ${reply.citations.length}`;
      sources.append(heading);
      for (const cite of reply.citations) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "src";
        row.cite = cite;
        const where = document.createElement("span");
        where.className = "where";
        where.textContent = cite.page ? `${cite.label} · p${cite.page}` : cite.label;
        const quote = document.createElement("span");
        quote.className = "qt";
        quote.textContent = `“${cite.quote}”`;
        row.append(pill(cite, true), where, quote);
        if (!cite.verified) {
          const flag = document.createElement("span");
          flag.className = "flag";
          flag.textContent = "not verified";
          row.append(flag);
        }
        row.addEventListener("click", () => showCitation(cite));
        sources.append(row);
      }
      box.append(sources);
    }
    const by = document.createElement("p");
    by.className = "by";
    by.textContent = `Answered by ${reply.by}`;
    box.append(by);
    return box;
  }

  // Mike's round citation number. In a source row the row is the button, so
  // the number there is only a label.
  function pill(cite, label) {
    const el = document.createElement(label ? "span" : "button");
    if (!label) {
      el.type = "button";
      el.addEventListener("click", () => showCitation(cite));
    }
    el.className = cite.verified ? "pill" : "pill bad";
    el.textContent = String(cite.n);
    el.title = cite.verified
      ? `${cite.label}${cite.page ? `, page ${cite.page}` : ""} — show it on the page`
      : "Not verified: these words are not in the document";
    el.cite = cite;
    return el;
  }

  async function showCitation(cite) {
    view.cited = cite;
    for (const el of $("answers").querySelectorAll(".pill, .src")) el.classList.toggle("on", el.cite === cite);
    $("cite-where").textContent =
      `${cite.verified ? "" : "Not verified · "}${cite.label}${cite.page ? ` · page ${cite.page}` : ""}`;
    $("cite-quote").textContent = `“${cite.quote}”`;
    $("cite-box").classList.toggle("bad", !cite.verified);
    $("cite-box").hidden = false;
    askError("");
    if (view.editing) { await findInEditor(cite.quote); return; }
    await drawOriginal();
    markCitation(cite, true);
  }

  function closeCitation() {
    view.cited = null;
    $("cite-box").hidden = true;
    for (const el of $("answers").querySelectorAll(".on")) el.classList.remove("on");
    markCitation(null, false);
  }
  $("cite-close").addEventListener("click", closeCitation);

  // A citation on its page: a box over each piece of the clause — one on each
  // page for a clause rejoined across a page break. A Word or text file has no
  // page geometry, so there the quoted words are found in the drawn text.
  function markCitation(cite, scroll) {
    for (const old of document.querySelectorAll("#viewer .hl")) old.remove();
    if (!cite) return;
    const sheets = document.querySelectorAll("#viewer .pg");
    let first = null;
    for (const { page, bbox } of cite.regions || []) {
      const sheet = sheets[page - 1];
      if (!sheet || !bbox) continue;
      const box = document.createElement("div");
      box.className = "hl";
      box.style.left = `${bbox.x0 * 100}%`;
      box.style.top = `${bbox.y0 * 100}%`;
      box.style.width = `${(bbox.x1 - bbox.x0) * 100}%`;
      box.style.height = `${(bbox.y1 - bbox.y0) * 100}%`;
      sheet.append(box);
      first = first || box;
    }
    if (!scroll) return;
    if (first) first.scrollIntoView({ block: "center", behavior: "smooth" });
    else if (!findInViewer(cite.quote)) askError("Those words could not be found on the page.");
  }

  // Searched forward from the top of the pages, so the conversation — which
  // holds the same words — is never what gets found. The whole quote first,
  // then its opening words, in case the drawing breaks it differently.
  function findInViewer(quote) {
    const viewer = document.querySelector("#viewer .doc-main") || $("viewer"), selection = getSelection();
    for (const words of [quote, quote.split(/\s+/).slice(0, 8).join(" ")]) {
      selection.removeAllRanges();
      const start = document.createRange();
      start.setStart(viewer, 0);
      selection.addRange(start);
      if (words && window.find(words, false, false, false) && viewer.contains(selection.anchorNode)) {
        return true;
      }
    }
    selection.removeAllRanges();
    return false;
  }

  // --- Comments, as Word takes them: select words, comment in the margin ------

  const floatButton = $("select-tools");
  const elementOf = (node) => (node.nodeType === 1 ? node : node.parentElement);
  const SAME = { "“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-" };

  // The selection, when it lies in the drawn document.
  function pickedWords() {
    const selection = getSelection();
    const main = document.querySelector("#viewer .doc-main");
    if (!state.result || view.historic || !main || !selection.rangeCount || selection.isCollapsed) return null;
    const range = selection.getRangeAt(0);
    if (!main.contains(range.startContainer) || !main.contains(range.endContainer)) return null;
    const quote = selection.toString().trim();
    return quote ? { range: range.cloneRange(), quote } : null;
  }

  function offerComment() {
    const picked = pickedWords();
    floatButton.hidden = !picked;
    if (!picked) return;
    // Formatting belongs to a Word file; a PDF's look is fixed, and marks sit beside it.
    $("fmt-bar").hidden = !state.result.editable;
    const at = picked.range.getBoundingClientRect();
    floatButton.style.left = `${Math.min(window.innerWidth - 250, at.right + 8)}px`;
    floatButton.style.top = `${Math.max(8, at.top - 40)}px`;
  }
  $("viewer").addEventListener("mouseup", () => setTimeout(offerComment, 0));
  $("viewer").addEventListener("keyup", offerComment);
  $("viewer").addEventListener("scroll", () => { floatButton.hidden = true; });
  document.addEventListener("mousedown", (e) => { if (!floatButton.contains(e.target)) floatButton.hidden = true; });
  floatButton.addEventListener("mousedown", (e) => e.preventDefault());  // keeps the selection
  for (const [id, start] of [["comment-button", () => startComment], ["edit-button", () => startEdit]]) {
    $(id).addEventListener("click", () => {
      const picked = pickedWords();
      floatButton.hidden = true;
      if (picked) start()(picked);
    });
  }

  // The text either side of the selection on its page: what tells the server
  // which of several identical passages was meant.
  function around(range) {
    const main = document.querySelector("#viewer .doc-main");
    const startRoot = elementOf(range.startContainer).closest(".pg") || main;
    const endRoot = elementOf(range.endContainer).closest(".pg") || main;
    const before = document.createRange();
    before.setStart(startRoot, 0);
    before.setEnd(range.startContainer, range.startOffset);
    const after = document.createRange();
    after.setStart(range.endContainer, range.endOffset);
    after.setEnd(endRoot, endRoot.childNodes.length);
    return { prefix: before.toString().slice(-160), suffix: after.toString().slice(0, 160) };
  }

  // Boxes over the words. A scan's words are laid by paragraph, not on each
  // printed word, so there the paragraph is marked — all that is known.
  function rectsOf(range) {
    const scanned = [...document.querySelectorAll("#viewer .ocr")].filter((piece) => range.intersectsNode(piece));
    if (scanned.length) return scanned.map((piece) => piece.getBoundingClientRect());
    const rects = [...range.getClientRects()].filter((r) => r.width > 0.5 && r.height > 0.5);
    // A range also reports whole elements it contains; a box several lines
    // tall is one of those, not a line of words.
    const heights = rects.map((r) => r.height).sort((a, b) => a - b);
    const typical = heights[Math.floor(heights.length / 2)] || 0;
    return rects.filter((r) => r.height <= typical * 2.5);
  }

  function drawBoxes(rects, row, className, id) {
    const base = row.getBoundingClientRect();
    let top = null;
    for (const r of rects) {
      const box = document.createElement("div");
      box.className = className;
      if (id) box.dataset.id = id;
      box.style.left = `${r.left - base.left}px`;
      box.style.top = `${r.top - base.top}px`;
      box.style.width = `${r.width}px`;
      box.style.height = `${r.height}px`;
      row.append(box);
      top = top === null ? r.top - base.top : Math.min(top, r.top - base.top);
    }
    return top;
  }

  // The drawn text as the server compares it — every character that is not a
  // space, lower-cased, typography made plain — with where each one sits.
  function textIndex(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let flat = "";
    const at = [];
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.nodeValue;
      for (let i = 0; i < text.length; i += 1) {
        if (/\s/.test(text[i])) continue;
        for (const char of (SAME[text[i]] ?? text[i]).toLowerCase()) {
          flat += char;
          at.push([node, i]);
        }
      }
    }
    return { flat, at };
  }

  const squash = (text) => [...text].filter((c) => !/\s/.test(c)).map((c) => (SAME[c] ?? c).toLowerCase()).join("");
  function agree(a, b) {
    let count = 0;
    while (count < a.length && count < b.length && a[count] === b[count]) count += 1;
    return count;
  }

  // Where a comment's words are drawn: of every place they appear, the one
  // whose surroundings match the ones stored with it.
  function findRange(index, comment) {
    const needle = squash(comment.quote);
    if (!needle) return null;
    const before = [...squash(comment.prefix).slice(-64)].reverse().join("");
    const after = squash(comment.suffix).slice(0, 64);
    let best = -1, bestScore = -1;
    for (let hit = index.flat.indexOf(needle); hit !== -1; hit = index.flat.indexOf(needle, hit + 1)) {
      const score = agree([...index.flat.slice(Math.max(0, hit - 64), hit)].reverse().join(""), before)
        + agree(index.flat.slice(hit + needle.length, hit + needle.length + 64), after);
      if (score > bestScore) { best = hit; bestScore = score; }
    }
    if (best < 0) return null;
    const [startNode, startOffset] = index.at[best];
    const [endNode, endOffset] = index.at[best + needle.length - 1];
    const range = document.createRange();
    range.setStart(startNode, startOffset);
    range.setEnd(endNode, endOffset + 1);
    return range;
  }

  function paintComments() {
    const row = document.querySelector("#viewer .doc-row"), margin = $("margin");
    if (!row || !margin || !state.result || view.historic) return;
    for (const old of row.querySelectorAll(".cm:not(.draft)")) old.remove();
    for (const old of margin.querySelectorAll(".card:not(.draft)")) old.remove();
    const index = textIndex(row.querySelector(".doc-main"));
    for (const comment of state.result.comments || []) {
      const range = findRange(index, comment);
      let mark = comment.resolved ? "cm resolved" : "cm";
      if (comment.kind === "proposal") mark = `cm prop ${changeKind(comment)} ${comment.status}`;
      if (comment.kind === "highlight") mark = "cm hi";
      const top = range ? drawBoxes(rectsOf(range), row, mark, comment.id) : null;
      margin.append(comment.kind === "proposal" ? markCard(comment, top)
        : comment.kind === "highlight" ? highlightCard(comment, top) : cardFor(comment, top));
    }
    activate(view.active, false);  // the chosen card stays chosen through a redraw
  }

  // Each card level with its words, pushed down only as far as it must be to
  // clear the one above — Word's margin.
  function layoutCards() {
    const margin = $("margin");
    if (!margin) return;
    let floor = 0;
    for (const card of [...margin.children].sort((a, b) => a.want - b.want)) {
      const top = Math.max(card.want, floor);
      card.style.top = `${top}px`;
      floor = top + card.offsetHeight + 8;
    }
    margin.style.minHeight = `${floor}px`;
  }

  function activate(id, scroll) {
    view.active = id;
    for (const el of document.querySelectorAll("#viewer .cm, #margin .card")) {
      el.classList.toggle("on", Boolean(id) && el.dataset.id === id);
    }
    layoutCards();  // the chosen card grows by its reply box
    if (scroll && id && view.editing) {
      const c = (state.result?.comments || []).find((x) => x.id === id);
      if (c?.quote) findInEditor(c.quote);
      return;
    }
    if (scroll && id) {
      document.querySelector(`#viewer .cm[data-id="${CSS.escape(id)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // Clicking commented words picks out their comment, as in Word.
  $("viewer").addEventListener("click", (e) => {
    if (!getSelection().isCollapsed || e.target.closest("#margin")) return;
    const hit = [...document.querySelectorAll("#viewer .cm:not(.draft)")].find((box) => {
      const r = box.getBoundingClientRect();
      return e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
    });
    activate(hit ? hit.dataset.id : null, false);
  });

  const when = (iso) => (iso
    ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "");

  function header(el, iso, author = "You") {
    const who = document.createElement("div");
    who.className = "who";
    const avatar = document.createElement("span");
    avatar.className = "av";
    avatar.textContent = author.trim().charAt(0).toUpperCase() || "?";
    if (author !== "You") avatar.style.background = colourOf(author);
    const name = document.createElement("b");
    name.textContent = author;
    who.append(avatar, name);
    if (iso) {
      const stamp = document.createElement("span");
      stamp.className = "when";
      stamp.textContent = when(iso);
      who.append(stamp);
    }
    el.append(who);
    const tools = document.createElement("span");
    tools.className = "tools";
    who.append(tools);
    return tools;
  }

  // Two clicks, so a stray one never deletes: Word asks nothing, but Word has undo.
  function deleteButton(title, confirmed) {
    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.title = title;
    del.textContent = "×";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!del.classList.contains("sure")) {
        del.classList.add("sure");
        del.textContent = "Delete?";
        return;
      }
      del.disabled = true;
      Promise.resolve(confirmed()).finally(() => { del.disabled = false; });
    });
    return del;
  }

  function smallButton(className, text, title, pressed) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.title = title;
    button.addEventListener("click", (e) => { e.stopPropagation(); pressed(); });
    return button;
  }

  // Word's comment card: the comment, its thread of replies, and what can be
  // done to it. Built with textContent: comments are shown, never parsed as markup.
  function cardFor(comment, top) {
    const card = document.createElement("div");
    card.className = comment.resolved ? "card resolved" : "card";
    card.dataset.id = comment.id;
    card.want = top ?? 0;
    const error = document.createElement("p");
    error.className = "error";
    error.hidden = true;
    const id = encodeURIComponent(comment.id);

    // Every action answers with the document's comments as they now are.
    const act = async (path, method, fields) => {
      error.hidden = true;
      try {
        const init = { method };
        if (fields) {
          init.body = new FormData();
          for (const [key, value] of Object.entries(fields)) init.body.append(key, value);
        }
        const res = await fetch(`${BASE}/docstudio/dev/comments/${path}`, init);
        if (!res.ok) throw new Error(await detail(res));
        Object.assign(state.result, await res.json());
        paint();
        return true;
      } catch (err) {
        error.textContent = err.message.startsWith("Failed to fetch") ? "Could not reach the server." : err.message;
        error.hidden = false;
        layoutCards();
        return false;
      }
    };

    const tools = header(card, comment.created_at);
    if (comment.resolved) {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = "Resolved";
      tools.append(tag, smallButton("link", "Reopen", "Open this thread again", () => act(`${id}/resolve`, "POST", { resolved: "false" })));
    } else {
      tools.append(smallButton("done", "✓", "Resolve this thread", () => act(`${id}/resolve`, "POST", { resolved: "true" })));
    }
    tools.append(deleteButton("Delete this thread", () => act(id, "DELETE")));

    const body = document.createElement("p");
    body.className = "body";
    body.textContent = comment.body;
    card.append(body);
    const about = [
      comment.kind !== "comment" ? comment.kind : "",
      comment.label ? `on ${comment.label}` : "",
      comment.resolved && comment.replies.length ? plural(comment.replies.length, "reply").replace(/ys$/, "ies") : "",
      top === null ? "its words were not found on the page" : "",
      comment.moved ? "moved with its words from the last version" : "",
    ].filter(Boolean).join(" · ");
    if (about) {
      const on = document.createElement("p");
      on.className = "on-what";
      on.textContent = about;
      card.append(on);
    }

    if (comment.replies.length) {
      const thread = document.createElement("div");
      thread.className = "thread";
      for (const answer of comment.replies) {
        const row = document.createElement("div");
        row.className = "reply";
        header(row, answer.created_at).append(
          deleteButton("Delete this reply", () => act(encodeURIComponent(answer.id), "DELETE")),
        );
        const text = document.createElement("p");
        text.className = "body";
        text.textContent = answer.body;
        row.append(text);
        thread.append(row);
      }
      card.append(thread);
    }

    // The reply box, shown on the chosen card only (page.css), as in Word.
    if (!comment.resolved) {
      const box = document.createElement("div");
      box.className = "reply-box";
      const text = document.createElement("textarea");
      text.rows = 1;
      text.placeholder = "Reply…";
      const send = async () => {
        const reply = text.value.trim();
        if (!reply) { error.textContent = "Write the reply first."; error.hidden = false; layoutCards(); return; }
        button.disabled = true;
        if (await act(`${id}/replies`, "POST", { body: reply })) text.value = "";
        button.disabled = false;
      };
      const button = smallButton("btn pri", "Reply", "Add this reply (Cmd+Enter)", send);
      text.addEventListener("click", (e) => e.stopPropagation());
      text.addEventListener("input", () => {
        text.style.height = "auto";
        text.style.height = `${text.scrollHeight}px`;
        layoutCards();
      });
      text.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); send(); }
      });
      box.append(text, button);
      card.append(box);
    }
    card.append(error);
    card.addEventListener("click", () => activate(comment.id, true));
    return card;
  }

  function clearDraft() {
    for (const el of document.querySelectorAll("#viewer .draft")) el.remove();
    layoutCards();
  }

  function startComment(picked) {
    const row = document.querySelector("#viewer .doc-row"), margin = $("margin");
    if (!row || !margin) return;
    clearDraft();
    const { prefix, suffix } = around(picked.range);
    const top = drawBoxes(rectsOf(picked.range), row, "cm draft") ?? 0;
    getSelection().removeAllRanges();

    const card = document.createElement("div");
    card.className = "card draft on";
    card.want = top;
    header(card);
    const text = document.createElement("textarea");
    text.rows = 3;
    text.placeholder = "Add a comment…";
    const error = document.createElement("p");
    error.className = "error";
    error.hidden = true;
    const acts = document.createElement("div");
    acts.className = "acts";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn";
    cancel.textContent = "Cancel";
    const post = document.createElement("button");
    post.type = "button";
    post.className = "btn pri";
    post.textContent = "Comment";
    acts.append(cancel, post);
    card.append(text, error, acts);

    const fail = (message) => { error.textContent = message; error.hidden = false; layoutCards(); };
    const save = async () => {
      const body = text.value.trim();
      if (!body) { fail("Write the comment first."); return; }
      post.disabled = true;
      try {
        const form = new FormData();
        form.append("version_id", state.result.version_id);
        form.append("quote", picked.quote);
        form.append("prefix", prefix);
        form.append("suffix", suffix);
        form.append("body", body);
        const res = await fetch(`${BASE}/docstudio/dev/comments`, { method: "POST", body: form });
        if (!res.ok) { fail(await detail(res)); return; }
        Object.assign(state.result, await res.json());
        clearDraft();
        paint();
      } catch (err) {
        fail(`Could not reach the server: ${err.message}`);
      } finally {
        post.disabled = false;
      }
    };
    post.addEventListener("click", save);
    cancel.addEventListener("click", clearDraft);
    text.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); save(); }
      if (e.key === "Escape") clearDraft();
    });
    card.addEventListener("click", (e) => e.stopPropagation());
    margin.append(card);
    layoutCards();
    text.focus();
  }

  // --- Redlining: Word tracked changes, suggested, listed, accepted, rejected -------

  // Every redline action answers with the document's next version, drawn afresh.
  async function redline(path, fields, fail) {
    return postTo(`redline/${path}`, fields, fail);
  }

  // The editor is holding the file, so anything that changes it — the AI's
  // suggestions, accepting, rejecting, a clause retyped — waits for the editor
  // to write out what is open first, and opens again on the result. Otherwise
  // the change is made underneath the editor and lost at its next save.
  async function throughEditor(run) {
    if (!view.editing) return run();
    const was = state.result.version_id;
    say("Saving what is open in the editor…");
    try {
      const body = new URLSearchParams({ version_id: was });
      const res = await fetch(`${BASE}/docstudio/dev/editor/save-now`, { method: "POST", body });
      if (!res.ok) throw new Error(await detail(res));
      const { saved } = await res.json();
      if (saved) await waitForVersion(was);
    } catch (err) {
      say(`The editor could not save first, so nothing was changed: ${err.message}`);
      return null;
    }
    await stopEditing(false);
    const made = await run();
    await startEditing(false);
    return made;
  }

  // The editor saves through its own callback, a moment after it is asked.
  async function waitForVersion(was) {
    for (let i = 0; i < 12; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      const res = await fetch(`${BASE}/docstudio/dev/documents/${encodeURIComponent(state.result.document_id)}`);
      if (!res.ok) continue;
      const latest = await res.json();
      if (latest.version_id !== was) {
        state.result = latest;
        return true;
      }
    }
    return false;
  }

  async function postTo(path, fields, fail) {
    const body = new FormData();
    body.append("version_id", state.result.version_id);
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    try {
      const res = await fetch(`${BASE}/docstudio/dev/${path}`, { method: "POST", body });
      if (!res.ok) { fail(await detail(res)); return null; }
      const reply = await res.json();
      state.result = reply;
      paint();
      loadDocuments();
      return reply;
    } catch (err) {
      fail(`Could not reach the server: ${err.message}`);
      return null;
    }
  }

  const rlError = (message) => { $("rl-error").textContent = message; $("rl-error").hidden = !message; };

  // Word's "type over the selection": the card offers the selected words to
  // change, or to delete, and writes the result as a tracked change.
  function startEdit(picked) {
    const row = document.querySelector("#viewer .doc-row"), margin = $("margin");
    if (!row || !margin) return;
    clearDraft();
    const { prefix, suffix } = around(picked.range);
    const top = drawBoxes(rectsOf(picked.range), row, "cm draft") ?? 0;
    getSelection().removeAllRanges();

    const card = document.createElement("div");
    card.className = "card draft on";
    card.want = top;
    header(card);
    const error = document.createElement("p");
    error.className = "error";
    error.hidden = true;
    const fail = (message) => { error.textContent = message; error.hidden = false; layoutCards(); };
    const acts = document.createElement("div");
    acts.className = "acts wrap";
    const button = (label, cls, pressed) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = label;
      b.addEventListener("click", (e) => { e.stopPropagation(); pressed(b); });
      return b;
    };

    const target = state.result.editable ? "redline/suggest" : state.result.mime === "application/pdf" ? "marks" : null;
    if (!target) {
      const note = document.createElement("p");
      note.className = "body";
      note.textContent = "Only a Word file can carry tracked changes. Make an editable Word version of this document, then suggest the edit there.";
      acts.append(button("Cancel", "btn", clearDraft), button("Make Word version", "btn pri", async (b) => {
        b.disabled = true;
        if (await redline("editable", {}, fail)) showTab("changes");
        b.disabled = false;
      }));
      card.append(note, acts, error);
    } else {
      const was = document.createElement("p");
      was.className = "replace";
      was.textContent = picked.quote;
      const text = document.createElement("textarea");
      text.rows = 3;
      text.value = picked.quote;
      const send = async (b, replacement) => {
        b.disabled = true;
        const done = await postTo(target, { quote: picked.quote, replacement, prefix, suffix }, fail);
        b.disabled = false;
        if (done) clearDraft();
      };
      acts.append(
        button("Cancel", "btn", clearDraft),
        button("Delete words", "btn", (b) => send(b, "")),
        button("Suggest", "btn pri", (b) => send(b, text.value)),
      );
      text.addEventListener("click", (e) => e.stopPropagation());
      text.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); acts.lastChild.click(); }
        if (e.key === "Escape") clearDraft();
      });
      card.append(was, text, acts, error);
      setTimeout(() => { text.focus(); text.select(); }, 0);
    }
    card.addEventListener("click", (e) => e.stopPropagation());
    margin.append(card);
    layoutCards();
  }

  // A colour per author, the same every time, as Word gives each reviewer one.
  const AUTHOR_COLOURS = ["#7c3aed", "#0f766e", "#b45309", "#be123c", "#1d4ed8", "#4d7c0f"];
  function colourOf(name) {
    let h = 0;
    for (const c of name || "?") h = (h * 31 + c.charCodeAt(0)) >>> 0;
    return AUTHOR_COLOURS[h % AUTHOR_COLOURS.length];
  }

  const KIND = { replace: "replaced", insert: "inserted", delete: "deleted", paragraph: "paragraph mark", format: "formatting" };

  // The review list: every tracked change in the Word file — yours, the AI's
  // and the other side's — each accepted or rejected the same way.
  // Built with textContent: the change's words are shown, never parsed as markup.
  function paintChanges() {
    const r = state.result, list = $("rl-list"), found = r.changes || [];
    const pdf = r.mime === "application/pdf";
    $("rl-editing-note").hidden = !view.editing;
    $("changes").classList.toggle("while-editing", !!view.editing);
    $("rl-convert").hidden = r.editable || !r.original || pdf;
    $("rl-tools").hidden = !r.editable;
    $("rl-ai").hidden = !(r.editable || pdf);
    $("mk-tools").hidden = $("mk-note").hidden = !pdf;
    if (pdf) {
      paintMarks();
      return;
    }
    for (const count of [$("changes-count")]) {
      count.textContent = String(found.length);
      count.hidden = !found.length;
    }
    $("rl-count").textContent = found.length ? plural(found.length, "tracked change") : "No tracked changes";
    // While the editor is open this list is the last saved version, so it can
    // be empty while the editor holds changes. Saving comes first either way.
    $("rl-accept-all").disabled = $("rl-reject-all").disabled = !found.length && !view.editing;
    rlError("");
    list.replaceChildren();
    for (const change of found) {
      const row = document.createElement("div");
      row.className = "chg";
      const top = document.createElement("div");
      top.className = "top";
      const avatar = document.createElement("span");
      avatar.className = "av";
      avatar.textContent = (change.author || "?").trim().charAt(0).toUpperCase();
      avatar.style.background = colourOf(change.author);
      const who = document.createElement("b");
      who.textContent = change.author || "Unknown";
      const kind = document.createElement("span");
      kind.className = "kind";
      kind.textContent = KIND[change.kind] || change.kind;
      const stamp = document.createElement("span");
      stamp.className = "when";
      stamp.textContent = when(change.date);
      const acts = document.createElement("span");
      acts.className = "acts";
      for (const [label, cls, accept] of [["Accept", "yes", "true"], ["Reject", "no", "false"]]) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = cls;
        b.textContent = label;
        b.addEventListener("click", async (e) => {
          e.stopPropagation();
          b.disabled = true;
          await throughEditor(() => redline("resolve", { ids: change.ids.join(","), accept }, rlError));
          b.disabled = false;
        });
        acts.append(b);
      }
      top.append(avatar, who, kind, stamp, acts);
      const diff = document.createElement("p");
      diff.className = "diff";
      const context = (text, lead) => {
        const span = document.createElement("span");
        span.className = "ctx";
        span.textContent = lead ? `…${text.slice(-40)}` : `${text.slice(0, 40)}…`;
        return span;
      };
      if (change.before) diff.append(context(change.before, true), " ");
      if (change.deleted) { const d = document.createElement("del"); d.textContent = change.deleted; diff.append(d); }
      if (change.deleted && change.inserted) diff.append(" ");
      if (change.inserted) { const i = document.createElement("ins"); i.textContent = change.inserted; diff.append(i); }
      if (change.kind === "format") diff.append("formatting changed");
      if (change.after) diff.append(" ", context(change.after, false));
      row.append(top, diff);
      if (change.why) {
        const why = document.createElement("p");
        why.className = "why";
        why.textContent = `Why: ${change.why}`;
        row.append(why);
      }
      row.addEventListener("click", () => showChange(change));
      list.append(row);
    }
  }

  // A change, found on the drawn page and selected there: its new words if it
  // has any, otherwise the struck ones, told apart by the words around them.
  async function showChange(change) {
    if (view.editing) { await findInEditor(change.inserted || change.deleted); return; }
    showTab("original");
    await drawOriginal();
    const main = document.querySelector("#viewer .doc-main");
    const quote = change.inserted || change.deleted;
    if (!main || !quote || quote === "¶") return;
    const range = findRange(textIndex(main), { quote, prefix: change.before, suffix: change.after });
    if (!range) { askError("That change could not be found on the page."); return; }
    elementOf(range.startContainer).scrollIntoView({ block: "center", behavior: "smooth" });
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  }

  // Accept and reject go to the editor while it is open: it does them at once,
  // in front of you, and the file it then saves is the version. Nothing closes
  // and nothing reloads.
  async function reviewInEditor(accept) {
    const version = view.editing.from;
    say(accept ? "Accepting the changes in the editor…" : "Rejecting the changes in the editor…");
    const body = new URLSearchParams({ version_id: version, action: accept ? "accept" : "reject", quote: "" });
    const res = await fetch(`${BASE}/docstudio/dev/editor/find`, { method: "POST", body });
    if (!res.ok) { rlError(await detail(res)); return false; }
    await new Promise((resolve) => setTimeout(resolve, 2500));   // the editor applies them
    const saved = await fetch(`${BASE}/docstudio/dev/editor/save-now`, {
      method: "POST",
      body: new URLSearchParams({ version_id: version, why: accept ? "accepted" : "rejected" }),
    });
    if (saved.ok && (await saved.json()).saved) await waitForVersion(version);
    paint();
    loadDocuments();
    const left = (state.result.changes || []).length;
    say(left
      ? `${plural(left, "tracked change")} still in the document.`
      : `Done — ${accept ? "accepted" : "rejected"}, and saved as the next version. The version before it is in History.`);
    return true;
  }

  for (const [id, accept] of [["rl-accept-all", "true"], ["rl-reject-all", "false"]]) {
    $(id).addEventListener("click", async () => {
      const b = $(id);
      if (!b.classList.contains("sure")) {  // every change at once: asked once first
        b.classList.add("sure");
        b.dataset.label = b.textContent;
        b.textContent = `${b.textContent}?`;
        return;
      }
      b.classList.remove("sure");
      b.textContent = b.dataset.label;
      b.disabled = true;
      if (view.editing) await reviewInEditor(accept === "true");
      else await redline("resolve", { everything: "true", accept }, rlError);
      b.disabled = false;
    });
  }

  // Converting a long PDF takes seconds. When it is done, the two are shown
  // side by side: the first thing to check is what the conversion kept.
  $("rl-make-word").addEventListener("click", async () => {
    const b = $("rl-make-word"), label = b.textContent;
    b.disabled = true;
    b.textContent = "Converting…";
    const made = await redline("editable", {}, rlError);
    b.disabled = false;
    b.textContent = label;
    if (made && made.converted) {
      $("v-compare").checked = view.compare = true;
      showTab("original");
    }
  });

  $("rl-draft").addEventListener("click", async () => {
    const instruction = $("rl-instruction").value.trim();
    if (!instruction) { rlError("Say what should change first."); return; }
    rlError("");
    $("rl-draft").disabled = true;
    const started = Date.now();
    const tick = () => { $("rl-status").textContent = `Drafting… ${Math.round((Date.now() - started) / 1000)}s`; };
    tick();
    const timer = setInterval(tick, 1000);
    const reply = await throughEditor(() => redline("draft", { instruction }, rlError));
    clearInterval(timer);
    $("rl-status").textContent = "";
    $("rl-draft").disabled = false;
    if (!reply) return;
    $("rl-instruction").value = "";
    const box = $("rl-drafted"), drafted = reply.drafted || {};
    box.replaceChildren();
    const summary = document.createElement("p");
    summary.textContent = `${drafted.summary || "Edits suggested."} ${plural((drafted.placed || []).length, "edit")} written as tracked changes by AEGIS AI.`;
    box.append(summary);
    for (const note of drafted.skipped || []) {
      const skip = document.createElement("p");
      skip.className = "skip";
      skip.textContent = `Left out — ${note}`;
      box.append(skip);
    }
    box.hidden = false;
  });

  // --- Notes (Phase 2) ------------------------------------------------------------

  function noteError(message) {
    $("n-error").textContent = message;
    $("n-error").hidden = !message;
  }

  async function post(path, fields) {
    if (!state.result) { noteError("Open a document first."); return false; }
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    const res = await fetch(`${BASE}/docstudio/dev/${path}`, { method: "POST", body });
    if (!res.ok) { noteError(await detail(res)); return false; }
    Object.assign(state.result, await res.json());
    noteError("");
    paint();
    return true;
  }

  $("n-add").addEventListener("click", async () => {
    const done = await post("notes", {
      document_id: state.result?.document_id ?? "",
      version_id: state.result?.version_id ?? "",
      quote: $("n-quote").value,
      body: $("n-body").value,
      kind: $("n-kind").value,
    });
    if (done) { $("n-quote").value = ""; $("n-body").value = ""; }
  });

  // Built with textContent: a note and its words are shown, never parsed as markup.
  function paintLost() {
    const list = $("lost");
    list.replaceChildren();
    for (const note of state.result.lost || []) {
      const item = document.createElement("div");
      item.className = "lost";
      const what = document.createElement("p");
      what.textContent = `Lost note: “${note.body}” — it was on: “${note.quote}”`;
      const words = document.createElement("input");
      words.type = "text";
      words.placeholder = "The words it belongs on now";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn";
      button.textContent = "Re-link";
      button.addEventListener("click", () => post(`notes/${encodeURIComponent(note.id)}/relink`, { quote: words.value }));
      item.append(what, words, button);
      list.append(item);
    }
  }

  $("download").addEventListener("click", () => {
    const r = state.result;
    if (!r) return;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([r.report], { type: "text/markdown" }));
    link.download = `${r.name.replace(/\.[^.]+$/, "")}.report.md`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  });


  // --- Marks on a PDF: suggested changes beside the page, never in it ------------
  // A PDF's words are painted in place, so a suggested change is a mark on its
  // words (marks.py on the server). Agreed marks become a marked-up PDF, a Word
  // version with tracked changes, words written into the PDF, or an amendment.

  function changeKind(c) {
    const old = (c.quote || "").split(/\s+/).join(" "), now = (c.proposed_text || "").split(/\s+/).join(" ").trim();
    if (!now) return "delete";
    if (now.startsWith(old)) return "insert";
    if (now.endsWith(old)) return "insert";
    return "replace";
  }

  function describeChange(c) {
    const old = (c.quote || "").split(/\s+/).join(" ").trim(), now = (c.proposed_text || "").split(/\s+/).join(" ").trim();
    if (!now) return ["Delete", old, ""];
    if (now.startsWith(old)) return ["Insert after", old, now.slice(old.length).trim()];
    if (now.endsWith(old)) return ["Insert before", old, now.slice(0, now.length - old.length).trim()];
    return ["Replace", old, now];
  }

  // What a mark says, built with textContent: a contract's words never become markup.
  function changeLine(c) {
    const [verb, old, now] = describeChange(c), line = document.createElement("p");
    line.className = "body change-line";
    line.append(`${verb} `);
    const was = document.createElement(verb.startsWith("Insert") ? "span" : "del");
    was.textContent = old;
    line.append(was);
    if (now) {
      line.append(verb.startsWith("Insert") ? " : " : " with ");
      const added = document.createElement("ins");
      added.textContent = now;
      line.append(added);
    }
    return line;
  }

  async function markAction(url, method, fields, error) {
    error.hidden = true;
    try {
      const init = { method };
      if (fields) {
        init.body = new FormData();
        for (const [key, value] of Object.entries(fields)) init.body.append(key, value);
      }
      const res = await fetch(`${BASE}/docstudio/dev/${url}`, init);
      if (!res.ok) throw new Error(await detail(res));
      Object.assign(state.result, await res.json());
      paint();
      return true;
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
      layoutCards();
      return false;
    }
  }

  const STATUS = { open: "Suggested", agreed: "Agreed", rejected: "Rejected" };

  function statusButtons(c, error) {
    const id = encodeURIComponent(c.id), buttons = [];
    const set = (status) => markAction(`marks/${id}/status`, "POST", { status }, error);
    if (c.status !== "agreed") buttons.push(smallButton("link yes", "Agree", "Agree to this change", () => set("agreed")));
    if (c.status !== "rejected") buttons.push(smallButton("link no", "Reject", "Reject this change", () => set("rejected")));
    if (c.status !== "open") buttons.push(smallButton("link", "Reopen", "Undecide", () => set("open")));
    buttons.push(deleteButton("Delete this suggestion", () => markAction(`comments/${id}`, "DELETE", null, error)));
    return buttons;
  }

  function markCard(c, top) {
    const card = document.createElement("div");
    card.className = `card mark ${c.status}`;
    card.dataset.id = c.id;
    card.want = top ?? 0;
    const error = document.createElement("p");
    error.className = "error";
    error.hidden = true;
    const tools = header(card, c.created_at, c.author || "You");
    const tag = document.createElement("span");
    tag.className = `tag ${c.status}`;
    tag.textContent = STATUS[c.status] || c.status;
    tools.append(tag);
    card.append(changeLine(c));
    if (c.body) {
      const why = document.createElement("p");
      why.className = "on-what";
      why.textContent = c.body;
      card.append(why);
    }
    const about = [c.label ? `on ${c.label}` : "", top === null ? "its words were not found on the page" : ""]
      .filter(Boolean).join(" · ");
    if (about) {
      const on = document.createElement("p");
      on.className = "on-what";
      on.textContent = about;
      card.append(on);
    }
    const acts = document.createElement("div");
    acts.className = "acts wrap";
    acts.append(...statusButtons(c, error));
    card.append(acts, error);
    card.addEventListener("click", () => activate(c.id, true));
    return card;
  }

  function highlightCard(c, top) {
    const card = document.createElement("div");
    card.className = "card mark hi";
    card.dataset.id = c.id;
    card.want = top ?? 0;
    const error = document.createElement("p");
    error.className = "error";
    error.hidden = true;
    header(card, c.created_at, c.author || "You").append(
      deleteButton("Remove this highlight", () => markAction(`comments/${encodeURIComponent(c.id)}`, "DELETE", null, error)),
    );
    const line = document.createElement("p");
    line.className = "on-what";
    line.textContent = `Highlighted${c.label ? ` · on ${c.label}` : ""}`;
    card.append(line, error);
    card.addEventListener("click", () => activate(c.id, true));
    return card;
  }

  function paintMarks() {
    const r = state.result, list = $("mk-list");
    const marks = (r.comments || []).filter((c) => c.kind === "proposal");
    const live = marks.filter((c) => c.status !== "rejected");
    for (const count of [$("changes-count")]) {
      count.textContent = String(live.length);
      count.hidden = !live.length;
    }
    const agreed = marks.filter((c) => c.status === "agreed").length;
    $("mk-count").textContent = marks.length
      ? `${plural(marks.length, "suggested change")} · ${agreed} agreed` : "No suggested changes";
    $("mk-agree-all").disabled = !marks.some((c) => c.status === "open");
    $("mk-note").textContent = r.scanned
      ? "This is a scan: every page is a photograph, so its words are what OCR read. A Word version is rebuilt from that reading — check it against the page before sending it."
      : "A PDF's words are painted in place, so a change to it is a mark beside its words — the file itself is not changed until you choose what to make of the agreed marks.";
    $("out-patch-row").hidden = $("out-look-row").hidden = !!r.scanned;
    rlError("");
    list.replaceChildren();
    for (const c of marks) {
      const row = document.createElement("div");
      row.className = `chg mark ${c.status}`;
      const top = document.createElement("div");
      top.className = "top";
      const avatar = document.createElement("span");
      avatar.className = "av";
      avatar.textContent = (c.author || "?").trim().charAt(0).toUpperCase();
      avatar.style.background = colourOf(c.author);
      const who = document.createElement("b");
      who.textContent = c.author || "You";
      const tag = document.createElement("span");
      tag.className = `tag ${c.status}`;
      tag.textContent = STATUS[c.status] || c.status;
      const where = document.createElement("span");
      where.className = "when";
      where.textContent = c.label ? `on ${c.label}` : "";
      const error = document.createElement("p");
      error.className = "error";
      error.hidden = true;
      const acts = document.createElement("span");
      acts.className = "acts";
      acts.append(...statusButtons(c, error));
      top.append(avatar, who, tag, where, acts);
      row.append(top, changeLine(c));
      if (c.body) {
        const why = document.createElement("p");
        why.className = "why";
        why.textContent = c.body;
        row.append(why);
      }
      row.append(error);
      row.addEventListener("click", () => {
        if (view.editing) { activate(c.id, true); return; }
        showTab("original");
        drawOriginal().then(() => activate(c.id, true));
      });
      list.append(row);
    }
  }

  $("mk-agree-all").addEventListener("click", async () => {
    const open = (state.result.comments || []).filter((c) => c.kind === "proposal" && c.status === "open");
    const error = $("rl-error");
    for (const c of open) {
      if (!await markAction(`marks/${encodeURIComponent(c.id)}/status`, "POST", { status: "agreed" }, error)) break;
    }
  });

  // --- What to make of the agreed marks -------------------------------------------

  function outResult(parts, bad = false) {
    const box = $("out-result");
    box.replaceChildren(...parts.filter(Boolean).map((text) => {
      const p = document.createElement("p");
      p.textContent = text;
      return p;
    }));
    box.classList.toggle("bad", bad);
    box.hidden = !parts.length;
  }

  function refusals(list) {
    return (list || []).map((x) => `Not made — “${x.find}”: ${x.why}`);
  }

  async function download(path, fallbackName) {
    const res = await fetch(`${BASE}/docstudio/dev/${path}`);
    if (!res.ok) throw new Error(await detail(res));
    const disposition = res.headers.get("Content-Disposition") || "";
    const name = (disposition.match(/filename="([^"]+)"/) || [])[1] || fallbackName;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(await res.blob());
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    return res;
  }

  $("out-marked").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      const res = await download(`versions/${encodeURIComponent(state.result.version_id)}/marked-up`, "marked-up.pdf");
      const onWords = res.headers.get("X-Marks-On-Words"), asNotes = res.headers.get("X-Marks-As-Notes");
      outResult([
        `Downloaded. ${onWords} mark(s) sit on their words${asNotes !== "0" ? `, ${asNotes} as notes on their clause (their words could not be found exactly on the page)` : ""}.`,
        "The file begins with the original's exact bytes, so a signature over them still verifies.",
      ]);
    } catch (err) {
      outResult([err.message], true);
    }
    b.disabled = false;
  });

  $("out-word").addEventListener("click", async (e) => {
    const b = e.currentTarget, label = b.textContent;
    b.disabled = true;
    b.textContent = "Making it…";
    const made = await postTo("marks/carry", {}, (message) => outResult([message], true));
    b.disabled = false;
    b.textContent = label;
    if (made && made.carried) {
      outResult([`A Word version was made with ${plural(made.carried.carried, "agreed change")} as tracked changes.`,
                 ...refusals(made.carried.refused)]);
      showTab("changes");
    }
  });

  $("out-patch").addEventListener("click", async (e) => {
    const b = e.currentTarget, label = b.textContent;
    b.disabled = true;
    b.textContent = "Writing and checking…";
    const done = await postTo("marks/patch", {}, (message) => outResult([message], true));
    b.disabled = false;
    b.textContent = label;
    if (done && done.patched) {
      const n = done.patched.patched;
      outResult([
        n ? `${plural(n, "agreed change")} written into the PDF as its next version. Checked: read back word for word, nothing else on the page moved, and the original's bytes kept.`
          : "Nothing was written into the PDF.",
        ...refusals(done.patched.refused),
        done.patched.refused.length ? "Those can go into the Word version or an amendment instead." : "",
      ], !n);
    }
  });

  $("out-look").addEventListener("click", async (e) => {
    const b = e.currentTarget, label = b.textContent;
    b.disabled = true;
    b.textContent = "Converting…";
    try {
      await download(`versions/${encodeURIComponent(state.result.version_id)}/word-lookalike`, "looks-the-same.docx");
      outResult(["Downloaded. It looks like the PDF because each line is a box of its own — send it, but redline the editable Word version instead."]);
    } catch (err) {
      outResult([err.message], true);
    }
    b.disabled = false;
    b.textContent = label;
  });

  $("out-reading").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      await download(`versions/${encodeURIComponent(state.result.version_id)}/reading`, "what-was-read.md");
      outResult(["Downloaded. It records, block by block, what was read and how the page looked — correct a word in it and the Word version can be built again from it."]);
    } catch (err) {
      outResult([err.message], true);
    }
    b.disabled = false;
  });

  $("out-amend").addEventListener("click", async (e) => {
    const b = e.currentTarget;
    b.disabled = true;
    try {
      await download(`versions/${encodeURIComponent(state.result.version_id)}/amendment`, "amendment.docx");
      outResult(["Downloaded the amendment. The contract itself is not changed."]);
    } catch (err) {
      outResult([err.message], true);
    }
    b.disabled = false;
  });

  // --- The selection toolbar: highlight, formatting, whole-clause editing ----------

  function toolbarPicked() {
    const picked = pickedWords();
    floatButton.hidden = true;
    if (!picked) return null;
    return { ...picked, ...around(picked.range) };
  }

  $("mark-button").addEventListener("click", async () => {
    const picked = toolbarPicked();
    if (!picked) return;
    getSelection().removeAllRanges();
    await postTo("highlights", { quote: picked.quote, prefix: picked.prefix, suffix: picked.suffix }, (m) => say(m));
  });

  for (const style of ["bold", "italic", "underline"]) {
    $(`fmt-${style}`).addEventListener("click", async () => {
      const picked = toolbarPicked();
      if (!picked) return;
      getSelection().removeAllRanges();
      const done = await postTo("redline/format", { quote: picked.quote, prefix: picked.prefix, suffix: picked.suffix, style },
        (m) => say(m));
      if (done) say(`${style[0].toUpperCase()}${style.slice(1)} applied as a tracked formatting change — see Changes.`);
    });
  }

  // Word-like typing, a clause at a time: the page stays exactly as it is, and
  // only the words the reader changed become tracked changes or marks.
  const ceError = (message) => { $("ce-error").textContent = message; $("ce-error").hidden = !message; };
  function closeClause() {
    $("clause-editor").hidden = true;
    view.clause = null;
  }

  $("clause-button").addEventListener("click", async () => {
    const picked = toolbarPicked();
    if (!picked) return;
    getSelection().removeAllRanges();
    const body = new FormData();
    for (const [key, value] of Object.entries({ version_id: state.result.version_id, quote: picked.quote,
                                                  prefix: picked.prefix, suffix: picked.suffix })) body.append(key, value);
    try {
      const res = await fetch(`${BASE}/docstudio/dev/clauses/at`, { method: "POST", body });
      if (!res.ok) { say(await detail(res)); return; }
      view.clause = await res.json();
    } catch (err) {
      say(`Could not reach the server: ${err.message}`);
      return;
    }
    $("ce-label").textContent = view.clause.label ? `· ${view.clause.label}` : "";
    $("ce-note").textContent = state.result.editable
      ? "Type as in Word. Only the words you change are saved, as tracked changes by You that can be accepted or rejected."
      : "Type as in Word. The PDF is not changed: each word you change becomes a suggested change on the page.";
    $("ce-text").value = view.clause.text;
    $("ce-status").textContent = "";
    ceError("");
    $("clause-editor").hidden = false;
    $("ce-text").focus();
  });

  $("ce-cancel").addEventListener("click", closeClause);
  $("clause-editor").addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeClause();
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("ce-save").click();
  });
  $("ce-save").addEventListener("click", async () => {
    if (!view.clause) return;
    $("ce-save").disabled = true;
    $("ce-status").textContent = "Saving…";
    const done = await throughEditor(() =>
      postTo("clauses/edit", { clause_id: view.clause.clause_id, text: $("ce-text").value }, ceError));
    $("ce-save").disabled = false;
    $("ce-status").textContent = "";
    if (!done) return;
    closeClause();
    const { placed, refused } = done.edited || {};
    say([`${plural(placed || 0, "change")} saved${state.result.editable ? " as tracked changes" : " as suggested changes on the page"}.`,
         ...(refused || []).map((why) => `Not placed: ${why}`)].join(" "));
  });

  // --- History: every version, kept; open one, download it, compare it ---------------

  function paintHistoric() {
    const old = view.historic;
    $("hist-note").hidden = !old;
    if (old) $("hist-text").textContent = `Viewing version ${old.number}, read-only — ${old.what}.`;
  }
  $("hist-back").addEventListener("click", () => {
    view.historic = null;
    paintHistoric();
    drawOriginal().then(paintComments);
  });

  async function loadHistory() {
    const list = $("hs-list"), error = $("hs-error");
    error.hidden = true;
    try {
      const res = await fetch(`${BASE}/docstudio/dev/documents/${encodeURIComponent(state.result.document_id)}/history`);
      if (!res.ok) throw new Error(await detail(res));
      const { versions } = await res.json();
      const latest = versions.find((v) => v.current) || versions[0];
      list.replaceChildren(...versions.map((v) => {
        const row = document.createElement("div");
        row.className = `hs-row${v.current ? " current" : ""}`;
        const head = document.createElement("div");
        head.className = "top";
        const number = document.createElement("b");
        number.textContent = `Version ${v.number}`;
        const what = document.createElement("span");
        what.className = "what";
        what.textContent = v.what;
        const stamp = document.createElement("span");
        stamp.className = "when";
        stamp.textContent = `${when(v.created_at)} · ${v.mime.includes("pdf") ? "PDF" : v.mime.includes("word") ? "Word" : "text"}`;
        head.append(number, what, stamp);
        const acts = document.createElement("div");
        acts.className = "acts";
        if (v.current) {
          const tag = document.createElement("span");
          tag.className = "tag agreed";
          tag.textContent = "Latest";
          acts.append(tag);
        } else {
          acts.append(smallButton("link", "View", "Open this version, read-only", () => {
            view.historic = { version_id: v.version_id, mime: v.mime, number: v.number, kept: v.kept, what: v.what };
            showTab("original");
            paintHistoric();
          }));
          acts.append(smallButton("link", "Compare with the latest", "Which words changed since this version",
            () => compareWith(v, latest)));
        }
        if (v.kept) {
          const link = document.createElement("a");
          link.className = "link";
          link.textContent = "Download";
          link.href = `${BASE}/docstudio/dev/versions/${encodeURIComponent(v.version_id)}/file`;
          link.setAttribute("download", v.filename || `version-${v.number}`);
          acts.append(link);
        }
        row.append(head, acts);
        return row;
      }));
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }

  async function compareWith(older, newer) {
    const view_ = $("hs-compare"), error = $("hs-error");
    error.hidden = true;
    try {
      const res = await fetch(`${BASE}/docstudio/dev/compare?older=${encodeURIComponent(older.version_id)}&newer=${encodeURIComponent(newer.version_id)}`);
      if (!res.ok) throw new Error(await detail(res));
      const diff = await res.json();
      const head = document.createElement("h3");
      head.textContent = `Version ${diff.older} → version ${diff.newer}`;
      const sum = document.createElement("p");
      sum.className = "muted";
      sum.textContent = diff.rows.length
        ? `${plural(diff.counts.clauses, "clause")} changed · ${diff.counts.words_removed} word(s) removed · ${diff.counts.words_added} added · ${diff.unchanged} clauses unchanged`
        : "No words changed.";
      const rows = diff.rows.map((r) => {
        const row = document.createElement("div");
        row.className = `cmp-row ${r.status}`;
        const label = document.createElement("b");
        label.textContent = r.label;
        const text = document.createElement("p");
        for (const [op, words] of r.parts) {
          const piece = document.createElement(op === "-" ? "del" : op === "+" ? "ins" : "span");
          piece.textContent = `${words} `;
          text.append(piece);
        }
        row.append(label, text);
        return row;
      });
      view_.replaceChildren(head, sum, ...rows);
      view_.hidden = false;
      view_.scrollIntoView({ block: "start", behavior: "smooth" });
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    }
  }


  // --- Editing the document itself: ONLYOFFICE, in the Original tab -----------------
  // It draws the file with its own fonts, sizes and positions and edits it as a
  // word processor does. Save (Ctrl+S) keeps the edits as the next version —
  // the original stays in History — and in a Word file every edit is a tracked
  // change by You.

  // The editor draws the document itself, so the page cannot lay a box over the
  // words the way it does in its own viewer. It leaves them on the server and
  // the plugin inside the editor finds and selects them there.
  async function findInEditor(quote) {
    // Named by the version the editor holds: another editor open on another
    // document must not jump to these words.
    const body = new URLSearchParams({ quote, version_id: view.editing.from });
    await fetch(`${BASE}/docstudio/dev/editor/find`, { method: "POST", body });
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      if (document.querySelector(`script[data-src="${src}"]`)) { resolve(); return; }
      const tag = document.createElement("script");
      tag.src = src;
      tag.dataset.src = src;
      tag.onload = resolve;
      tag.onerror = () => reject(new Error("The document editor is not running. Start it with: docker compose --profile editor up -d onlyoffice"));
      document.head.append(tag);
    });
  }

  // A document opens in the editor, the way a document opens in Word: the file
  // itself, in its own fonts, ready to type in. A PDF is read as a Word
  // document first — that is what the editor edits — and the PDF stays as the
  // version before it.
  async function startEditing(asked) {
    let r = state.result;
    if (view.editing || view.historic || !r || !r.original) return false;
    $("v-edit").disabled = true;
    try {
      if (!r.editable) {
        // The editor holds Word documents. A PDF is read as one, a plain text
        // file is written as one, and anything else stays in the page's own
        // viewer — there is nothing to hand the editor.
        if (!["application/pdf", "text/plain"].includes(r.mime)) return false;
        say(r.mime === "text/plain" ? "Making a Word document of it…" : "Reading the PDF as a Word document…");
        const made = await postTo("redline/editable", {}, (message) => say(message));
        if (!made) return false;
        r = state.result;
      }
      const res = await fetch(`${BASE}/docstudio/dev/editor/config?version_id=${encodeURIComponent(r.version_id)}`);
      if (!res.ok) throw new Error(await detail(res));
      const { server, config } = await res.json();
      await loadScript(`${server}/web-apps/apps/api/documents/api.js`);
      if (view.editing || state.result.version_id !== r.version_id) return false;  // moved on while it loaded
      view.editing = { from: r.version_id, document: r.document_id };
      ++view.token;  // any drawing under way stops here
      view.drawn = null;
      const host = $("viewer");
      host.classList.add("editing");
      $("vbar").classList.add("editing");   // zoom and the like belong to our own viewer
      paintChanges();  // the list reads, but does not act, while the editor holds the file
      const box = document.createElement("div");
      box.id = "oo-editor";
      host.replaceChildren(box);
      floatButton.hidden = true;
      say("Editing the document as it is. Save (Ctrl+S, or the save icon) keeps your changes as a new version; the original stays in History.",
          "Every edit is a tracked change by You, to accept or reject.");
      // What the editor itself complains about — a file it will not open, a
      // session it will not give editing rights to — otherwise happens inside
      // its frame where the page cannot see it, and the document simply sits
      // there refusing to take a change.
      view.editor = new window.DocsAPI.DocEditor("oo-editor", {
        ...config,
        events: {
          onError: (e) => told(e?.data?.errorDescription || e?.data?.errorCode, "reported"),
          onWarning: (e) => told(e?.data?.warningDescription || e?.data?.warningCode, "warned"),
          onRequestEditRights: () => startEditing(true),
          onDocumentReady: () => { view.ready = true; },
        },
      });
      paintOriginal();
      return true;
    } catch (err) {
      // Opening a document must not fail loudly: the page falls back to its own
      // viewer, which reads the file without the editor.
      if (asked) say(err.message);
      else say(`This document is being shown here rather than in the editor: ${err.message}`);
      return false;
    } finally {
      $("v-edit").disabled = false;
    }
  }

  $("v-edit").addEventListener("click", () => startEditing(true));

  // The editor keeps a connection to the document service; when that drops —
  // the service restarted, the network blinked — it goes on showing the
  // document and quietly stops taking changes. Typing then does nothing and
  // nothing says why, which reads as "the document cannot be edited".
  function told(said, how) {
    const text = String(said || "something").replace(/<br\s*\/?>/gi, " ");
    if (!/connection is lost|connection lost/i.test(text)) {
      say(`The editor ${how}: ${text}.`);
      return;
    }
    waitForTheEditor();
  }

  async function waitForTheEditor() {
    if (view.waiting) return;
    view.waiting = true;
    say("The editor lost its connection to the document service, so it has stopped taking changes.",
        "Waiting for the service, then opening the document again at its last save — which is in History either way.");
    try {
      for (let i = 0; i < 100; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const res = await fetch(`${BASE}/docstudio/dev/editor/health`).catch(() => null);
        if (!res || !res.ok) continue;
        const { up } = await res.json();
        if (!up || !view.editing) continue;
        await stopEditing(false);
        await startEditing(false);
        say("The editor is back, opened again on the last saved version.");
        return;
      }
      say("The document service is still not answering. Start it with: docker compose --profile editor up -d onlyoffice");
    } finally {
      view.waiting = false;
    }
  }

  // The editor saves after it closes, a few seconds later; the page waits for
  // the new version rather than showing the old one as if nothing happened.
  async function stopEditing(wait = true) {
    const was = view.editing;
    if (!was) return;
    try { view.editor?.destroyEditor(); } catch { /* already gone */ }
    view.editor = null;
    view.editing = null;
    $("viewer").classList.remove("editing");
    $("vbar").classList.remove("editing");
    paintChanges();
    paintOriginal();
    if (!wait) return;
    say("Saving your changes…");
    for (let i = 0; i < 15; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const res = await fetch(`${BASE}/docstudio/dev/documents/${encodeURIComponent(was.document)}`);
      if (!res.ok) continue;
      const latest = await res.json();
      if (latest.version_id !== was.from || i === 14) {
        state.result = latest;
        view.drawn = null;
        paint();
        loadDocuments();
        await view.drawing;  // the redraw writes its own note; this one comes after it
        say(latest.version_id !== was.from
          ? `Saved as version ${String(latest.how).split("version ").pop()}. The version before is kept in History.`
          : "No changes were saved.");
        return;
      }
    }
  }
  $("v-done").addEventListener("click", () => stopEditing(true));

  // Nothing open yet: the page is the front door until a document is added.
  showUpload(true);
  loadDocuments();
})();
