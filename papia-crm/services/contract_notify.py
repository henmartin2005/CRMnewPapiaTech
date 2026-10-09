"""
Papia Sign — notificaciones (Gmail del CRM + WhatsApp vía Zernio).

Todo es "best effort": si un canal falla se registra un evento `notify_failed`
en el historial del sobre y el resto del flujo continúa.
"""
import base64
import html
import logging
import os
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from database import get_db
from models.contract import log_event

log = logging.getLogger(__name__)

NAVY, CORAL = '#0A2540', '#FF6B4A'


def _base_url(org_id):
    try:
        db = get_db()
        row = db.execute("SELECT app_base_url FROM organizations WHERE id=?", (org_id,)).fetchone()
        db.close()
        if row and row['app_base_url']:
            return row['app_base_url'].rstrip('/')
    except Exception:
        pass
    return (os.getenv('APP_BASE_URL') or 'https://datos.papiatech.com').rstrip('/')


def org_name(org_id):
    try:
        db = get_db()
        row = db.execute("SELECT name FROM organizations WHERE id=?", (org_id,)).fetchone()
        db.close()
        return row['name'] if row else 'Papia Technology Solutions'
    except Exception:
        return 'Papia Technology Solutions'


def sign_url(org_id, token):
    return f'{_base_url(org_id)}/firmar/{token}'


# ── email ────────────────────────────────────────────────────────────────────

def _button(url, label):
    return (f'<a href="{html.escape(url)}" style="display:inline-block;background:{CORAL};color:{NAVY};'
            f'font-weight:700;text-decoration:none;padding:13px 26px;border-radius:10px;font-size:15px;">'
            f'{html.escape(label)}</a>')


def send_email(org_id, to_email, subject, paragraphs, button=None, attachments=None, plain_extra=''):
    """paragraphs: lista de textos (se escapan). button: (url, label). attachments: [(nombre, bytes)]."""
    from routes.emails import _gmail_service, _get_settings, _build_html_email
    service = _gmail_service(org_id)
    if not service:
        raise RuntimeError('Gmail no está conectado en el CRM (Emails → Conectar Gmail).')
    parts = [f'<p style="margin:0 0 14px;">{html.escape(p)}</p>' for p in paragraphs if p]
    if button:
        parts.append(f'<p style="margin:22px 0;">{_button(*button)}</p>')
        parts.append('<p style="margin:0;color:#6B7280;font-size:12px;">Este enlace es personal; no lo reenvíes. '
                     'Firma electrónica con Papia Sign.</p>')
    body_html = _build_html_email(''.join(parts), _get_settings(org_id))
    plain = '\n\n'.join(p for p in paragraphs if p)
    if button:
        plain += f'\n\n{button[1]}: {button[0]}'
    if plain_extra:
        plain += '\n\n' + plain_extra

    msg = MIMEMultipart('mixed')
    msg['Subject'], msg['To'] = subject, to_email
    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(plain, 'plain', 'utf-8'))
    alt.attach(MIMEText(body_html, 'html', 'utf-8'))
    msg.attach(alt)
    for name, data in attachments or []:
        part = MIMEApplication(data, _subtype='pdf')
        part.add_header('Content-Disposition', 'attachment', filename=name)
        msg.attach(part)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_id = service.users().messages().send(userId='me', body={'raw': raw}).execute().get('id')

    # Todo correo del sistema queda en Emails → Enviados (vinculado al cliente por su email)
    try:
        from services.mailer import save_sent_copy
        copy = plain
        if attachments:
            copy += '\n\n' + '\n'.join(f'[Adjunto: {name}]' for name, _ in attachments)
        save_sent_copy(org_id, to_email, subject, copy, gmail_id=gmail_id)
    except Exception:
        pass  # el correo ya salió; no romper el flujo de firma por la copia
    return gmail_id


def sender_address(org_id):
    from routes.emails import _gmail_service
    try:
        service = _gmail_service(org_id)
        return service.users().getProfile(userId='me').execute().get('emailAddress') if service else None
    except Exception:
        return None


# ── WhatsApp ─────────────────────────────────────────────────────────────────

def send_whatsapp(org_id, phone, text, template=None, client_id=None):
    from routes.whatsapp import deliver, _record_outbound
    from services import zernio
    try:
        wa_id = deliver(phone, text, org_id=org_id)
    except zernio.TemplateRequired:
        if not template:
            raise
        wa_id = deliver(phone, text, org_id=org_id, template=template)
    try:
        _record_outbound(phone, text, wa_id, org_id, client_id=client_id)
    except Exception:
        log.exception('Papia Sign: no se pudo registrar el WhatsApp saliente')
    return wa_id


# ── high-level messages ──────────────────────────────────────────────────────

def _safe(env, rc, channel, fn):
    try:
        fn()
        return True
    except Exception as exc:
        log.exception('Papia Sign: fallo de notificación')
        log_event(env['id'], env['org_id'], 'notify_failed', rc['id'] if rc else None, actor='Sistema',
                  detail=f'{channel}: {exc}'[:500])
        return False


def invite(env, rc, token, sender_name, reminder=False):
    url = sign_url(env['org_id'], token)
    org = org_name(env['org_id'])
    first = (rc['name'] or '').split()[0] if rc['name'] else ''
    sent = []
    if rc['notify_email'] and rc['email']:
        subject = (f'Recordatorio: firma pendiente — {env["title"]}' if reminder
                   else f'Firma requerida: {env["title"]}')
        paras = [f'Hola {first},',
                 f'{sender_name or org} te envió "{env["title"]}" para revisar y firmar electrónicamente.' if not reminder
                 else f'Todavía tienes pendiente la firma de "{env["title"]}".']
        if env.get('message') and not reminder:
            paras.append(env['message'])
        if _safe(env, rc, 'email', lambda: send_email(env['org_id'], rc['email'], subject, paras,
                                                       button=(url, 'Revisar y firmar'))):
            sent.append('email')
    if rc['notify_whatsapp'] and rc['phone']:
        text = (f'Hola {first}, {"te recordamos que tienes pendiente" if reminder else (sender_name or org) + " te envió"} '
                f'el documento *{env["title"]}* para firmar. Revísalo y fírmalo aquí: {url}')
        tpl = {'name': 'firma_contrato', 'language': 'es',
               'params': [first or rc['name'], sender_name or org, env['title'], url]}
        if _safe(env, rc, 'whatsapp', lambda: send_whatsapp(env['org_id'], rc['phone'], text, template=tpl,
                                                             client_id=env.get('client_id'))):
            sent.append('WhatsApp')
    if sent:
        log_event(env['id'], env['org_id'], 'reminder' if reminder else 'invited', rc['id'], actor='Sistema',
                  detail=f'{rc["name"]} por {" + ".join(sent)}')
    return sent


def send_otp(env, rc, code):
    """Envía el código por email (preferido) o WhatsApp. Devuelve el canal usado o None."""
    text = f'Tu código para abrir "{env["title"]}" es {code}. Vence en 10 minutos. No lo compartas.'
    if rc['email']:
        if _safe(env, rc, 'email OTP', lambda: send_email(
                env['org_id'], rc['email'], f'Código de verificación: {code}',
                [f'Tu código para abrir y firmar "{env["title"]}" es:', code,
                 'Vence en 10 minutos. Si no fuiste tú, ignora este correo.'])):
            return 'email'
    if rc['phone']:
        tpl = {'name': 'codigo_firma', 'language': 'es', 'params': [env['title'], code]}
        if _safe(env, rc, 'WhatsApp OTP', lambda: send_whatsapp(env['org_id'], rc['phone'], text, template=tpl)):
            return 'whatsapp'
    return None


def notify_sender(env, subject, paragraphs, attachments=None):
    to = sender_address(env['org_id'])
    if not to:
        return False
    return _safe(env, None, 'email remitente', lambda: send_email(env['org_id'], to, subject, paragraphs,
                                                                   attachments=attachments))


def send_completed_copies(env, pdf_bytes, filename):
    sent_to = []
    for rc in env['recipients']:
        first = (rc['name'] or '').split()[0] if rc['name'] else ''
        if rc['email']:
            ok = _safe(env, rc, 'email copia', lambda rc=rc, first=first: send_email(
                env['org_id'], rc['email'], f'Completado: {env["title"]}',
                [f'Hola {first},', f'Todas las partes firmaron "{env["title"]}". '
                 'Adjuntamos el documento final con el certificado de finalización.',
                 f'ID del sobre: {env["uid"]}'],
                attachments=[(filename, pdf_bytes)]))
            if ok:
                sent_to.append(rc['name'])
        elif rc['phone']:
            # WhatsApp: sin adjunto (la ventana de 24 h suele estar abierta tras firmar)
            ok = _safe(env, rc, 'whatsapp copia', lambda rc=rc, first=first: send_whatsapp(
                env['org_id'], rc['phone'],
                f'Hola {first}, el documento *{env["title"]}* quedó firmado por todas las partes. '
                f'ID del sobre: {env["uid"]}. Pídenos la copia en PDF por este chat cuando la necesites.',
                client_id=env.get('client_id')))
            if ok:
                sent_to.append(rc['name'])
    if sent_to:
        log_event(env['id'], env['org_id'], 'copy_sent', actor='Sistema', detail=', '.join(sent_to))
