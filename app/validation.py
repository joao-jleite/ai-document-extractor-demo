"""Business-rule validation for extracted documents.

Pydantic already guarantees *types* (the model output is parsed into
`ExtractedDocument`). This module checks *meaning*: arithmetic, tax-ID check
digits, dates, required fields and currency. Every rule returns a `Check`
instead of raising, so the UI can show a full checklist.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .schema import Check, ExtractedDocument, Party, ValidationReport

# ISO 4217 codes this demo accepts, with their number of minor-unit decimals.
CURRENCY_DECIMALS: dict[str, int] = {
    "BRL": 2, "USD": 2, "EUR": 2, "CLP": 0, "ARS": 2, "PEN": 2, "COP": 2, "MXN": 2,
    "UYU": 2, "PYG": 0, "BOB": 2, "GBP": 2, "CNY": 2, "JPY": 0, "CAD": 2, "CHF": 2,
    "AUD": 2,
}


# ---------------------------------------------------------------------------
# Tax IDs
# ---------------------------------------------------------------------------

def _cnpj_digit(values: list[int], weights: list[int]) -> int:
    rest = sum(v * w for v, w in zip(values, weights)) % 11
    return 0 if rest < 2 else 11 - rest


def is_valid_cnpj(raw: str) -> bool:
    """CNPJ check digits (mod 11). Supports the alphanumeric CNPJ format
    (valid since July 2026): each char counts as ord(char) - 48, so digits keep
    their value and 'A' = 17, 'B' = 18, ... The two check digits stay numeric."""
    s = re.sub(r"[.\-/\s]", "", raw or "").upper()
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", s) or len(set(s)) == 1:
        return False
    vals = [ord(c) - 48 for c in s[:12]]
    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d1 = _cnpj_digit(vals, w1)
    d2 = _cnpj_digit(vals + [d1], [6] + w1)
    return s[12:] == f"{d1}{d2}"


def is_valid_cpf(raw: str) -> bool:
    s = re.sub(r"\D", "", raw or "")
    if len(s) != 11 or len(set(s)) == 1:
        return False
    nums = [int(c) for c in s]
    for pos in (9, 10):
        total = sum(n * w for n, w in zip(nums[:pos], range(pos + 1, 1, -1)))
        digit = (total * 10) % 11 % 10
        if digit != nums[pos]:
            return False
    return True


def is_valid_rut(raw: str) -> bool:
    """Chilean RUT: body + check digit (0-9 or K), mod 11 with weights 2..7."""
    s = re.sub(r"[.\s]", "", raw or "").upper()
    m = re.fullmatch(r"(\d{1,8})-?([\dK])", s)
    if not m:
        return False
    body, dv = m.groups()
    total, weight = 0, 2
    for ch in reversed(body):
        total += int(ch) * weight
        weight = 2 if weight == 7 else weight + 1
    rest = 11 - total % 11
    expected = "0" if rest == 11 else "K" if rest == 10 else str(rest)
    return dv == expected


def guess_tax_id_type(party: Party) -> str:
    if party.tax_id_type and party.tax_id_type != "OTHER":
        return party.tax_id_type
    compact = re.sub(r"[.\-/\s]", "", party.tax_id or "")
    if re.fullmatch(r"[0-9A-Za-z]{12}\d{2}", compact):
        return "CNPJ"
    if re.fullmatch(r"\d{11}", compact):
        return "CPF"
    if re.fullmatch(r"\d{7,8}[\dkK]", compact) and "-" in (party.tax_id or ""):
        return "RUT"
    return party.tax_id_type or "OTHER"


# ---------------------------------------------------------------------------
# NF-e access key (chave de acesso, 44 characters)
# ---------------------------------------------------------------------------

# cUF(2) AAMM(4) issuer CNPJ(14) model(2) series(3) number(9) emission type(1) code(8) DV(1).
# With the alphanumeric CNPJ (NT 2025.001), the 12 first CNPJ characters may be
# letters; everything else, the CNPJ check digits and the key's DV stay numeric.
NFE_KEY = re.compile(r"\d{6}[0-9A-Z]{12}\d{26}")


def nfe_key_dv_ok(key: str) -> bool:
    """Mod-11 check digit (weights 2..9). Letters count as ord(char) - 48, as in the
    alphanumeric CNPJ, so an all-digit key gets exactly the classic result."""
    if not NFE_KEY.fullmatch(key or ""):
        return False
    total, weight = 0, 2
    for ch in reversed(key[:43]):
        total += (ord(ch) - 48) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    dv = 0 if rest < 2 else 11 - rest
    return dv == int(key[43])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _d(x: float | None) -> Decimal | None:
    return None if x is None else Decimal(str(x))


def _q(x: Decimal, decimals: int) -> Decimal:
    return x.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def fmt_money(x: Decimal | float | None, currency: str | None) -> str:
    """Human format used in check messages: 1,234.56 BRL / 1,234,567 CLP."""
    if x is None:
        return "n/a"
    dec = CURRENCY_DECIMALS.get(currency or "", 2)
    return f"{float(x):,.{dec}f} {currency or ''}".strip()


def _sentence(parts: list[str]) -> str:
    """Join messages into one sentence; capitalise only the first letter
    (str.capitalize() would lowercase currency codes like BRL)."""
    text = "; ".join(parts)
    return text[:1].upper() + text[1:] + "."


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)  # raises ValueError on bad input


def line_mismatches(doc: ExtractedDocument) -> dict[int, Decimal]:
    """Index -> expected total for lines where quantity x unit price != printed total."""
    decimals = CURRENCY_DECIMALS.get(doc.currency or "", 2)
    tol = Decimal(1).scaleb(-decimals)
    out: dict[int, Decimal] = {}
    for idx, item in enumerate(doc.items):
        q, p, t = _d(item.quantity), _d(item.unit_price), _d(item.line_total)
        if q is None or p is None or t is None:
            continue
        expected = _q(q * p, decimals)
        if abs(expected - t) > tol:
            out[idx] = expected
    return out


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_document(doc: ExtractedDocument, today: date | None = None) -> ValidationReport:
    today = today or date.today()
    checks: list[Check] = []
    cur = doc.currency
    decimals = CURRENCY_DECIMALS.get(cur or "", 2)
    tol = Decimal(1).scaleb(-decimals)  # one minor unit: 0.01 BRL, 1 CLP

    def add(code, status, title, detail, field=None):
        checks.append(Check(code=code, status=status, title=title, detail=detail, field=field))

    # 1) Required fields ----------------------------------------------------
    required = {
        "document_number": doc.document_number,
        "issue_date": doc.issue_date,
        "currency": doc.currency,
        "supplier.name": doc.supplier.name,
        "supplier.tax_id": doc.supplier.tax_id,
        "buyer.name": doc.buyer.name,
        "grand_total": doc.grand_total,
    }
    missing = [k for k, v in required.items() if v in (None, "")]
    if not doc.items:
        missing.append("items")
    if missing:
        add("required_fields", "error", "Required fields",
            "Missing: " + ", ".join(missing), ",".join(missing))
    else:
        add("required_fields", "ok", "Required fields",
            f"All {len(required) + 1} required fields are present.")

    # 2) Currency -----------------------------------------------------------
    if cur and cur not in CURRENCY_DECIMALS:
        add("currency", "error", "Currency", f"'{cur}' is not a supported ISO 4217 code.", "currency")
    elif cur:
        expected = None
        if doc.document_type == "nfe_danfe":
            expected = "BRL"
        if expected and cur != expected:
            add("currency", "warning", "Currency",
                f"A DANFE must be in {expected}, but '{cur}' was found.", "currency")
        else:
            add("currency", "ok", "Currency", f"{cur} is a valid ISO 4217 currency.")
        # CLP (and other 0-decimal currencies) must not carry cents.
        if decimals == 0:
            amounts = [doc.items_subtotal, doc.grand_total, doc.tax_added] + [
                i.line_total for i in doc.items
            ]
            if any(a is not None and Decimal(str(a)) % 1 != 0 for a in amounts):
                add("currency_decimals", "warning", "Currency decimals",
                    f"{cur} has no minor unit, but some amounts have decimals.", "currency")

    # 3) Tax IDs ------------------------------------------------------------
    for role, party in (("supplier", doc.supplier), ("buyer", doc.buyer)):
        if not party.tax_id:
            continue
        kind = guess_tax_id_type(party)
        validator = {"CNPJ": is_valid_cnpj, "CPF": is_valid_cpf, "RUT": is_valid_rut}.get(kind)
        label = f"{role.capitalize()} {kind}"
        if validator is None:
            add(f"{role}_tax_id", "info", f"{role.capitalize()} tax ID",
                f"{party.tax_id}: no check-digit rule for this ID type.", f"{role}.tax_id")
        elif validator(party.tax_id):
            add(f"{role}_tax_id", "ok", label, f"{party.tax_id}: check digit is valid.")
        else:
            add(f"{role}_tax_id", "error", label,
                f"{party.tax_id}: check digit is INVALID (typo or OCR error?).", f"{role}.tax_id")

    # 4) Dates --------------------------------------------------------------
    issue = due = None
    try:
        issue = _parse_date(doc.issue_date)
        due = _parse_date(doc.due_or_delivery_date)
    except ValueError as exc:
        add("dates", "error", "Dates", f"Unparseable date: {exc}.", "issue_date")
    else:
        problems = []
        if issue and issue > today:
            problems.append(f"issue date {issue.isoformat()} is in the future")
        if issue and due and due < issue:
            problems.append(f"due/delivery date {due.isoformat()} is before the issue date")
        if problems:
            add("dates", "warning", "Dates", _sentence(problems), "issue_date")
        elif issue:
            extra = f", due/delivery {due.isoformat()}" if due else ""
            add("dates", "ok", "Dates", f"Issue date {issue.isoformat()}{extra}: consistent.")

    # 5) Line arithmetic: quantity x unit price = line total ------------------
    bad_lines = []
    for idx, expected in line_mismatches(doc).items():
        item = doc.items[idx]
        n = item.line_number or idx + 1
        bad_lines.append(
            f"line {n}: {item.quantity:g} × {item.unit_price:,.{max(decimals, 2)}f} = "
            f"{fmt_money(expected, cur)}, but the document prints {fmt_money(item.line_total, cur)}"
        )
    if bad_lines:
        add("line_math", "warning", "Line totals", _sentence(bad_lines), "items")
    elif doc.items:
        add("line_math", "ok", "Line totals",
            f"quantity x unit price matches on all {len(doc.items)} lines.")

    # 6) Sum of lines vs printed subtotal ------------------------------------
    line_totals = [_d(i.line_total) for i in doc.items]
    items_sum = sum(line_totals, Decimal(0)) if line_totals and None not in line_totals else None
    subtotal = _d(doc.items_subtotal)
    if items_sum is not None and subtotal is not None:
        diff = items_sum - subtotal
        if abs(diff) > tol:
            add("items_vs_subtotal", "warning", "Items vs subtotal",
                f"Sum of line totals is {fmt_money(items_sum, cur)} but the printed subtotal is "
                f"{fmt_money(subtotal, cur)} (difference {fmt_money(diff, cur)}).", "items_subtotal")
        else:
            add("items_vs_subtotal", "ok", "Items vs subtotal",
                f"Sum of {len(line_totals)} lines = printed subtotal ({fmt_money(subtotal, cur)}).")

    # 7) Grand total = subtotal - discount + freight + insurance + other + taxes
    base = subtotal if subtotal is not None else items_sum
    grand = _d(doc.grand_total)
    expected_total = None
    if base is not None and grand is not None:
        extras = {
            "discount": -(_d(doc.discount) or 0),
            "freight": _d(doc.freight) or 0,
            "insurance": _d(doc.insurance) or 0,
            "other": _d(doc.other_charges) or 0,
            "taxes": _d(doc.tax_added) or 0,
        }
        expected_total = base + sum(extras.values(), Decimal(0))
        parts = " ".join(
            f"{'-' if v < 0 else '+'} {k} {fmt_money(abs(v), cur)}" for k, v in extras.items() if v
        )
        formula = f"subtotal {fmt_money(base, cur)} {parts}".strip()
        if abs(expected_total - grand) > tol:
            add("grand_total", "warning", "Grand total",
                f"{formula} = {fmt_money(expected_total, cur)}, but the printed total is "
                f"{fmt_money(grand, cur)}.", "grand_total")
        else:
            add("grand_total", "ok", "Grand total", f"{formula} = printed total {fmt_money(grand, cur)}.")

    # 8) NF-e access key -----------------------------------------------------
    if doc.document_type == "nfe_danfe":
        key = doc.nfe_access_key or ""
        if not key:
            add("nfe_key", "warning", "NF-e access key", "No 44-character access key found.", "nfe_access_key")
        elif not nfe_key_dv_ok(key):
            add("nfe_key", "error", "NF-e access key",
                "Key is not 44 characters or its check digit is invalid.", "nfe_access_key")
        else:
            issues = []
            supplier_id = re.sub(r"[.\-/\s]", "", doc.supplier.tax_id or "").upper()
            if supplier_id and key[6:20] != supplier_id:
                issues.append("issuer CNPJ inside the key differs from the supplier CNPJ")
            if issue and key[2:6] != issue.strftime("%y%m"):
                issues.append("year/month inside the key differs from the issue date")
            number = re.sub(r"\D", "", doc.document_number or "")
            if number and int(key[25:34]) != int(number):
                issues.append("NF-e number inside the key differs from the document number")
            if issues:
                add("nfe_key", "warning", "NF-e access key", _sentence(issues),
                    "nfe_access_key")
            else:
                add("nfe_key", "ok", "NF-e access key",
                    "Check digit valid; CNPJ, year/month and number match the document.")

    # 9) Model's own notes (surfaced, never hidden) -------------------------
    for note in doc.extraction_notes:
        add("model_note", "info", "Reader note", note)

    counts = {s: sum(1 for c in checks if c.status == s) for s in ("ok", "warning", "error", "info")}
    status = "review" if counts["warning"] or counts["error"] else "ok"
    computed = {
        "items_sum": float(items_sum) if items_sum is not None else None,
        "expected_total": float(expected_total) if expected_total is not None else None,
    }
    return ValidationReport(status=status, counts=counts, checks=checks, computed=computed)
