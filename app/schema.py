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
        "(e.g. a CNPJ as 'NN.NNN.NNN/NNNN-NN', a RUT as 'NN.NNN.NNN-D'). Null if not printed.",
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
        None,
        # The first live run asked for "44 digits, no spaces" and on both DANFEs the key came
        # back 1-2 digits short (lost in long runs of repeated digits). Copying the printed
        # 4-digit groups fixed it; the validator below removes the spaces.
        description="NF-e 'chave de acesso' (44 digits) copied exactly as printed, group by group, "
        "KEEPING the spaces between the 4-digit groups. Null if not an NF-e.",
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


# ---------------------------------------------------------------------------
# Wire schema: the JSON schema Claude actually fills (structured output).
#
# The API compiles the schema into a grammar and rejects it with 400 "Schema is
# too complex" when it has too many optional fields and nullable unions (the
# first version, ExtractedDocument sent as-is, had 28 optional fields and 27
# `X | None` unions and was rejected). So the wire models make EVERY field
# required, use "" instead of null for text that is not printed, and keep null
# only for numbers, where 0 and "not printed" must stay different.
# to_document() turns the answer into the ExtractedDocument used everywhere else.
# ---------------------------------------------------------------------------

NOT_PRINTED = "NOT_PRINTED"
_BLANK = " Empty string if not printed."


def _desc(model: type[BaseModel], name: str, extra: str = "") -> str:
    """Reuse the description of the matching ExtractedDocument field (one source of truth)."""
    return (model.model_fields[name].description or "") + extra


def _none_if_blank(value):
    return None if isinstance(value, str) and not value.strip() else value


class WireParty(BaseModel):
    name: str = Field(description=_desc(Party, "name", _BLANK))
    tax_id: str = Field(description=_desc(Party, "tax_id").replace("Null", "Empty string"))
    tax_id_type: Literal["CNPJ", "CPF", "RUT", "OTHER", "NOT_PRINTED"] = Field(
        description=_desc(Party, "tax_id_type", " NOT_PRINTED if there is no tax ID.")
    )
    country: str = Field(description=_desc(Party, "country", _BLANK))
    address: str = Field(description=_desc(Party, "address", _BLANK))

    def to_party(self) -> Party:
        data = {k: _none_if_blank(v) for k, v in self.model_dump().items()}
        if data["tax_id_type"] == NOT_PRINTED:
            data["tax_id_type"] = None
        return Party.model_validate(data)


class WireLineItem(BaseModel):
    line_number: int | None = Field(description=_desc(LineItem, "line_number", " Null if not printed."))
    code: str = Field(description=_desc(LineItem, "code", _BLANK))
    description: str = Field(description=_desc(LineItem, "description"))
    quantity: float | None = Field(description=_desc(LineItem, "quantity", " Null if not legible."))
    unit: str = Field(description=_desc(LineItem, "unit", _BLANK))
    unit_price: float | None = Field(description=_desc(LineItem, "unit_price", " Null if not legible."))
    line_total: float | None = Field(description=_desc(LineItem, "line_total", " Null if not legible."))


class WireDocument(BaseModel):
    """What Claude returns. Converted with to_document()."""

    document_type: DocumentType = Field(description=_desc(ExtractedDocument, "document_type"))
    document_language: str = Field(description=_desc(ExtractedDocument, "document_language", _BLANK))
    document_number: str = Field(description=_desc(ExtractedDocument, "document_number", _BLANK))
    series: str = Field(description=_desc(ExtractedDocument, "series", _BLANK))
    issue_date: str = Field(description=_desc(ExtractedDocument, "issue_date", _BLANK))
    due_or_delivery_date: str = Field(description=_desc(ExtractedDocument, "due_or_delivery_date", _BLANK))
    currency: str = Field(description=_desc(ExtractedDocument, "currency", _BLANK))
    supplier: WireParty = Field(description=_desc(ExtractedDocument, "supplier"))
    buyer: WireParty = Field(description=_desc(ExtractedDocument, "buyer"))
    items: list[WireLineItem] = Field(description=_desc(ExtractedDocument, "items"))
    items_subtotal: float | None = Field(description=_desc(ExtractedDocument, "items_subtotal", " Null if not printed."))
    discount: float | None = Field(description=_desc(ExtractedDocument, "discount", " Null if not printed."))
    freight: float | None = Field(description=_desc(ExtractedDocument, "freight", " Null if not printed."))
    insurance: float | None = Field(description=_desc(ExtractedDocument, "insurance", " Null if not printed."))
    other_charges: float | None = Field(description=_desc(ExtractedDocument, "other_charges", " Null if not printed."))
    tax_added: float | None = Field(description=_desc(ExtractedDocument, "tax_added", " Null if none."))
    grand_total: float | None = Field(description=_desc(ExtractedDocument, "grand_total", " Null if not printed."))
    payment_terms: str = Field(description=_desc(ExtractedDocument, "payment_terms", _BLANK))
    nfe_access_key: str = Field(
        description=_desc(ExtractedDocument, "nfe_access_key").replace("Null", "Empty string")
    )
    notes: str = Field(description=_desc(ExtractedDocument, "notes", _BLANK))
    extraction_notes: list[str] = Field(description=_desc(ExtractedDocument, "extraction_notes"))

    def to_document(self) -> ExtractedDocument:
        """"" -> None for text fields; nested parties/items converted too."""
        data = {k: _none_if_blank(v) for k, v in self.model_dump(exclude={"supplier", "buyer", "items"}).items()}
        data["supplier"] = self.supplier.to_party()
        data["buyer"] = self.buyer.to_party()
        data["items"] = [
            LineItem.model_validate({k: (v if k == "description" else _none_if_blank(v))
                                     for k, v in item.model_dump().items()})
            for item in self.items
        ]
        data["extraction_notes"] = [n for n in self.extraction_notes if n.strip()]
        return ExtractedDocument.model_validate(data)

    @classmethod
    def from_document(cls, doc: ExtractedDocument) -> "WireDocument":
        """Inverse of to_document() (tests and fixtures): None -> "" for text fields."""
        def blank(d: dict, numeric: set[str]) -> dict:
            return {k: ("" if v is None and k not in numeric else v) for k, v in d.items()}

        numbers = {"items_subtotal", "discount", "freight", "insurance", "other_charges", "tax_added",
                   "grand_total", "line_number", "quantity", "unit_price", "line_total"}
        data = blank(doc.model_dump(mode="json", exclude={"supplier", "buyer", "items"}), numbers)
        for role in ("supplier", "buyer"):
            party = blank(getattr(doc, role).model_dump(mode="json"), set())
            party["tax_id_type"] = party["tax_id_type"] or NOT_PRINTED
            data[role] = party
        data["items"] = [blank(i.model_dump(mode="json"), numbers) for i in doc.items]
        return cls.model_validate(data)


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
