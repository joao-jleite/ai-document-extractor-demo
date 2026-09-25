"""Pydantic models shared by the extractor, the validator and the exporters.

`ExtractedDocument` doubles as the JSON schema sent to Claude (structured output):
the SDK converts it with `TypeAdapter(...).json_schema()`, so field descriptions
here are effectively part of the prompt. Keep them precise.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DocumentType = Literal["nfe_danfe", "purchase_order", "commercial_invoice", "other"]
TaxIdType = Literal["CNPJ", "CPF", "RUT", "OTHER"]


class Party(BaseModel):
    """A company or person on the document (seller or buyer)."""

    name: str | None = Field(None, description="Legal name exactly as printed.")
    tax_id: str | None = Field(
        None,
        description="Tax ID exactly as printed, keeping punctuation "
        "(e.g. '11.222.333/0001-81', '76.543.210-3'). Null if not printed.",
    )
    tax_id_type: TaxIdType | None = Field(
        None,
        description="CNPJ (Brazil company, 14 chars, may be alphanumeric), CPF (Brazil person, "
        "11 digits), RUT (Chile), OTHER for anything else.",
    )
    country: str | None = Field(None, description="ISO 3166-1 alpha-2 country code, e.g. BR, CL, US.")
    address: str | None = Field(None, description="Single-line postal address as printed.")

    @field_validator("country")
    @classmethod
    def _upper_country(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v


class LineItem(BaseModel):
    """One line of the goods/services table."""

    line_number: int | None = Field(None, description="Line/item number if printed.")
    code: str | None = Field(None, description="Product/part code as printed.")
    description: str = Field(..., description="Item description as printed.")
    quantity: float | None = Field(None, description="Quantity as a plain number.")
    unit: str | None = Field(None, description="Unit of measure as printed (UN, KG, PZA...).")
    unit_price: float | None = Field(None, description="Unit price as a plain number.")
    line_total: float | None = Field(
        None,
        description="Line total exactly as PRINTED on the document. Never recompute it, "
        "even if it looks wrong.",
    )


class ExtractedDocument(BaseModel):
    """Everything the model extracts from a single procurement document."""

    document_type: DocumentType = Field(
        ...,
        description="nfe_danfe = Brazilian NF-e/DANFE; purchase_order = PO / pedido de compra / "
        "orden de compra; commercial_invoice = invoice/factura; other = anything else.",
    )
    document_language: str | None = Field(None, description="ISO 639-1 code: pt, es, en...")
    document_number: str | None = Field(None, description="Document number (NF-e number, PO number...).")
    series: str | None = Field(None, description="NF-e series (serie) if printed.")
    issue_date: str | None = Field(None, description="Issue date as YYYY-MM-DD.")
    due_or_delivery_date: str | None = Field(
        None, description="Payment due date or requested delivery date as YYYY-MM-DD, if printed."
    )
    currency: str | None = Field(
        None, description="ISO 4217 code (BRL, CLP, USD...). A DANFE is always BRL."
    )
    supplier: Party = Field(
        ...,
        description="The seller: NF-e 'emitente'; PO 'proveedor/fornecedor/vendor'.",
    )
    buyer: Party = Field(
        ...,
        description="The purchaser: NF-e 'destinatario'; the company that issues the PO.",
    )
    items: list[LineItem] = Field(..., description="Every line of the items table, in order.")
    items_subtotal: float | None = Field(
        None,
        description="Goods subtotal as PRINTED (DANFE 'V. TOTAL PRODUTOS'; PO 'Subtotal'/'Neto').",
    )
    discount: float | None = Field(None, description="Total discount as printed, positive number.")
    freight: float | None = Field(None, description="Freight / flete / frete as printed.")
    insurance: float | None = Field(None, description="Insurance / seguro as printed.")
    other_charges: float | None = Field(None, description="Other charges / outras despesas as printed.")
    tax_added: float | None = Field(
        None,
        description="Taxes ADDED on top of the goods subtotal to reach the grand total "
        "(e.g. IPI on an NF-e, IVA on a Chilean PO). Do NOT include taxes already embedded "
        "in item prices, such as ICMS on an NF-e.",
    )
    grand_total: float | None = Field(
        None, description="Total amount to pay as PRINTED (DANFE 'V. TOTAL DA NOTA'; PO 'Total')."
    )
    payment_terms: str | None = Field(None, description="Payment terms / condicion de pago.")
    nfe_access_key: str | None = Field(
        None, description="NF-e 'chave de acesso': the 44 digits, no spaces. Null if not an NF-e."
    )
    notes: str | None = Field(None, description="Relevant free-text notes printed on the document.")
    extraction_notes: list[str] = Field(
        default_factory=list,
        description="Anything unreadable, ambiguous or suspicious you noticed while reading. "
        "Empty list if none.",
    )

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str | None) -> str | None:
        return v.strip().upper() if v else v

    @field_validator("nfe_access_key")
    @classmethod
    def _digits_only(cls, v: str | None) -> str | None:
        # The model is told to drop spaces, but be tolerant anyway.
        return "".join(ch for ch in v if ch.isdigit()) if v else v


CheckStatus = Literal["ok", "warning", "error", "info"]


class Check(BaseModel):
    """Result of one business-rule check."""

    code: str
    status: CheckStatus
    title: str
    detail: str
    field: str | None = None


class ValidationReport(BaseModel):
    status: Literal["ok", "review"]
    counts: dict[str, int]
    checks: list[Check]
    computed: dict[str, float | None]
