"use client";

// A PDF drawn as it is: every page painted to a canvas, with a transparent
// text layer sitting exactly on top.
//
// That layer is why a scan is still worth showing. The picture is the document
// — signatures, stamps, handwriting — and the invisible words over it are what
// make it selectable and searchable, and in Phase 4 what makes a clause
// clickable. It is the same "searchable image" shape Acrobat produces and the
// one Relativity and Mike's viewer use.

import { useEffect, useRef, useState } from "react";

const ZOOMS = [0.75, 1, 1.25, 1.5, 2];

export function PdfRenderer({ bytes }: { bytes: ArrayBuffer }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const [pages, setPages] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const host = hostRef.current;
    if (!host) return;

    (async () => {
      const pdfjs = await import("pdfjs-dist");
      // The worker ships with the library; resolving it through the bundler
      // keeps it on this origin (no CDN) and matches the exact version.
      pdfjs.GlobalWorkerOptions.workerSrc = new URL(
        "pdfjs-dist/build/pdf.worker.min.mjs",
        import.meta.url,
      ).toString();

      let doc;
      try {
        // A copy, because pdf.js takes ownership of the buffer it is handed
        // and a re-render would otherwise find it detached.
        doc = await pdfjs.getDocument({ data: bytes.slice(0) }).promise;
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "This PDF could not be opened.");
        return;
      }
      if (cancelled) {
        doc.destroy();
        return;
      }
      setPages(doc.numPages);
      host.replaceChildren();

      // Fit the page to the panel, then apply the reader's zoom on top.
      const first = await doc.getPage(1);
      const natural = first.getViewport({ scale: 1 }).width;
      const scale = ((host.clientWidth || natural) / natural) * zoom;
      const ratio = window.devicePixelRatio || 1;

      for (let number = 1; number <= doc.numPages; number += 1) {
        if (cancelled) break;
        const page = await doc.getPage(number);
        const viewport = page.getViewport({ scale });

        const wrapper = document.createElement("div");
        wrapper.className = "pg";
        wrapper.style.width = `${viewport.width}px`;
        wrapper.style.height = `${viewport.height}px`;
        host.appendChild(wrapper);

        // Painted at the screen's real pixel density. Painted at its CSS size, a
        // high-density screen (every recent Mac) stretches the canvas 2x and
        // every letter goes soft — found checking this against the file.
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const context = canvas.getContext("2d");
        if (!context) continue;
        wrapper.appendChild(canvas);
        await page.render({
          canvasContext: context,
          viewport,
          transform: ratio === 1 ? undefined : [ratio, 0, 0, ratio, 0, 0],
        }).promise;

        const layer = document.createElement("div");
        layer.className = "tl";
        // pdf.js positions its spans from this variable.
        layer.style.setProperty("--scale-factor", String(scale));
        wrapper.appendChild(layer);
        await new pdfjs.TextLayer({
          textContentSource: page.streamTextContent(),
          container: layer,
          viewport,
        }).render();
      }
      if (cancelled) doc.destroy();
    })();

    return () => {
      cancelled = true;
    };
  }, [bytes, zoom]);

  return (
    <div className="pdfv">
      <div className="bar">
        <span className="muted">{pages ? `${pages} page${pages === 1 ? "" : "s"}` : "Opening…"}</span>
        <span className="sp" />
        <button
          type="button"
          onClick={() => setZoom((z) => ZOOMS[Math.max(0, ZOOMS.indexOf(z) - 1)] ?? z)}
          disabled={zoom === ZOOMS[0]}
        >
          −
        </button>
        <span className="muted zm">{Math.round(zoom * 100)}%</span>
        <button
          type="button"
          onClick={() => setZoom((z) => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(z) + 1)] ?? z)}
          disabled={zoom === ZOOMS[ZOOMS.length - 1]}
        >
          +
        </button>
      </div>
      {error ? <p className="err">{error}</p> : null}
      <div className="pages" ref={hostRef} />
    </div>
  );
}
