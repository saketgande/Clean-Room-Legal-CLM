"""Run Phase 1 on one file and write what it produced.

    python -m app.docstudio contract.pdf           # cut, arrange with AI, check, report
    python -m app.docstudio contract.pdf --no-ai   # ... keeping the numbering's structure
    python -m app.docstudio - --name contract.pdf < contract.pdf

From the host, `make phase1 FILE="$HOME/Downloads/contract.pdf"` streams the
file in, so it can live anywhere and its name can contain spaces.

Output goes to `docstudio_out/` — `backend/docstudio_out/` on the host, which
is git-ignored because every report carries the contract's full text.

Two behaviours worth knowing before running it on a scan:

* **The same file twice is free.** The earlier ingest is found by its SHA-256
  and reused, so a scanned contract is OCR'd once however often it is run.
* **Arranging calls Claude once per version** — a few cents — and applies only
  each clause's parent and level. `--no-ai` skips it; so does a missing key.
"""

import argparse
import sys
from pathlib import Path

import app.models  # noqa: F401  (register every mapper)
from app.core.database import SessionLocal

from .runner import mime_for, run

DEFAULT_OUT = "docstudio_out"


def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="python -m app.docstudio",
        description="Run docstudio Phase 1 on one file and write its report.",
    )
    parser.add_argument("source", help="the file to read, or - to read it from stdin")
    parser.add_argument("--name", help="the file's name, required when reading from stdin")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"output folder (default {DEFAULT_OUT})")
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="keep the structure the numbering gives, without asking Claude to arrange it",
    )
    parser.add_argument(
        "--version-of",
        metavar="DOCUMENT_ID",
        help="read the file as a new version of this document (its id is in the report), "
        "so the document's notes are found again in the new text",
    )
    args = parser.parse_args(argv)

    if args.source == "-":
        if not args.name:
            parser.error("--name is required when reading from stdin")
        content, name = sys.stdin.buffer.read(), args.name
    else:
        path = Path(args.source)
        if not path.is_file():
            parser.error(f"no such file: {path}")
        content, name = path.read_bytes(), path.name

    if mime_for(name) is None:
        parser.error(f"cannot read {Path(name).suffix or 'a file with no extension'}: use .pdf, .docx or .txt")
    if not content:
        parser.error("the file is empty")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = Path(name).stem

    db = SessionLocal()
    try:
        output = run(db, content=content, name=name, ai=not args.no_ai, version_of=args.version_of)
    except LookupError as exc:
        parser.error(str(exc))
    finally:
        db.close()

    report_path = out / f"{stem}.report.md"
    report_path.write_text(output.report_md, encoding="utf-8")
    print(name)
    print(f"  read        {output.how}")
    print(f"  clauses     {output.clause_count}")
    print(f"  pages       {output.page_count or '-'}")
    print(f"  findings    {output.finding_count}")
    print(f"  structure   {output.structure}")
    if output.notes != "0":
        print(f"  notes       {output.notes}")
    print(f"  report   -> {report_path}")
    return output.result


if __name__ == "__main__":
    main()
