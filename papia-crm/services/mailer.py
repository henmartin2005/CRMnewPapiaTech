"""Envío central de correos del CRM.

REGLA: todo correo que envíe el sistema debe pasar por send_email(). Así siempre
queda una copia en Emails → Enviados (tabla `emails`) vinculada al cliente,
además de la copia que Gmail guarda en su carpeta "Enviados".
"""
import base64
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import unescape

from database import get_db


class MailerError(Exception):
    pass


def html_to_text(html):
    """Versión en texto plano de un HTML (para la parte text/plain y la copia en Enviados)."""
    text = re.sub(r'(?is)<(script|style|title).*?</\1>', '', html)
    text = re.sub(r'(?is)<div[^>]*display:\s*none[^>]*>.*?</div>', '', text)  # preheader oculto
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</(p|div|tr|h[1-6]|table|li)>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = unescape(text).replace('\xa0', ' ')
    lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if not ln:
            if not blank and out:
                out.append('')
            blank = True
            continue
        out.append(ln)
        blank = False
    return '\n'.join(out).strip()


def _find_client_id(org_id, to_email):
    first = re.split(r'[,;]', to_email)[0].strip().lower()
    if not first:
        return None
    db = get_db()
    try:
        row = db.execute(
            "SELECT id FROM clients WHERE org_id=? AND lower(email)=?", (org_id, first)
        ).fetchone()
        return row['id'] if row else None
    finally:
        db.close()


def save_sent_copy(org_id, to_email, subject, body, client_id=None, gmail_id=None):
    """Guarda la copia en Emails → Enviados. Úsalo también para correos que salgan
    por fuera del CRM (ej. n8n) para que queden registrados."""
    if client_id is None:
        client_id = _find_client_id(org_id, to_email)
    db = get_db()
    try:
        db.execute(
            "INSERT INTO emails (client_id, direction, to_email, subject, body, gmail_message_id, status, org_id) "
            "VALUES (?, 'sent', ?, ?, ?, ?, 'sent', ?)",
            (client_id, to_email, subject, body, gmail_id, org_id),
        )
        db.commit()
    finally:
        db.close()
    return client_id


def send_email(org_id, to_email, subject, html, text=None, client_id=None, note=None):
    """Envía por el Gmail conectado de la organización y guarda la copia en Enviados.

    - html: cuerpo HTML ya armado.
    - text: versión texto plano (si no se da, se genera desde el HTML).
    - client_id: si es None se busca el cliente por el email del destinatario.
    - note: si se da, se registra como nota tipo 'email' en el perfil del cliente.
    Devuelve (gmail_message_id, client_id). Lanza MailerError si no se puede enviar.
    """
    from routes.emails import _gmail_service  # import tardío: evita ciclos

    service = _gmail_service(org_id)
    if not service:
        raise MailerError('Gmail no conectado. Ve a Emails → Conectar Gmail.')

    text = text or html_to_text(html)
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['To'] = to_email
    msg.attach(MIMEText(text, 'plain', 'utf-8'))
    msg.attach(MIMEText(html, 'html', 'utf-8'))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        sent = service.users().messages().send(userId='me', body={'raw': raw}).execute()
    except Exception as exc:
        raise MailerError(str(exc)) from exc

    gmail_id = sent.get('id')
    client_id = save_sent_copy(org_id, to_email, subject, text, client_id=client_id, gmail_id=gmail_id)

    if note and client_id:
        db = get_db()
        try:
            db.execute(
                "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'email', ?)",
                (int(client_id), note),
            )
            db.commit()
        finally:
            db.close()

    return gmail_id, client_id
