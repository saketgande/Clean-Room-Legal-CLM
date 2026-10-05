---
name: repo-graph
description: Regenerate the repo knowledge graph (domains, DB models, API endpoints, frontend pages/typed-client, migrations) that architect/db-engineer/backend-dev/frontend-dev/qa-engineer read as context during /plan and /implement. Usable any time, in any session.
---

# /repo-graph — regenerate the repo knowledge graph

Read-only with respect to source code; writes only under `specs/_graph/`. Safe to run
any time — cheap (single-digit seconds), and `/plan` and `/implement` already run it
automatically at their start, so a manual run is for inspecting drift mid-session or
after a big refactor.

## Procedure

1. **Run the scanner**: `python scripts/build_repo_graph.py` from the repo root.
   It rewrites `specs/_graph/repo-graph.json`, `specs/_graph/index.md`, and
   `specs/_graph/domains/<domain>.md` (one per backend domain). All three are
   generated — never hand-edit them.
2. **Surface the warnings** printed to stdout (also in the JSON's
   `metadata.warnings` and mirrored in `index.md`'s Warnings section) as a short
   list to the user. Common categories:
   - A frontend `Api` group or backend domain with no counterpart match — usually
     legitimate (e.g. `core`, `observability`, `integrations` are backend-only; a
     group can also legitimately span more than one backend domain, which a strict
     1:1 pairing can't represent). Only add an entry to
     `specs/_graph/domain-aliases.json` (`{"backend_domain": "frontendGroupName"}`)
     if the heuristic genuinely mismatched two things that ARE a real 1:1 pair —
     don't force a pairing that doesn't exist just to silence a warning.
   - A frontend client call that didn't match any backend endpoint by method+path —
     can mean a real bug (e.g. a malformed path string), not just a scanner miss.
     Worth a second look, not automatic dismissal.
3. **Report a short summary**: node/edge counts, warning count, and
   `git diff --stat specs/_graph/repo-graph.json` against the previous committed
   version (if any) as the "what changed since last run" signal.
4. **Do not commit automatically** — leave the regenerated files staged/modified for
   the user's normal review and commit flow, same as any other generated artifact.

## Rules

- Never hand-edit `repo-graph.json`, `index.md`, or `domains/*.md` — re-run the
  scanner instead. `domain-aliases.json` is the one hand-maintained file.
- This is a structural, static-analysis snapshot (stdlib `ast` for backend, a
  depth-aware scan for frontend) — not a live/dynamic graph. It won't catch every
  runtime behavior; agents still read the real source file before writing code.
- If the scanner errors outright (not just warnings), that's a bug in
  `scripts/build_repo_graph.py`, not a workflow gate — report it to the user rather
  than silently skipping graph regeneration.
