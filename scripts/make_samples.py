"""Generate the fictitious sample documents in samples/ plus their ground truth.

    python scripts/make_samples.py

Creates:
  samples/danfe-exemplo-industrial.pdf      NF-e DANFE, PT-BR, BRL (consistent)
  samples/orden-compra-andina.pdf           Purchase order, Spanish, Chilean buyer, USD
  samples/foto-danfe-parafusos.jpg          Phone-style photo of a printed DANFE
                                            (contains ONE deliberate typo: line 3 total)
  samples/truth/<name>.json                 What a perfect extraction should return

Every company, tax ID, key and number here is fictitious. The CNPJ 11.222.333/0001-81
is the classic documentation example, 11.444.777/0001-61 and the RUT 11.111.111-1 are
textbook examples too; the alphanumeric CNPJ 12.ABC.345/01DE-35 is the example format
from the 2026 CNPJ change. Any match with a real registration is coincidental.
"""

from __future__ import annotations

import io
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from reportlab.graphics.barcode.code128 import Code128
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
TRUTH = SAMPLES / "truth"
W, H = A4

# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def br(x: float, dec: int = 2) -> str:
    """1234.5 -> '1.234,50' (pt-BR / es-CL number format)."""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def nfe_key(cuf: str, aamm: str, cnpj: str, serie: int, numero: int, cnf: str) -> str:
    base = f"{cuf}{aamm}{cnpj}55{serie:03d}{numero:09d}1{cnf}"
    assert len(base) == 43
    total, weight = 0, 2
    for ch in reversed(base):
        total += int(ch) * weight
        weight = 2 if weight == 9 else weight + 1
    rest = total % 11
    return base + str(0 if rest < 2 else 11 - rest)


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

EXEMPLO = {
    "name": "EXEMPLO INDUSTRIAL LTDA",
    "cnpj": "11.222.333/0001-81",
    "ie": "123.456.789.110",
    "street": "RUA FICTÍCIA DAS INDÚSTRIAS, 100",
    "district": "DISTRITO INDUSTRIAL",
    "cep": "13000-000",
    "city": "CAMPINAS",
    "uf": "SP",
    "phone": "(19) 3000-0000",
}

DANFE_1 = {
    "stem": "danfe-exemplo-industrial",
    "emit": EXEMPLO,
    "dest": {
        "name": "FICTÍCIA MINERAÇÃO E MÁQUINAS S.A.",
        "cnpj": "12.ABC.345/01DE-35",  # alphanumeric CNPJ example (valid check digits)
        "ie": "987.654.321.000",
        "street": "AV. DEMONSTRAÇÃO, 2500",
        "district": "CENTRO",
        "cep": "30100-000",
        "city": "BELO HORIZONTE",
        "uf": "MG",
        "phone": "(31) 3000-0000",
    },
    "numero": 4217, "serie": 1, "cuf": "35", "cnf": "73920184",
    "issue": "2026-09-15", "due": "2026-10-15", "time": "14:32:10",
    "natureza": "VENDA DE PRODUÇÃO DO ESTABELECIMENTO",
    "protocolo": "135260000000000 15/09/2026 14:30:58",
    "items": [
        # code, description, ncm, qty, unit, unit price, ipi %
        ("EI-1001", "Rolamento rígido de esferas 6205-2RS", "8482.10.10", 40, "PC", 38.50, 5),
        ("EI-2040", "Correia em V perfil B-54", "4010.32.00", 25, "PC", 64.90, 0),
        ("EI-3310", "Filtro de ar industrial 610x610x292 mm", "8421.39.90", 12, "UN", 289.00, 5),
        ("EI-4102", "Graxa de lítio EP2 - balde 20 kg", "3403.99.00", 6, "BD", 412.75, 0),
        ("EI-5007", "Parafuso sextavado M16x60 zincado (cx 50)", "7318.15.00", 10, "CX", 97.30, 0),
    ],
    "freight": 350.00, "insurance": 0.0, "discount": 180.00, "other": 0.0,
    "carrier": "TRANSPORTES FICTÍCIOS LTDA", "volumes": 9, "gross_kg": 312.4, "net_kg": 298.0,
    "typo": None,
}

DANFE_PHOTO = {
    "stem": "foto-danfe-parafusos",
    "emit": {
        "name": "PARAFUSOS FICTÍCIOS DO BRASIL LTDA",
        "cnpj": "11.444.777/0001-61",  # textbook example CNPJ (valid check digits)
        "ie": "062.000.000.0099",
        "street": "RUA EXEMPLO DOS METAIS, 45",
        "district": "CIDADE INDUSTRIAL",
        "cep": "32000-000",
        "city": "CONTAGEM",
        "uf": "MG",
        "phone": "(31) 3999-0000",
    },
    "dest": EXEMPLO,
    "numero": 18734, "serie": 2, "cuf": "31", "cnf": "50318842",
    "issue": "2026-09-18", "due": "2026-10-18", "time": "09:05:44",
    "natureza": "VENDA DE MERCADORIA ADQUIRIDA DE TERCEIROS",
    "protocolo": "131260000000000 18/09/2026 09:04:12",
    "items": [
        ("PF-0110", "Parafuso Allen M8x25 inox A2 (cx 100)", "7318.15.00", 20, "CX", 42.60, 0),
        ("PF-0225", "Porca sextavada M12 zincada (cx 100)", "7318.16.00", 15, "CX", 23.40, 0),
        ("PF-0340", "Arruela lisa 1/2 pol. galvanizada (cx 200)", "7318.22.00", 25, "CX", 18.90, 0),
        ("PF-0418", "Barra roscada M10 x 1 m aço carbono", "7318.15.00", 40, "PC", 9.75, 0),
    ],
    "freight": 85.00, "insurance": 0.0, "discount": 0.0, "other": 0.0,
    "carrier": "TRANSPORTES FICTÍCIOS LTDA", "volumes": 4, "gross_kg": 96.2, "net_kg": 91.5,
    # Deliberate divergence: line 3 printed as 427,50 instead of 25 x 18,90 = 472,50
    # (digit transposition). The products total below stays correct (2.065,50).
    "typo": {2: 427.50},
}

PO = {
    "stem": "orden-compra-andina",
    "number": "OC-2026-0917",
    "issue": "2026-09-17", "delivery": "2026-11-02",
    "buyer": {
        "name": "COMERCIAL ANDINA EJEMPLO SpA",
        "rut": "11.111.111-1",  # textbook example RUT (valid check digit)
        "giro": "Distribución de repuestos industriales",
        "address": "Av. Ejemplo 1234, Of. 501, Las Condes, Santiago, Chile",
        "email": "compras@andina-ejemplo.example",
    },
    "supplier": {
        "name": "Exemplo Industrial Ltda",
        "cnpj": "11.222.333/0001-81",
        "address": "Rua Fictícia das Indústrias, 100 - Distrito Industrial, Campinas - SP, Brasil",
        "email": "export@exemplo-industrial.example",
    },
    "items": [
        ("EI-7801", "Rodamiento de rodillos esféricos 22218 EK", 8, "UN", 186.40),
        ("EI-7815", "Soporte de pie SNL 518-615", 8, "UN", 142.75),
        ("EI-3310", "Filtro de aire industrial 610x610x292 mm", 24, "UN", 61.90),
        ("EI-6620", "Acoplamiento elástico tipo grilla 1060T", 4, "UN", 318.00),
        ("EI-9050", "Kit de sellos para ventilador axial", 10, "KIT", 54.35),
    ],
    "freight": 420.00, "insurance": 38.60,
    "payment": "30% anticipo / 70% contra copia de B/L",
    "incoterm": "CIF San Antonio (Incoterms 2020)",
    "place": "Bodega central, Quilicura, Santiago",
}

# ---------------------------------------------------------------------------
# Drawing primitives (top-based coordinates: y grows downwards)
# ---------------------------------------------------------------------------

M = 20  # page margin


class Page:
    def __init__(self, c: canvas.Canvas):
        self.c = c

    def y(self, t: float) -> float:
        return H - t

    def box(self, x, t, w, h, label="", value="", size=8, bold=False, align="left", lsize=5.2):
        c = self.c
        c.setLineWidth(0.6)
        c.setStrokeColor(colors.black)
        c.rect(x, self.y(t + h), w, h, stroke=1, fill=0)
        if label:
            c.setFont("Helvetica", lsize)
            c.setFillColor(colors.black)
            c.drawString(x + 2, self.y(t + lsize + 1.5), label)
        if value != "":
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            vy = self.y(t + h - 3.2)
            if align == "right":
                c.drawRightString(x + w - 3, vy, str(value))
            elif align == "center":
                c.drawCentredString(x + w / 2, vy, str(value))
            else:
                c.drawString(x + 3, vy, str(value))

    def text(self, x, t, s, size=7, bold=False, align="left", color=colors.black):
        c = self.c
        c.setFillColor(color)
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        if align == "center":
            c.drawCentredString(x, self.y(t), s)
        elif align == "right":
            c.drawRightString(x, self.y(t), s)
        else:
            c.drawString(x, self.y(t), s)
        c.setFillColor(colors.black)

    def row(self, t, h, cells):
        """cells: list of (width, label, value, kwargs) laid left to right."""
        x = M
        for w, label, value, kw in cells:
            self.box(x, t, w, h, label, value, **kw)
            x += w

    def watermark(self, text: str):
        c = self.c
        c.saveState()
        c.setFillColor(colors.Color(0.8, 0.05, 0.05, alpha=0.13))
        c.setFont("Helvetica-Bold", 46)
        c.translate(W / 2, H / 2)
        c.rotate(40)
        c.drawCentredString(0, 0, text)
        c.restoreState()

    def footer(self, text: str):
        self.text(W / 2, H - 14, text, size=6.5, align="center", color=colors.HexColor("#B91C1C"))


# ---------------------------------------------------------------------------
# DANFE
# ---------------------------------------------------------------------------

def danfe_numbers(d: dict) -> dict:
    lines = []
    for idx, (code, desc, ncm, qty, un, price, ipi) in enumerate(d["items"]):
        real_total = round(qty * price, 2)
        printed = (d["typo"] or {}).get(idx, real_total)
        lines.append({
            "code": code, "desc": desc, "ncm": ncm, "qty": qty, "un": un, "price": price,
            "total": printed, "real_total": real_total, "icms_base": real_total,
            "icms": round(real_total * 0.12, 2), "ipi": round(real_total * ipi / 100, 2), "ipi_rate": ipi,
        })
    products = round(sum(li["real_total"] for li in lines), 2)
    ipi_total = round(sum(li["ipi"] for li in lines), 2)
    total = round(products + d["freight"] + d["insurance"] + d["other"] + ipi_total - d["discount"], 2)
    return {"lines": lines, "products": products, "ipi": ipi_total, "total": total,
            "icms_base": products, "icms": round(products * 0.12, 2)}


def draw_danfe(d: dict, path: Path) -> dict:
    n = danfe_numbers(d)
    key = nfe_key(d["cuf"], d["issue"][2:4] + d["issue"][5:7],
                  "".join(ch for ch in d["emit"]["cnpj"] if ch.isdigit()), d["serie"], d["numero"], d["cnf"])
    issue_br = "/".join(reversed(d["issue"].split("-")))
    due_br = "/".join(reversed(d["due"].split("-")))
    numero_fmt = f"{d['numero']:09d}"
    numero_fmt = f"{numero_fmt[:3]}.{numero_fmt[3:6]}.{numero_fmt[6:]}"
    e, dst = d["emit"], d["dest"]

    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"DANFE {numero_fmt} - DEMO")
    c.setAuthor("AI Document Extractor demo - fictitious data")
    p = Page(c)
    CW = W - 2 * M

    # Canhoto (receipt stub)
    p.box(M, 20, CW - 110, 18,
          f"RECEBEMOS DE {e['name']} OS PRODUTOS E/OU SERVIÇOS CONSTANTES DA NOTA FISCAL ELETRÔNICA INDICADA AO LADO")
    p.box(M, 38, 110, 18, "DATA DE RECEBIMENTO")
    p.box(M + 110, 38, CW - 220, 18, "IDENTIFICAÇÃO E ASSINATURA DO RECEBEDOR")
    p.box(M + CW - 110, 20, 110, 36)
    p.text(M + CW - 55, 32, "NF-e", size=11, bold=True, align="center")
    p.text(M + CW - 55, 43, f"Nº {numero_fmt}", size=8, bold=True, align="center")
    p.text(M + CW - 55, 52, f"SÉRIE {d['serie']:03d}", size=8, bold=True, align="center")
    c.setDash(3, 2)
    c.line(M, p.y(62), W - M, p.y(62))
    c.setDash()

    # Header: emitente | DANFE | key
    top, hh = 68, 112
    p.box(M, top, 230, hh, "IDENTIFICAÇÃO DO EMITENTE")
    p.text(M + 115, top + 26, e["name"], size=9.5, bold=True, align="center")
    for i, line in enumerate([e["street"], f"{e['district']} - CEP {e['cep']}",
                              f"{e['city']} - {e['uf']}", f"FONE: {e['phone']}"]):
        p.text(M + 115, top + 44 + i * 11, line, size=7.5, align="center")
    x2 = M + 230
    p.box(x2, top, 105, hh)
    p.text(x2 + 52.5, top + 20, "DANFE", size=15, bold=True, align="center")
    for i, line in enumerate(["DOCUMENTO AUXILIAR DA", "NOTA FISCAL ELETRÔNICA"]):
        p.text(x2 + 52.5, top + 31 + i * 8, line, size=6, align="center")
    p.text(x2 + 12, top + 55, "0 - ENTRADA", size=6.5)
    p.text(x2 + 12, top + 64, "1 - SAÍDA", size=6.5)
    p.box(x2 + 72, top + 49, 16, 16, "", "1", size=10, bold=True, align="center")
    p.text(x2 + 52.5, top + 82, f"Nº {numero_fmt}", size=9, bold=True, align="center")
    p.text(x2 + 52.5, top + 94, f"SÉRIE {d['serie']:03d}", size=8.5, bold=True, align="center")
    p.text(x2 + 52.5, top + 105, "FOLHA 1/1", size=7, align="center")
    x3 = x2 + 105
    w3 = CW - 335
    p.box(x3, top, w3, 44)
    bc = Code128(key, barHeight=30, barWidth=0.62, quiet=False)
    bc.drawOn(c, x3 + (w3 - bc.width) / 2, p.y(top + 38))
    p.box(x3, top + 44, w3, 24, "CHAVE DE ACESSO",
          " ".join(key[i:i + 4] for i in range(0, 44, 4)), size=7.4, bold=True, align="center")
    p.box(x3, top + 68, w3, hh - 68)
    for i, line in enumerate(["Consulta de autenticidade no portal nacional da NF-e",
                              "www.nfe.fazenda.gov.br/portal ou no site da Sefaz Autorizadora"]):
        p.text(x3 + w3 / 2, top + 84 + i * 9, line, size=6.3, align="center")

    t = top + hh
    p.row(t, 20, [(335, "NATUREZA DA OPERAÇÃO", d["natureza"], {}),
                  (CW - 335, "PROTOCOLO DE AUTORIZAÇÃO DE USO", d["protocolo"], {"size": 7.5})])
    t += 20
    p.row(t, 20, [(185, "INSCRIÇÃO ESTADUAL", e["ie"], {}), (185, "INSC. ESTADUAL DO SUBST. TRIBUT.", "", {}),
                  (CW - 370, "CNPJ", e["cnpj"], {"bold": True})])
    t += 26
    p.text(M, t + 3, "DESTINATÁRIO / REMETENTE", size=7, bold=True)
    t += 6
    p.row(t, 20, [(310, "NOME / RAZÃO SOCIAL", dst["name"], {"bold": True}),
                  (145, "CNPJ / CPF", dst["cnpj"], {"bold": True}), (CW - 455, "DATA DA EMISSÃO", issue_br, {})])
    t += 20
    p.row(t, 20, [(240, "ENDEREÇO", dst["street"], {}), (125, "BAIRRO / DISTRITO", dst["district"], {}),
                  (90, "CEP", dst["cep"], {}), (CW - 455, "DATA DA SAÍDA/ENTRADA", issue_br, {})])
    t += 20
    p.row(t, 20, [(180, "MUNICÍPIO", dst["city"], {}), (30, "UF", dst["uf"], {}),
                  (110, "FONE / FAX", dst["phone"], {}), (135, "INSCRIÇÃO ESTADUAL", dst["ie"], {}),
                  (CW - 455, "HORA DA SAÍDA/ENTRADA", d["time"], {})])
    t += 26
    p.text(M, t + 3, "FATURA / DUPLICATAS", size=7, bold=True)
    t += 6
    p.box(M, t, 150, 20, "", "")
    p.text(M + 4, t + 8, "NÚM.  001", size=6.5)
    p.text(M + 4, t + 16, f"VENC. {due_br}   VALOR R$ {br(n['total'])}", size=6.5)
    p.box(M + 150, t, CW - 150, 20, "CONDIÇÃO DE PAGAMENTO", "A PRAZO - 30 DIAS")
    t += 26
    p.text(M, t + 3, "CÁLCULO DO IMPOSTO", size=7, bold=True)
    t += 6
    wcol = CW / 6
    p.row(t, 20, [(wcol, "BASE DE CÁLC. DO ICMS", br(n["icms_base"]), {"align": "right"}),
                  (wcol, "VALOR DO ICMS", br(n["icms"]), {"align": "right"}),
                  (wcol, "BASE DE CÁLC. ICMS S.T.", "0,00", {"align": "right"}),
                  (wcol, "VALOR DO ICMS SUBST.", "0,00", {"align": "right"}),
                  (wcol, "V. IMP. IMPORTAÇÃO", "0,00", {"align": "right"}),
                  (wcol, "V. TOTAL PRODUTOS", br(n["products"]), {"align": "right", "bold": True})])
    t += 20
    p.row(t, 20, [(wcol, "VALOR DO FRETE", br(d["freight"]), {"align": "right"}),
                  (wcol, "VALOR DO SEGURO", br(d["insurance"]), {"align": "right"}),
                  (wcol, "DESCONTO", br(d["discount"]), {"align": "right"}),
                  (wcol, "OUTRAS DESPESAS", br(d["other"]), {"align": "right"}),
                  (wcol, "VALOR TOTAL IPI", br(n["ipi"]), {"align": "right"}),
                  (wcol, "V. TOTAL DA NOTA", br(n["total"]), {"align": "right", "bold": True, "size": 9})])
    t += 26
    p.text(M, t + 3, "TRANSPORTADOR / VOLUMES TRANSPORTADOS", size=7, bold=True)
    t += 6
    p.row(t, 20, [(200, "RAZÃO SOCIAL", d["carrier"], {}), (95, "FRETE POR CONTA", "0-EMITENTE", {}),
                  (70, "CÓDIGO ANTT", "", {}), (60, "PLACA DO VEÍCULO", "", {}), (25, "UF", "", {}),
                  (CW - 450, "CNPJ / CPF", "", {})])
    t += 20
    p.row(t, 20, [(90, "QUANTIDADE", str(d["volumes"]), {}), (90, "ESPÉCIE", "VOLUMES", {}),
                  (90, "MARCA", "", {}), (90, "NUMERAÇÃO", "", {}),
                  (97, "PESO BRUTO", br(d["gross_kg"], 3), {"align": "right"}),
                  (CW - 457, "PESO LÍQUIDO", br(d["net_kg"], 3), {"align": "right"})])
    t += 26
    p.text(M, t + 3, "DADOS DOS PRODUTOS / SERVIÇOS", size=7, bold=True)
    t += 6

    cols = [("CÓDIGO", 44), ("DESCRIÇÃO DO PRODUTO / SERVIÇO", 128), ("NCM/SH", 40), ("CST", 20),
            ("CFOP", 24), ("UN", 18), ("QUANT.", 32), ("VALOR UNIT.", 42), ("VALOR TOTAL", 46),
            ("B.CÁLC ICMS", 44), ("VALOR ICMS", 36), ("VALOR IPI", 33), ("ALÍQ. ICMS", 27), ("ALÍQ. IPI", 21)]
    x = M
    for name, w in cols:
        p.box(x, t, w, 14)
        p.text(x + w / 2, t + 9, name, size=4.9, bold=True, align="center")
        x += w
    t += 14
    table_top = t
    cfop = "5101" if d["emit"]["uf"] == d["dest"]["uf"] else "6101"
    for li in n["lines"]:
        desc_lines = wrap(li["desc"], "Helvetica", 6.6, 124)
        rh = 8 + 8 * len(desc_lines)
        vals = [li["code"], None, li["ncm"], "000", cfop, li["un"], br(li["qty"], 4), br(li["price"], 4),
                br(li["total"]), br(li["icms_base"]), br(li["icms"]), br(li["ipi"]), "12,00", br(li["ipi_rate"])]
        x = M
        for (name, w), v in zip(cols, vals):
            if v is None:
                for i, dl in enumerate(desc_lines):
                    p.text(x + 2, t + 9 + i * 8, dl, size=6.6)
            else:
                right = name not in ("CÓDIGO", "NCM/SH", "CST", "CFOP", "UN")
                p.text(x + w - 2 if right else x + 2, t + 9, v, size=6.6, align="right" if right else "left")
            x += w
        t += rh
    # Column rules down to the fixed table bottom
    table_bottom = 712
    x = M
    c.setLineWidth(0.6)
    for _, w in cols:
        c.rect(x, p.y(table_bottom), w, table_bottom - table_top, stroke=1, fill=0)
        x += w

    t = table_bottom + 8
    p.text(M, t + 3, "DADOS ADICIONAIS", size=7, bold=True)
    t += 6
    p.box(M, t, 360, 78, "INFORMAÇÕES COMPLEMENTARES")
    info = ("DOCUMENTO FICTÍCIO GERADO PARA DEMONSTRAÇÃO - SEM VALOR FISCAL. DEMO - dados fictícios. "
            f"Pedido do cliente: PC-{d['numero'] % 1000:03d}/2026. Tributos aproximados conforme Lei 12.741/2012: "
            f"R$ {br(n['icms'] + n['ipi'])}.")
    for i, line in enumerate(wrap(info, "Helvetica", 6.6, 350)):
        p.text(M + 4, t + 16 + i * 8.5, line, size=6.6)
    p.box(M + 360, t, CW - 360, 78, "RESERVADO AO FISCO")

    p.watermark("DEMO - dados fictícios")
    p.footer("DEMO - dados fictícios  |  documento sem valor fiscal  |  gerado para demonstração de software")
    c.showPage()
    c.save()

    truth = {
        "document_type": "nfe_danfe", "document_language": "pt",
        # Exactly as printed on the DANFE ("Nº 000.004.217", "SÉRIE 001"); the scorer normalises.
        "document_number": numero_fmt, "series": f"{d['serie']:03d}",
        "issue_date": d["issue"], "due_or_delivery_date": d["due"], "currency": "BRL",
        "supplier": {"name": e["name"], "tax_id": e["cnpj"], "tax_id_type": "CNPJ", "country": "BR"},
        "buyer": {"name": dst["name"], "tax_id": dst["cnpj"], "tax_id_type": "CNPJ", "country": "BR"},
        "items": [{"line_number": i + 1, "code": li["code"], "description": li["desc"], "quantity": li["qty"],
                   "unit": li["un"], "unit_price": li["price"], "line_total": li["total"]}
                  for i, li in enumerate(n["lines"])],
        "items_subtotal": n["products"], "discount": d["discount"], "freight": d["freight"],
        "insurance": d["insurance"], "other_charges": d["other"], "tax_added": n["ipi"],
        "grand_total": n["total"], "nfe_access_key": key,
    }
    return truth


# ---------------------------------------------------------------------------
# Purchase order (Spanish, Chile)
# ---------------------------------------------------------------------------

def draw_po(d: dict, path: Path) -> dict:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle(f"Orden de Compra {d['number']} - DEMO")
    c.setAuthor("AI Document Extractor demo - fictitious data")
    p = Page(c)
    CW = W - 2 * M - 20
    L = M + 10
    navy = colors.HexColor("#1F3A5F")
    b, s = d["buyer"], d["supplier"]
    fmt_date = lambda iso: "/".join(reversed(iso.split("-")))  # noqa: E731

    # Buyer letterhead
    c.setFillColor(navy)
    c.roundRect(L, p.y(78), 40, 40, 6, stroke=0, fill=1)
    p.text(L + 20, 64, "CA", size=16, bold=True, align="center", color=colors.white)
    p.text(L + 50, 50, b["name"], size=13, bold=True, color=navy)
    p.text(L + 50, 62, f"RUT {b['rut']}  -  Giro: {b['giro']}", size=8)
    p.text(L + 50, 72, b["address"], size=8)
    p.text(L + 50, 82, b["email"], size=8)

    # PO title box
    bx = L + CW - 170
    c.setStrokeColor(navy)
    c.setLineWidth(1.2)
    c.rect(bx, p.y(112), 170, 76, stroke=1, fill=0)
    c.setFillColor(navy)
    c.rect(bx, p.y(56), 170, 20, stroke=0, fill=1)
    p.text(bx + 85, 50, "ORDEN DE COMPRA", size=11, bold=True, align="center", color=colors.white)
    p.text(bx + 85, 72, f"N° {d['number']}", size=12, bold=True, align="center")
    p.text(bx + 85, 88, f"Fecha de emisión: {fmt_date(d['issue'])}", size=8.5, align="center")
    p.text(bx + 85, 102, "Moneda: USD (dólar estadounidense)", size=8.5, align="center")

    def section(t, title):
        c.setFillColor(colors.HexColor("#E8EEF5"))
        c.rect(L, p.y(t + 14), CW, 14, stroke=0, fill=1)
        p.text(L + 6, t + 10, title, size=8, bold=True, color=navy)

    t = 128
    section(t, "PROVEEDOR")
    rows = [("Razón social:", s["name"]), ("CNPJ (Brasil):", s["cnpj"]), ("Dirección:", s["address"]),
            ("Contacto:", s["email"])]
    for i, (k, v) in enumerate(rows):
        p.text(L + 6, t + 28 + i * 12, k, size=8.5, bold=True)
        p.text(L + 90, t + 28 + i * 12, v, size=8.5)

    t = 206
    section(t, "CONDICIONES COMERCIALES")
    conds = [("Condición de pago:", d["payment"]), ("Incoterm:", d["incoterm"]),
             ("Fecha de entrega requerida:", fmt_date(d["delivery"])), ("Lugar de entrega:", d["place"])]
    for i, (k, v) in enumerate(conds):
        p.text(L + 6, t + 28 + i * 12, k, size=8.5, bold=True)
        p.text(L + 140, t + 28 + i * 12, v, size=8.5)

    # Items table
    t = 284
    heads = [("Ítem", 30, "c"), ("Código", 58, "l"), ("Descripción", 200, "l"), ("Cant.", 40, "r"),
             ("Unidad", 44, "c"), ("Precio unit. US$", 72, "r"), ("Total US$", CW - 444, "r")]
    c.setFillColor(navy)
    c.rect(L, p.y(t + 18), CW, 18, stroke=0, fill=1)
    x = L
    for name, w, al in heads:
        pos = {"c": x + w / 2, "l": x + 5, "r": x + w - 5}[al]
        p.text(pos, t + 12, name, size=8, bold=True, color=colors.white,
               align={"c": "center", "l": "left", "r": "right"}[al])
        x += w
    t += 18
    subtotal = 0.0
    lines = []
    for i, (code, desc, qty, un, price) in enumerate(d["items"], start=1):
        total = round(qty * price, 2)
        subtotal += total
        lines.append({"line_number": i, "code": code, "description": desc, "quantity": qty, "unit": un,
                      "unit_price": price, "line_total": total})
        if i % 2 == 0:
            c.setFillColor(colors.HexColor("#F4F7FA"))
            c.rect(L, p.y(t + 20), CW, 20, stroke=0, fill=1)
        vals = [str(i), code, desc, br(qty, 0), un, br(price), br(total)]
        x = L
        for (name, w, al), v in zip(heads, vals):
            pos = {"c": x + w / 2, "l": x + 5, "r": x + w - 5}[al]
            p.text(pos, t + 13.5, v, size=8.5, align={"c": "center", "l": "left", "r": "right"}[al])
            x += w
        t += 20
    c.setStrokeColor(navy)
    c.setLineWidth(0.8)
    c.line(L, p.y(t), L + CW, p.y(t))
    subtotal = round(subtotal, 2)
    total = round(subtotal + d["freight"] + d["insurance"], 2)

    # Totals
    t += 10
    tx = L + CW - 230
    for label, val, bold in [("Subtotal (neto)", subtotal, False), ("Flete internacional", d["freight"], False),
                             ("Seguro de transporte", d["insurance"], False)]:
        p.text(tx, t + 12, label, size=9)
        p.text(L + CW - 5, t + 12, f"US$ {br(val)}", size=9, align="right")
        t += 16
    c.setFillColor(navy)
    c.rect(tx - 6, p.y(t + 22), 236, 22, stroke=0, fill=1)
    p.text(tx, t + 15, "TOTAL CIF", size=10.5, bold=True, color=colors.white)
    p.text(L + CW - 5, t + 15, f"US$ {br(total)}", size=10.5, bold=True, align="right", color=colors.white)
    t += 40

    section(t, "OBSERVACIONES")
    obs = ("Adjuntar factura comercial, packing list y certificado de origen. Indicar el número de esta OC en "
           "todos los documentos. Documento de demostración: empresas y datos ficticios.")
    for i, line in enumerate(wrap(obs, "Helvetica", 8.5, CW - 12)):
        p.text(L + 6, t + 28 + i * 11, line, size=8.5)
    t += 80

    # Signatures
    for i, (who, role) in enumerate([("Solicitado por", "Jefe de Mantención"),
                                     ("Aprobado por", "Gerencia de Abastecimiento")]):
        sx = L + 20 + i * 270
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.6)
        c.line(sx, p.y(t + 40), sx + 200, p.y(t + 40))
        p.text(sx + 100, t + 52, who, size=8, bold=True, align="center")
        p.text(sx + 100, t + 63, role, size=8, align="center")

    p.watermark("DEMO - datos ficticios")
    p.footer("DEMO - datos ficticios / fictitious data  |  documento de demostración sin validez comercial")
    c.showPage()
    c.save()

    return {
        "document_type": "purchase_order", "document_language": "es",
        "document_number": d["number"], "series": None,
        "issue_date": d["issue"], "due_or_delivery_date": d["delivery"], "currency": "USD",
        "supplier": {"name": s["name"], "tax_id": s["cnpj"], "tax_id_type": "CNPJ", "country": "BR"},
        "buyer": {"name": b["name"], "tax_id": b["rut"], "tax_id_type": "RUT", "country": "CL"},
        "items": lines, "items_subtotal": subtotal, "discount": None, "freight": d["freight"],
        "insurance": d["insurance"], "other_charges": None, "tax_added": None, "grand_total": total,
        "nfe_access_key": None,
    }


# ---------------------------------------------------------------------------
# "Photo" of a printed page
# ---------------------------------------------------------------------------

def _perspective_coeffs(dst, src):
    """Coefficients for PIL's Image.transform(PERSPECTIVE) mapping dst quad -> src quad."""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(a, dtype=float)
    B = np.array([c for pt in src for c in pt], dtype=float)
    return np.linalg.solve(A, B).tolist()


def photograph(pdf_path: Path, out_path: Path, seed: int = 7) -> None:
    import pypdfium2 as pdfium

    rng = np.random.default_rng(seed)
    random.seed(seed)
    pdf = pdfium.PdfDocument(str(pdf_path))
    page = pdf[0].render(scale=200 / 72).to_pil().convert("RGB")  # 200 DPI
    pdf.close()
    pw, ph = page.size

    # Paper: warm off-white tint + faint fibre noise
    paper = np.asarray(page).astype(np.float32)
    paper = paper * np.array([0.975, 0.965, 0.93]) + rng.normal(0, 3.0, paper.shape)
    page = Image.fromarray(np.clip(paper, 0, 255).astype(np.uint8))

    # Canvas = desk surface (dark grey-brown gradient with texture)
    cw, ch = int(pw * 1.16), int(ph * 1.10)
    yy, xx = np.mgrid[0:ch, 0:cw]
    base = 70 + 30 * (xx / cw) + 18 * (yy / ch)
    desk = np.stack([base * 1.05, base * 0.92, base * 0.80], axis=-1)
    desk += rng.normal(0, 6, desk.shape)
    desk = Image.fromarray(np.clip(desk, 0, 255).astype(np.uint8))

    # Where the page corners land (camera slightly tilted and rotated)
    ox, oy = (cw - pw) / 2, (ch - ph) / 2
    dst = [(ox + 70, oy + 40), (ox + pw - 10, oy + 95), (ox + pw + 25, oy + ph - 20), (ox - 5, oy + ph - 70)]
    src = [(0, 0), (pw, 0), (pw, ph), (0, ph)]

    # Soft drop shadow under the page
    shadow = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(shadow).polygon([(x + 18, y + 22) for x, y in dst], fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    desk.paste(Image.new("RGB", (cw, ch), (15, 12, 10)), (0, 0), shadow)

    warped = page.transform((cw, ch), Image.PERSPECTIVE, _perspective_coeffs(dst, src), Image.BICUBIC)
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).polygon(dst, fill=255)
    desk.paste(warped, (0, 0), mask.filter(ImageFilter.GaussianBlur(1.2)))

    # Uneven lighting: vignette + a soft diagonal shadow band (hand/phone shadow)
    img = np.asarray(desk).astype(np.float32)
    cx, cy = cw * 0.45, ch * 0.4
    dist = np.sqrt(((xx - cx) / cw) ** 2 + ((yy - cy) / ch) ** 2)
    light = 1.08 - 0.55 * dist ** 2
    band = np.exp(-(((xx * 0.8 + yy * 0.6) - (cw * 0.95)) / (cw * 0.10)) ** 2)
    light *= 1 - 0.20 * band
    img = img * light[..., None]
    img += rng.normal(0, 5.5, img.shape)  # sensor noise
    out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    out = out.rotate(-1.2, resample=Image.BICUBIC, expand=False, fillcolor=(60, 52, 45))
    out = out.filter(ImageFilter.GaussianBlur(0.7))  # slight focus softness
    out.thumbnail((1700, 2300), Image.LANCZOS)
    # Save as JPEG with no EXIF/metadata at all
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=84, optimize=True)
    out_path.write_bytes(buf.getvalue())


# ---------------------------------------------------------------------------

def main() -> int:
    SAMPLES.mkdir(exist_ok=True)
    TRUTH.mkdir(exist_ok=True)
    tmp = ROOT / "tmp"
    tmp.mkdir(exist_ok=True)

    truths = {
        "danfe-exemplo-industrial.pdf": draw_danfe(DANFE_1, SAMPLES / "danfe-exemplo-industrial.pdf"),
        "orden-compra-andina.pdf": draw_po(PO, SAMPLES / "orden-compra-andina.pdf"),
    }
    printed = tmp / "foto-danfe-parafusos-flat.pdf"
    truths["foto-danfe-parafusos.jpg"] = draw_danfe(DANFE_PHOTO, printed)
    photograph(printed, SAMPLES / "foto-danfe-parafusos.jpg")

    for name, truth in truths.items():
        truth = {"_sample": name, "_note": "Ground truth: what is PRINTED on the fictitious sample.", **truth}
        (TRUTH / f"{Path(name).stem}.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    for f in sorted(SAMPLES.glob("*.*")):
        print(f"{f.name:34s} {f.stat().st_size / 1024:8.1f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
