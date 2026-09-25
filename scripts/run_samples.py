"""Run the real pipeline on every bundled sample and score it against the ground truth.

    python scripts/run_samples.py            # live: calls the Claude API
    python scripts/run_samples.py --replay   # replay the saved outputs through the pipeline (writes to tmp/)
    python scripts/run_samples.py --rescore  # re-score the saved live outputs, rewrite accuracy.md (no API call)

Writes examples/output/<sample>.json|.xlsx|.report.pdf and examples/output/accuracy.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.extractor import ExtractionError, InvalidFileError  # noqa: E402
from app.pipeline import process  # noqa: E402

SAMPLES = ROOT / "samples"
TRUTH = SAMPLES / "truth"
OUT = ROOT / "examples" / "output"

HEADER_FIELDS = ["document_type", "document_number", "series", "issue_date", "due_or_delivery_date", "currency",
                 "items_subtotal", "discount", "freight", "insurance", "other_charges", "tax_added",
                 "grand_total", "nfe_access_key"]
PARTY_FIELDS = ["name", "tax_id", "tax_id_type", "country"]
ITEM_FIELDS = ["code", "description", "quantity", "unit", "unit_price", "line_total"]


def norm(v):
    """Normalise for comparison: numbers as floats, text case/space/accent-insensitive,
    tax IDs without punctuation, 0.0 and null treated alike for optional charges."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    s = unicodedata.normalize("NFKD", str(v)).encode("ascii", "ignore").decode().casefold()
    s = re.sub(r"\s+", " ", s).strip()
    if re.fullmatch(r"[0-9a-z./\-\s]+", s) and any(ch.isdigit() for ch in s):
        s = re.sub(r"[./\-\s]", "", s).lstrip("0") or "0"  # numbers / IDs: ignore punctuation, leading zeros
    return s


# Written into accuracy.md so the "Fields correct" column can be checked by hand.
NORMALISATION = (
    "How fields are compared (`norm()` in `scripts/run_samples.py`): text ignores case, accents and repeated "
    "spaces; numbers and IDs written with digits and `. / -` ignore that punctuation and leading zeros "
    "(`000.004.217` = `4217`, `001` = `1`); amounts are compared as numbers; for discount, freight, insurance, "
    "other charges and taxes, `0` and `null` (not printed) count as equal. *Exact match* counts the same fields "
    "with no normalisation at all: identical strings, equal numbers."
)


def exact(a, b, field=""):
    """Strict comparison: identical strings, equal numbers, nothing normalised."""
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def same(a, b, field=""):
    na, nb = norm(a), norm(b)
    if field in {"discount", "freight", "insurance", "other_charges", "tax_added"}:
        na, nb = na or None, nb or None  # 0 == not printed
    return na == nb


def score(truth: dict, got: dict, compare=same) -> tuple[int, int, list[str]]:
    ok = total = 0
    misses = []

    def cmp(path, a, b, field):
        nonlocal ok, total
        total += 1
        if compare(a, b, field):
            ok += 1
        else:
            misses.append(f"{path}: expected {a!r}, got {b!r}")

    for f in HEADER_FIELDS:
        cmp(f, truth.get(f), got.get(f), f)
    for role in ("supplier", "buyer"):
        for f in PARTY_FIELDS:
            cmp(f"{role}.{f}", truth[role].get(f), got.get(role, {}).get(f), f)
    t_items, g_items = truth["items"], got.get("items", [])
    cmp("items.count", len(t_items), len(g_items), "count")
    for i, ti in enumerate(t_items):
        gi = g_items[i] if i < len(g_items) else {}
        for f in ITEM_FIELDS:
            cmp(f"items[{i + 1}].{f}", ti.get(f), gi.get(f), f)
    return ok, total, misses


def load_saved(sample: Path) -> dict:
    """The result a LIVE run saved in examples/output/ for this sample (used by --rescore)."""
    path = OUT / sample.stem / f"{sample.stem}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    if saved.get("mode") != "live":
        raise ValueError(f"{path} is not the result of a live run")
    return saved


def main() -> int:
    ap = argparse.ArgumentParser()
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--replay", action="store_true", help="re-use saved outputs instead of calling the API")
    how.add_argument("--rescore", action="store_true",
                     help="score the saved live outputs again and rewrite examples/output/accuracy.md (no API call)")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)

    lines = ["# Accuracy on the bundled samples", "",
             "Field-by-field comparison between the extraction and the ground truth written by "
             "`scripts/make_samples.py` (what is printed on each fictitious document).", "",
             NORMALISATION, "",
             "| Sample | Model | Fields correct | Exact match | Validation | Time (s) |", "|---|---|---|---|---|---|"]
    details = []
    for truth_file in sorted(TRUTH.glob("*.json")):
        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        sample = SAMPLES / truth["_sample"]
        # Replay runs go to tmp/ so they never overwrite the saved real outputs.
        stem_out = (ROOT / "tmp" / "replay" if args.replay else OUT) / sample.stem
        try:
            if args.rescore:
                result = load_saved(sample)
            else:
                result = process(sample.read_bytes(), sample.name, stem_out,
                                 mode="replay" if args.replay else "live")
        except (ExtractionError, InvalidFileError, OSError, ValueError) as exc:  # e.g. missing key: no report
            print(f"error on {sample.name}: {exc}", file=sys.stderr)
            return 2
        ok, total, misses = score(truth, result["document"])
        strict_ok, _, _ = score(truth, result["document"], compare=exact)
        v = result["validation"]
        flags = ", ".join(f"{c['title']}" for c in v["checks"] if c["status"] in ("warning", "error")) or "all passed"
        lines.append(f"| `{sample.name}` | {result['model']} | {ok}/{total} ({ok / total:.0%}) | "
                     f"{strict_ok}/{total} | {flags} | {result['timing']['extract_seconds']} |")
        details.append(f"\n## {sample.name}\n\n" + ("\n".join(f"- {m}" for m in misses) if misses else "- no differences"))
        print(f"{sample.name:32s} {ok}/{total} fields (exact {strict_ok}/{total})  validation={v['status']}  ({flags})")
        for m in misses:
            print(f"    - {m}")
    report = (ROOT / "tmp" if args.replay else OUT) / "accuracy.md"
    report.write_text("\n".join(lines + details) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
