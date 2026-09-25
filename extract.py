"""Command-line entry point.

    python extract.py samples/foto-danfe-parafusos.jpg
    python extract.py invoice.pdf --out results/
    python extract.py samples/orden-compra-andina.pdf --replay   # no API key needed

Exit codes: 0 = all checks passed, 1 = needs review, 2 = could not process.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.extractor import ExtractionError, InvalidFileError
from app.pipeline import process

ICON = {"ok": "[ OK ]", "warning": "[WARN]", "error": "[FAIL]", "info": "[INFO]"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract and validate data from an NF-e/DANFE or purchase order.")
    ap.add_argument("file", type=Path, help="PDF, JPG or PNG")
    ap.add_argument("--out", type=Path, default=None, help="output folder (default: runs/<timestamp>)")
    ap.add_argument("--replay", action="store_true", help="use saved results for the bundled samples (no API call)")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles: print accents safely
    if not args.file.is_file():
        print(f"error: file not found: {args.file}", file=sys.stderr)
        return 2
    try:
        result = process(args.file.read_bytes(), args.file.name, args.out, mode="replay" if args.replay else None)
    except (InvalidFileError, ExtractionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    d, v = result["document"], result["validation"]
    cur = d.get("currency") or ""
    print(f"\nDEMO - {d['document_type']}  No. {d.get('document_number')}  issued {d.get('issue_date')}")
    print(f"  supplier : {d['supplier'].get('name')}  [{d['supplier'].get('tax_id')}]")
    print(f"  buyer    : {d['buyer'].get('name')}  [{d['buyer'].get('tax_id')}]")
    print(f"  items    : {len(d['items'])}   subtotal {d.get('items_subtotal')}   total {d.get('grand_total')} {cur}")
    print(f"  model    : {result['model']}  ({result['timing']['extract_seconds']} s, {result['attempts']} attempt(s))")
    print("\nValidation:")
    for c in v["checks"]:
        print(f"  {ICON[c['status']]} {c['title']}: {c['detail']}")
    out = Path(result["out_dir"])
    print("\nOutputs:")
    for kind in ("json", "xlsx", "pdf"):
        print(f"  {kind:5s} {out / result['files'][kind]}")
    return 0 if v["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
