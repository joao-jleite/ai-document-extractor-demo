"""Offline tests: validators, business rules and exporters (no API key needed)."""

import json
from datetime import date
from pathlib import Path

import pytest

from app.exporters import write_pdf, write_xlsx
from app.extractor import InvalidFileError, prepare_input, sniff_media_type
from app.schema import ExtractedDocument
from app.validation import (
    is_valid_cnpj, is_valid_cpf, is_valid_rut, nfe_key_dv_ok, validate_document,
)

ROOT = Path(__file__).resolve().parent.parent
TRUTH = ROOT / "samples" / "truth"
TODAY = date(2026, 9, 25)


def load_truth(stem: str) -> ExtractedDocument:
    data = json.loads((TRUTH / f"{stem}.json").read_text(encoding="utf-8"))
    return ExtractedDocument.model_validate({k: v for k, v in data.items() if not k.startswith("_")})


@pytest.mark.parametrize("value,ok", [
    ("11.222.333/0001-81", True),
    ("11222333000181", True),
    ("11.222.333/0001-82", False),
    ("12.ABC.345/01DE-35", True),   # alphanumeric CNPJ (2026 format)
    ("12.ABC.345/01DE-36", False),
    ("00.000.000/0000-00", False),
])
def test_cnpj(value, ok):
    assert is_valid_cnpj(value) is ok


@pytest.mark.parametrize("value,ok", [
    ("11.111.111-1", True), ("11111111-1", True), ("11.111.111-2", False),
    ("77.123.456-9", True), ("96.555.444-0", True), ("12.345.678-5", True), ("12.345.678-K", False),
])
def test_rut(value, ok):
    assert is_valid_rut(value) is ok


def test_cpf():
    assert is_valid_cpf("529.982.247-25")
    assert not is_valid_cpf("529.982.247-24")
    assert not is_valid_cpf("111.111.111-11")


def test_nfe_key_from_sample():
    key = load_truth("danfe-exemplo-industrial").nfe_access_key
    assert nfe_key_dv_ok(key)
    assert not nfe_key_dv_ok(key[:-1] + str((int(key[-1]) + 1) % 10))


def test_clean_danfe_passes_everything():
    report = validate_document(load_truth("danfe-exemplo-industrial"), today=TODAY)
    assert report.status == "ok", [c for c in report.checks if c.status != "ok"]
    codes = {c.code for c in report.checks}
    assert {"required_fields", "supplier_tax_id", "buyer_tax_id", "line_math",
            "items_vs_subtotal", "grand_total", "nfe_key", "dates", "currency"} <= codes


def test_purchase_order_passes_with_cnpj_and_rut():
    report = validate_document(load_truth("orden-compra-andina"), today=TODAY)
    assert report.status == "ok"
    titles = {c.title for c in report.checks}
    assert "Supplier CNPJ" in titles and "Buyer RUT" in titles


def test_photo_sample_flags_the_deliberate_typo():
    report = validate_document(load_truth("foto-danfe-parafusos"), today=TODAY)
    assert report.status == "review"
    warnings = {c.code: c.detail for c in report.checks if c.status == "warning"}
    assert set(warnings) == {"line_math", "items_vs_subtotal"}
    assert "472.50" in warnings["line_math"] and "prints 427.50" in warnings["line_math"]
    assert "-45.00" in warnings["items_vs_subtotal"]


def test_missing_and_invalid_fields_are_errors():
    doc = load_truth("orden-compra-andina").model_copy(deep=True)
    doc.buyer.tax_id = "11.111.111-9"
    doc.grand_total = None
    doc.issue_date = "2026-02-31"
    report = validate_document(doc, today=TODAY)
    errors = {c.code for c in report.checks if c.status == "error"}
    assert {"buyer_tax_id", "required_fields", "dates"} <= errors


def test_clp_decimals_warning():
    doc = load_truth("orden-compra-andina").model_copy(deep=True)
    doc.currency = "CLP"
    report = validate_document(doc, today=TODAY)
    assert any(c.code == "currency_decimals" for c in report.checks)


def test_file_sniffing_and_rejection(tmp_path):
    assert sniff_media_type((ROOT / "samples" / "orden-compra-andina.pdf").read_bytes()) == "application/pdf"
    assert sniff_media_type((ROOT / "samples" / "foto-danfe-parafusos.jpg").read_bytes()) == "image/jpeg"
    with pytest.raises(InvalidFileError):
        prepare_input(b"hello world, not a pdf", "fake.pdf")
    with pytest.raises(InvalidFileError):
        prepare_input(b"", "empty.png")
    with pytest.raises(InvalidFileError):
        prepare_input(b"%PDF-1.4 truncated", "cut.pdf")
    prepared = prepare_input((ROOT / "samples" / "foto-danfe-parafusos.jpg").read_bytes(), "photo.jpg")
    assert prepared.kind == "image" and prepared.media_type == "image/jpeg"


def test_exporters_write_files(tmp_path):
    doc = load_truth("foto-danfe-parafusos")
    report = validate_document(doc, today=TODAY)
    meta = {"filename": "foto.jpg", "model": "test", "generated_at": "2026-09-25", "seconds": 1, "attempts": 1}
    xlsx = write_xlsx(tmp_path / "out.xlsx", doc, report, meta)
    pdf = write_pdf(tmp_path / "out.pdf", doc, report, meta)
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    assert wb.sheetnames == ["Header", "Items", "Validation"]
    assert "DEMO" in wb["Header"]["A1"].value
    assert pdf.read_bytes()[:5] == b"%PDF-"
