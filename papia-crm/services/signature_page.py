"""
Página de firmas automática (para enviar contratos desde la app móvil).

En el teléfono no es práctico arrastrar campos sobre el PDF, así que se agrega
una página final con un bloque por firmante (firma, nombre y fecha) y se
devuelven las posiciones normalizadas (0–1, origen arriba-izquierda) de esos
campos para guardarlos en el sobre.
"""
import io
from datetime import datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

MAX_SIGNERS = 5

TOP = 0.17          # donde empieza el primer bloque
BLOCK = 0.145       # alto de cada bloque de firmante
LEFT = 0.09
SIG_W, SIG_H = 0.50, 0.065
SIDE_X, SIDE_W = 0.63, 0.28
TEXT_H = 0.028

_INK = HexColor('#1F2937')
_MUTED = HexColor('#6B7280')
_LINE = HexColor('#CBD5E1')


def _layout(index):
    """Posiciones de los campos del firmante `index` (página relativa)."""
    y = TOP + index * BLOCK
    return [
        {'type': 'signature', 'x': LEFT, 'y': y + 0.030, 'w': SIG_W, 'h': SIG_H, 'required': True},
        {'type': 'full_name', 'x': SIDE_X, 'y': y + 0.030, 'w': SIDE_W, 'h': TEXT_H, 'required': True},
        {'type': 'date_signed', 'x': SIDE_X, 'y': y + 0.072, 'w': SIDE_W, 'h': TEXT_H, 'required': True},
    ]


def append_signature_page(pdf_bytes, signer_names, title=''):
    """Devuelve (pdf_con_pagina_de_firmas, [[campos firmante 1], [campos firmante 2], ...])."""
    if not signer_names:
        raise ValueError('Se necesita al menos un firmante')
    if len(signer_names) > MAX_SIGNERS:
        raise ValueError(f'Máximo {MAX_SIGNERS} firmantes')

    reader = PdfReader(io.BytesIO(pdf_bytes))
    last = reader.pages[-1]
    width, height = float(last.cropbox.width), float(last.cropbox.height)
    if int(last.get('/Rotate') or 0) % 180:
        width, height = height, width
    page_number = len(reader.pages) + 1

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=(width, height))

    def px(nx):
        return nx * width

    def py(ny):           # normalizado (arriba-izquierda) → puntos PDF (abajo-izquierda)
        return height * (1 - ny)

    pdf.setFillColor(_INK)
    pdf.setFont('Helvetica-Bold', 15)
    pdf.drawString(px(LEFT), py(0.08), 'Firmas')
    pdf.setFont('Helvetica', 9.5)
    pdf.setFillColor(_MUTED)
    subtitle = f'Documento: {title}' if title else 'Página de firmas'
    pdf.drawString(px(LEFT), py(0.105), subtitle[:110])
    pdf.drawString(px(LEFT), py(0.125), 'Las partes aceptan este documento con su firma electrónica.')

    for index, name in enumerate(signer_names):
        y = TOP + index * BLOCK
        sig, full_name, date = _layout(index)
        pdf.setFillColor(_INK)
        pdf.setFont('Helvetica-Bold', 10.5)
        pdf.drawString(px(LEFT), py(y + 0.018), f'{index + 1}. {name}'[:70])

        pdf.setStrokeColor(_LINE)
        pdf.setLineWidth(0.8)
        pdf.line(px(sig['x']), py(sig['y'] + sig['h']), px(sig['x'] + sig['w']), py(sig['y'] + sig['h']))
        pdf.line(px(full_name['x']), py(full_name['y'] + full_name['h']),
                 px(full_name['x'] + full_name['w']), py(full_name['y'] + full_name['h']))
        pdf.line(px(date['x']), py(date['y'] + date['h']),
                 px(date['x'] + date['w']), py(date['y'] + date['h']))

        pdf.setFillColor(_MUTED)
        pdf.setFont('Helvetica', 8)
        pdf.drawString(px(sig['x']), py(sig['y'] + sig['h'] + 0.014), 'Firma')
        pdf.drawString(px(full_name['x']), py(full_name['y'] + full_name['h'] + 0.012), 'Nombre')
        pdf.drawString(px(date['x']), py(date['y'] + date['h'] + 0.012), 'Fecha')

    pdf.setFont('Helvetica', 7.5)
    pdf.drawString(px(LEFT), py(0.96), f'Página de firmas generada por Papia Sign · {datetime.now():%Y-%m-%d}')
    pdf.showPage()
    pdf.save()

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_page(PdfReader(io.BytesIO(buf.getvalue())).pages[0])
    out = io.BytesIO()
    writer.write(out)

    layout = [[{**f, 'page': page_number} for f in _layout(i)] for i in range(len(signer_names))]
    return out.getvalue(), layout
