"""Recordatorios de pago por email (1 día antes del vencimiento de cada cuota).

Usa la plantilla templates/emails/recordatorio_pago.html y envía con
services.mailer.send_email(), así cada recordatorio queda en Emails → Enviados.
Requiere un app context de Flask.
"""
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from models.payment_schedule import due_for_reminder, mark_reminder_sent
from services.mailer import MailerError, send_email

TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']
DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def fecha_larga(iso):
    d = datetime.strptime(iso, '%Y-%m-%d')
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def build_reminder(r):
    """Devuelve (subject, html) para una cuota con datos del cliente (fila de due_for_reminder)."""
    company = os.getenv('COMPANY_NAME', 'Papia Technology Solutions LLC')
    nombre = f"{r.get('first_name') or ''} {r.get('last_name') or ''}".strip() or 'cliente'
    monto = f"{r['amount']:,.2f}"
    fecha = fecha_larga(r['due_date'])
    subject = f"Recordatorio de pago – {r['label']} vence mañana ({fecha})"
    # jinja_env directo: funciona también fuera de una petición web (scheduled task)
    html = current_app.jinja_env.get_template('emails/recordatorio_pago.html').render(
        cliente_nombre=nombre,
        cuota_nombre=r['label'],
        monto=monto,
        fecha_pago=fecha,
        whatsapp_texto=(f"Hola, envío el comprobante de mi {r['label']} (${monto}) "
                        f"a {company.replace(' LLC', '')}."),
    )
    return subject, html


def send_reminder(r):
    """Envía el recordatorio de una cuota, guarda copia en Enviados y marca la cuota."""
    if not r.get('email'):
        raise MailerError('El cliente no tiene email registrado.')
    subject, html = build_reminder(r)
    gmail_id, _ = send_email(
        r.get('org_id') or 1, r['email'], subject, html, client_id=r['client_id'],
    )
    mark_reminder_sent(r['id'])  # registra fecha de envío + nota en el perfil
    return gmail_id


def run_daily(days_ahead=1, today=None):
    """Envía los recordatorios de las cuotas pendientes que vencen en `days_ahead` días.
    Las que ya tienen reminder_sent_at se saltan (no hay duplicados)."""
    today = today or datetime.now(TZ).date()
    target = (today + timedelta(days=days_ahead)).isoformat()
    sent, skipped, errors = [], [], []
    for r in due_for_reminder(target):
        label = f"#{r['id']} {r['label']} – {r.get('first_name') or ''} {r.get('last_name') or ''}".strip()
        if not r.get('email'):
            skipped.append(f"{label}: sin email")
            continue
        try:
            send_reminder(r)
            sent.append(label)
        except Exception as exc:  # sigue con las demás cuotas
            errors.append(f"{label}: {exc}")
    return {'date': target, 'sent': sent, 'skipped': skipped, 'errors': errors}
