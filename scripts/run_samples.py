"""Run the real pipeline on every bundled sample and score it against the ground truth.

    python scripts/run_samples.py            # live: calls the Claude API
    python scripts/run_samples.py --replay   # re-score the saved outputs, no API call

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


def same(a, b, field=""):
    na, nb = norm(a), norm(b)
    if field in {"discount", "freight", "insurance", "other_charges", "tax_added"}:
        na, nb = na or None, nb or None  # 0 == not printed
    return na == nb


def score(truth: dict, got: dict) -> tuple[int, int, list[str]]:
    ok = total = 0
    misses = []

    def cmp(path, a, b, field):
        nonlocal ok, total
        total += 1
        if same(a, b, field):
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true", help="re-use saved outputs instead of calling the API")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)

    lines = ["# Accuracy on the bundled samples", "",
             "Field-by-field comparison between the extraction and the ground truth written by "
             "`scripts/make_samples.py` (what is printed on each fictitious document).", "",
             "| Sample | Model | Fields correct | Validation | Time (s) |", "|---|---|---|---|---|"]
    details = []
    for truth_file in sorted(TRUTH.glob("*.json")):
        truth = json.loads(truth_file.read_text(encoding="utf-8"))
        sample = SAMPLES / truth["_sample"]
        # Replay runs go to tmp/ so they never overwrite the saved real outputs.
        stem_out = (ROOT / "tmp" / "replay" if args.replay else OUT) / sample.stem
        stem_out.mkdir(parents=True, exist_ok=True)
        result = process(sample.read_bytes(), sample.name, stem_out, mode="replay" if args.replay else "live")
        ok, total, misses = score(truth, result["document"])
        v = result["validation"]
        flags = ", ".join(f"{c['title']}" for c in v["checks"] if c["status"] in ("warning", "error")) or "all passed"
        lines.append(f"| `{sample.name}` | {result['model']} | {ok}/{total} ({ok / total:.0%}) | {flags} | "
                     f"{result['timing']['extract_seconds']} |")
        details.append(f"\n## {sample.name}\n\n" + ("\n".join(f"- {m}" for m in misses) if misses else "- no differences"))
        print(f"{sample.name:32s} {ok}/{total} fields  validation={v['status']}  ({flags})")
        for m in misses:
            print(f"    - {m}")
    report = (ROOT / "tmp" if args.replay else OUT) / "accuracy.md"
    report.write_text("\n".join(lines + details) + "\n", encoding="utf-8")
    print(f"\nwrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
