# Docstudio viewer — Phase 3

The document workspace: outline · pages · clause panel.

## What is real and what is a placeholder

| | |
|---|---|
| Clause geometry, ids, tree, structure source | **real** — extracted from five genuine client documents |
| Outline, selection, scroll sync, clause panel, orphan queue | **real** — final code, unchanged by the swap below |
| The page image | **placeholder** — `PageSurface.tsx` draws each clause's text inside its own stored box |
| The data source | **placeholder** — `api.ts` reads JSON fixtures, not the API |

Both placeholders are one file each. Nothing else in the feature knows.

## Wiring it to the real backend

1. **`api.ts` → `loadVersion`.** Point it at `GET /docstudio/versions/{id}` and
   return the same `VersionBundle`. Serve annotations from `ds_annotation`
   instead of `mockAnnotations`.
2. **`PageSurface.tsx`.** Replace the body with a pdf.js canvas plus pdf.js's
   own transparent text layer, sized to the same box the component already
   receives. Serve the bytes `Content-Disposition: attachment` and parse them
   in JS — never hand a blob to an `<iframe>`, and match image MIME types by
   exact name, since `startsWith("image/")` also accepts SVG.
3. **`types.ts` → `regionsOf`.** Return `ds_clause.source_regions` directly and
   set `regions_known: true`. The approximation it replaces is documented in
   the function.
4. **Delete** `public/docstudio-mock/`, `scripts/report-to-fixture.mjs`, the
   fixture half of `docstudio.test.ts`, and the "Reconstructed pages" banner in
   `DocstudioWorkspace.tsx`.

Real page sizes should come with the version — `DocumentView` currently assumes
US Letter (`PAGE_ASPECT`).

## Regenerating the fixtures

```
node scripts/report-to-fixture.mjs <file>.report.md public/docstudio-mock
```

## What is deliberately not here

Authoring (free-form typing), real-time collaboration, and character-level
anchoring. See §1 of the build plan.
