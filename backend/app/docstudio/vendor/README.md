# Vendored for the docstudio dev page only

The same builds the app's document view uses, copied from the frontend's
`node_modules` so the dev page draws a file with exactly the product's code.
Served by `devui.py`'s `/vendor/{name}` route, which exists only outside
production.

| File | Package | Version | Licence |
|---|---|---|---|
| `pdf.min.mjs`, `pdf.worker.min.mjs` | pdfjs-dist | 4.10.38 | Apache-2.0 (`LICENSE.pdfjs`) |
| `docx-preview.min.js` | docx-preview | 0.4.0 | Apache-2.0 (`LICENSE.docx-preview`) |
| `jszip.min.js` | jszip | 3.10.2 | MIT, chosen from MIT OR GPL-3.0 (`LICENSE.jszip.md`) |

To update, bump the package in `frontend/package.json` first, then copy the
same four files from the frontend container's `node_modules` — never a
different version from the app's.
