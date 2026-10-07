"""
Firma electrónica (Contratos) — generación del PDF final.

  1. Estampa en el PDF original las firmas, iniciales y valores de cada campo.
  2. Añade el Certificado de finalización (audit trail) como páginas finales.
  3. Sella el PDF con una firma digital PAdES (pyHanko). Cualquier cambio posterior
     al archivo invalida el sello en Adobe Acrobat / cualquier validador PDF.

Certificado del sello:
  - Si defines SIGN_CERT_FILE + SIGN_KEY_FILE (y SIGN_KEY_PASSWORD si aplica) en .env,
    se usa ese certificado (p. ej. uno comercial de la lista AATL de Adobe, que Acrobat
    muestra con la marca verde de "firma válida").
  - Si no, se genera automáticamente un certificado propio por organización en
    contract_files/_keys/. Protege igual la integridad, pero Acrobat mostrará
    "identidad del firmante desconocida" hasta que lo marques como confiable.
  - SIGN_TSA_URL (opcional): servidor de sello de tiempo RFC 3161 (p. ej.
    http://timestamp.digicert.com) para fijar la hora de forma independiente.
"""
import base64
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import instance_config as cfg
from models.contract import FILES_ROOT, describe_user_agent, EVENT_LABELS

log = logging.getLogger(__name__)


def _rgb(hex_color, fallback=(0.04, 0.05, 0.08)):
    h = (hex_color or '').lstrip('#')
    try:
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return fallback


def _tz():
    try:
        return ZoneInfo(cfg.timezone_name())
    except Exception:
        return ZoneInfo('America/New_York')


NAVY = _rgb(cfg.esign()['dark'])
AQUA_TEXT = _rgb(cfg.esign()['link'])
MUTED = (71 / 255, 85 / 255, 105 / 255)


def fmt_local(ts, fmt='%m/%d/%Y %I:%M:%S %p'):
    if not ts:
        return ''
    try:
        dt = datetime.strptime(str(ts)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        local = dt.astimezone(_tz())
        return local.strftime(fmt) + (f' {local.strftime("%Z")}' if '%H' in fmt or '%I' in fmt else '')
    except ValueError:
        return str(ts)


def _png_reader(data_url):
    from reportlab.lib.utils import ImageReader
    if not data_url or ',' not in data_url:
        return None
    try:
        raw = base64.b64decode(data_url.split(',', 1)[1])
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _fit_font(c, text, font, max_w, start):
    size = start
    while size > 5 and c.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


# ── 1. stamping ──────────────────────────────────────────────────────────────

def _stamp_pages(original_bytes, env):
    from pypdf import PdfReader, PdfWriter, Transformation
    from reportlab.pdfgen import canvas as rl_canvas

    reader = PdfReader(io.BytesIO(original_bytes))
    writer = PdfWriter()
    rcpts = {r['id']: r for r in env['recipients']}
    short_id = env['uid'].split('-')[0]
    sign_name = cfg.esign()['name']

    for idx, page in enumerate(reader.pages, start=1):
        try:
            page.transfer_rotation_to_content()
        except Exception:
            pass
        box = page.cropbox
        llx, lly = float(box.left), float(box.bottom)
        pw, ph = float(box.width), float(box.height)

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
        # Encabezado discreto en cada página (como el "Envelope ID" de DocuSign)
        c.setFont('Helvetica', 6.5)
        c.setFillColorRGB(*MUTED)
        c.drawString(14, ph - 12, f'{sign_name} · ID del sobre: {env["uid"]}')

        for f in env['fields']:
            if f['page'] != idx or f.get('value') in (None, ''):
                continue
            rc = rcpts.get(f['recipient_id'])
            if not rc:
                continue
            x, w = f['x'] * pw, f['w'] * pw
            h = f['h'] * ph
            y = ph - (f['y'] * ph) - h          # origen abajo-izquierda en PDF
            t = f['type']
            if t in ('signature', 'initials'):
                img = _png_reader(rc['signature_png'] if t == 'signature' else (rc['initials_png'] or rc['signature_png']))
                if img:
                    pad = 1.5
                    c.drawImage(img, x + pad, y + pad, w - 2 * pad, h - 2 * pad - (5 if t == 'signature' else 0),
                                mask='auto', preserveAspectRatio=True, anchor='sw')
                if t == 'signature':
                    c.setStrokeColorRGB(*AQUA_TEXT)
                    c.setLineWidth(0.6)
                    c.line(x, y, x, y + h)            # ribete lateral tipo "firmado por"
                    c.setFont('Helvetica', 4.8)
                    c.setFillColorRGB(*AQUA_TEXT)
                    c.drawString(x + 2.5, y + h - 4.8, f'Firmado con {sign_name} · {short_id}')
            elif t == 'checkbox':
                c.setStrokeColorRGB(*NAVY)
                c.setLineWidth(0.8)
                side = min(w, h)
                c.rect(x, y + (h - side) / 2, side, side)
                if f['value'] == '1':
                    c.setLineWidth(1.4)
                    bx, by = x, y + (h - side) / 2
                    c.line(bx + side * .2, by + side * .5, bx + side * .42, by + side * .25)
                    c.line(bx + side * .42, by + side * .25, bx + side * .82, by + side * .78)
            else:
                text = f['value']
                if t == 'date_signed':
                    text = fmt_local(text, '%m/%d/%Y')
                c.setFillColorRGB(*NAVY)
                size = _fit_font(c, text, 'Helvetica', w - 4, min(11, max(6, h * 0.62)))
                c.setFont('Helvetica', size)
                c.drawString(x + 2, y + (h - size) / 2 + size * 0.18, text)
        c.save()
        overlay = PdfReader(io.BytesIO(buf.getvalue())).pages[0]
        page.merge_transformed_page(overlay, Transformation().translate(llx, lly))
        writer.add_page(page)

    writer.add_metadata({'/Title': env['title'], '/Producer': f'{sign_name} — {cfg.esign()["legal_name"]}',
                         '/Subject': f'Sobre {env["uid"]}'})
    return writer


# ── 2. certificate of completion ─────────────────────────────────────────────

def _certificate_pdf(env, org_name):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
                                    KeepTogether)

    navy = colors.Color(*NAVY)
    muted = colors.Color(*MUTED)
    border = colors.HexColor('#DCE3EB')
    base = ParagraphStyle('b', fontName='Helvetica', fontSize=8.5, leading=11.5, textColor=navy)
    small = ParagraphStyle('s', parent=base, fontSize=7.5, leading=10, textColor=muted)
    label = ParagraphStyle('l', parent=small, fontName='Helvetica-Bold', textColor=muted)
    h1 = ParagraphStyle('h1', parent=base, fontName='Helvetica-Bold', fontSize=18, leading=22)
    h2 = ParagraphStyle('h2', parent=base, fontName='Helvetica-Bold', fontSize=11, leading=14, spaceBefore=10,
                        spaceAfter=6)
    eyebrow = ParagraphStyle('e', parent=base, fontName='Helvetica-Bold', fontSize=7.5,
                             textColor=colors.Color(*AQUA_TEXT))

    def esc(s):
        return (str(s or '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                            topMargin=0.7 * inch, bottomMargin=0.7 * inch,
                            title=f'Certificado de finalización — {env["title"]}')
    sign_name = cfg.esign()['name']
    story = [Paragraph(esc(sign_name.upper()), eyebrow), Paragraph('Certificado de finalización', h1), Spacer(1, 8)]

    signers = [r for r in env['recipients'] if r['role'] == 'signer']
    ccs = [r for r in env['recipients'] if r['role'] == 'cc']
    info = [
        [Paragraph('ID DEL SOBRE', label), Paragraph(esc(env['uid']), base),
         Paragraph('ESTADO', label), Paragraph('Completado', base)],
        [Paragraph('DOCUMENTO', label), Paragraph(esc(env['title']), base),
         Paragraph('PÁGINAS', label), Paragraph(str(env['page_count']), base)],
        [Paragraph('REMITENTE', label), Paragraph(esc(f"{env.get('created_by_name') or ''} · {org_name}"), base),
         Paragraph('FIRMANTES', label), Paragraph(str(len(signers)), base)],
        [Paragraph('ENVIADO', label), Paragraph(fmt_local(env.get('sent_at')), base),
         Paragraph('COMPLETADO', label), Paragraph(fmt_local(env.get('completed_at')), base)],
        [Paragraph('SHA-256 DEL ORIGINAL', label),
         Paragraph(f'<font name="Courier" size="7.5">{env["original_sha256"]}</font>', base), '', ''],
    ]
    t = Table(info, colWidths=[1.25 * inch, 2.75 * inch, 0.95 * inch, 2.05 * inch])
    t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('SPAN', (1, 4), (3, 4)),
                           ('LINEBELOW', (0, 0), (-1, -1), 0.4, border),
                           ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
    story += [t, Paragraph('Firmantes', h2)]

    for r in signers:
        img_cell = ''
        sig = r.get('signature_png')
        if sig and ',' in sig:
            try:
                im = Image(io.BytesIO(base64.b64decode(sig.split(',', 1)[1])))
                ratio = im.imageWidth / float(im.imageHeight or 1)
                im.drawHeight = 0.5 * inch
                im.drawWidth = min(2.2 * inch, 0.5 * inch * ratio)
                img_cell = im
            except Exception:
                img_cell = ''
        auth = {'otp_email': 'Código de un solo uso enviado por email',
                'otp_whatsapp': 'Código de un solo uso enviado por WhatsApp',
                'link': 'Enlace único enviado al destinatario',
                'in_person': 'Firma en persona desde sesión del CRM'}.get(r.get('auth_method') or '', r.get('auth_method') or '—')
        left = [Paragraph(f'<b>{esc(r["name"])}</b>', base),
                Paragraph(esc(r['email'] or r['phone']), small),
                Paragraph(f'Orden de firma: {r["routing_order"]}', small)]
        right = [Paragraph(f'<b>Firmado:</b> {fmt_local(r.get("signed_at"))}', small),
                 Paragraph(f'<b>Consentimiento ESIGN/UETA:</b> {fmt_local(r.get("consent_at")) or "—"}', small),
                 Paragraph(f'<b>Autenticación:</b> {esc(auth)}', small),
                 Paragraph(f'<b>Tipo de firma:</b> {"dibujada" if r.get("signature_kind") == "draw" else "escrita (estilo adoptado)"}', small),
                 Paragraph(f'<b>IP:</b> {esc(r.get("signed_ip"))} · <b>Dispositivo:</b> {esc(describe_user_agent(r.get("signed_ua") or ""))}', small)]
        st = Table([[left, img_cell, right]], colWidths=[1.9 * inch, 2.3 * inch, 2.8 * inch])
        st.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.6, border),
                                ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                                ('LEFTPADDING', (0, 0), (-1, -1), 7)]))
        story += [KeepTogether([st, Spacer(1, 6)])]

    if ccs:
        story.append(Paragraph('Copias (CC)', h2))
        for r in ccs:
            story.append(Paragraph(f'{esc(r["name"])} · {esc(r["email"] or r["phone"])}', base))

    story.append(Paragraph('Historial de eventos', h2))
    rows = [[Paragraph('<b>Fecha y hora (ET)</b>', small), Paragraph('<b>Evento</b>', small),
             Paragraph('<b>Actor</b>', small), Paragraph('<b>IP / dispositivo</b>', small)]]
    for ev in env['events']:
        detail = f'{EVENT_LABELS.get(ev["event"], ev["event"])}'
        if ev.get('detail') and ev['event'] not in ('signed',):
            detail += f'<br/><font color="#475569">{esc(ev["detail"])[:220]}</font>'
        rows.append([Paragraph(fmt_local(ev['created_at']), small), Paragraph(detail, small),
                     Paragraph(esc(ev.get('actor')), small),
                     Paragraph(esc(' · '.join(x for x in (ev.get('ip'), describe_user_agent(ev.get('user_agent') or '')) if x) or '—'), small)])
    et = Table(rows, colWidths=[1.35 * inch, 2.75 * inch, 1.25 * inch, 1.65 * inch], repeatRows=1)
    et.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 0), (-1, -1), 0.3, border),
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F4F7FA')),
                            ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
    story += [et, Spacer(1, 14), Paragraph(
        'Este documento fue firmado electrónicamente conforme a la ley federal ESIGN (15 U.S.C. § 7001 y ss.) '
        'y la Ley Uniforme de Transacciones Electrónicas (UETA) adoptada por el estado. '
        'Cada firmante aceptó realizar la transacción por medios electrónicos antes de firmar. '
        'El archivo está sellado digitalmente: cualquier modificación posterior invalida el sello. '
        'Verifica su integridad en la página /verificar del CRM con el ID del sobre o subiendo el PDF.', small)]

    def _footer(canvas, _doc):
        canvas.setFont('Helvetica', 6.5)
        canvas.setFillColor(muted)
        canvas.drawString(0.75 * inch, 0.45 * inch, f'{sign_name} · Certificado de finalización · {env["uid"]}')
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.45 * inch, f'Página {_doc.page}')

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


# ── 3. digital seal (PAdES) ──────────────────────────────────────────────────

def _org_keypair(org_id, org_name):
    """Certificado del sello. Usa el configurado en .env o genera uno propio por organización."""
    cert_file, key_file = os.getenv('SIGN_CERT_FILE'), os.getenv('SIGN_KEY_FILE')
    if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
        pw = os.getenv('SIGN_KEY_PASSWORD')
        return cert_file, key_file, (pw.encode() if pw else None)

    folder = os.path.join(FILES_ROOT, '_keys', f'org_{int(org_id)}')
    cert_path, key_path = os.path.join(folder, 'seal_cert.pem'), os.path.join(folder, 'seal_key.pem')
    if os.path.exists(cert_path) and os.path.exists(key_path):
        return cert_path, key_path, None

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    os.makedirs(folder, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'{org_name} — {cfg.esign()["name"]}'),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, org_name),
                      x509.NameAttribute(NameOID.COUNTRY_NAME, 'US')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False)
            .sign(key, hashes.SHA256()))
    with open(key_path, 'wb') as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                   serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    with open(cert_path, 'wb') as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, None


def _seal(pdf_bytes, env, org_name):
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers, fields as sig_fields
    from pyhanko.sign.timestamps import HTTPTimeStamper

    cert_path, key_path, pw = _org_keypair(env['org_id'], org_name)
    signer = signers.SimpleSigner.load(key_path, cert_path, key_passphrase=pw)
    meta = signers.PdfSignatureMetadata(
        field_name='ESignSeal', md_algorithm='sha256',
        subfilter=sig_fields.SigSeedSubFilter.PADES,
        reason=f'Sellado de sobre completado {env["uid"]}', location=f'{cfg.esign()["name"]} — {cfg.base_url() or org_name}',
        name=f'{org_name} ({cfg.esign()["name"]})')
    tsa_url = os.getenv('SIGN_TSA_URL')
    timestamper = HTTPTimeStamper(tsa_url) if tsa_url else None
    out = io.BytesIO()
    signers.PdfSigner(meta, signer=signer, timestamper=timestamper).sign_pdf(
        IncrementalPdfFileWriter(io.BytesIO(pdf_bytes)), output=out)
    return out.getvalue()


def build_final_pdf(env, original_bytes, org_name=None):
    """Devuelve (pdf_bytes, sealed: bool)."""
    from pypdf import PdfReader
    global NAVY, AQUA_TEXT
    NAVY, AQUA_TEXT = _rgb(cfg.esign()['dark']), _rgb(cfg.esign()['link'])   # marca vigente
    org_name = org_name or cfg.esign()['legal_name']
    writer = _stamp_pages(original_bytes, env)
    for page in PdfReader(io.BytesIO(_certificate_pdf(env, org_name))).pages:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    try:
        return _seal(data, env, org_name), True
    except Exception:
        log.exception('Contratos: no se pudo aplicar el sello PAdES; se guarda el PDF sin sello')
        return data, False
