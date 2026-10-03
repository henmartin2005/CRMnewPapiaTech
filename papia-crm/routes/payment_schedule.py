"""Cronograma de pagos en el perfil del cliente + API para recordatorios con n8n."""
import hmac
import os
import re
from datetime import datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

from models.client import get_client
from models.payment_schedule import (
    add_installment, delete_installment, due_for_reminder, mark_paid,
    mark_reminder_sent, mark_unpaid,
)

payment_schedule_bp = Blueprint('payment_schedule', __name__)

TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
         'septiembre', 'octubre', 'noviembre', 'diciembre']
DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo']


def _org():
    return g.org_id if hasattr(g, 'org_id') else 1


def _back(client_id):
    return redirect(url_for('clients.detail', client_id=client_id) + '#cronograma')


def fecha_larga(iso):
    d = datetime.strptime(iso, '%Y-%m-%d')
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


# ── UI (perfil del cliente) ──────────────────────────────────────────────────

@payment_schedule_bp.route('/clients/<int:client_id>/schedule', methods=['POST'])
def add(client_id):
    org_id = _org()
    client = get_client(client_id, org_id=org_id)
    if not client:
        abort(404)
    label = request.form.get('label', '').strip()
    due = request.form.get('due_date', '').strip()
    try:
        amount = float(request.form.get('amount', '0').replace(',', '').replace('$', ''))
        datetime.strptime(due, '%Y-%m-%d')
    except ValueError:
        flash('Fecha o monto inválido.', 'danger')
        return _back(client_id)
    if amount <= 0 or not label:
        flash('Indica concepto y monto mayor que 0.', 'danger')
        return _back(client_id)
    already_paid = request.form.get('already_paid') == '1'
    add_installment(
        client_id, org_id, label[:120], due, amount,
        already_paid=already_paid,
        create_task='1' in request.form.getlist('create_task'),
        client_name=f"{client['first_name']} {client['last_name'] or ''}".strip(),
    )
    flash('Cuota agregada al cronograma.', 'success')
    return _back(client_id)


@payment_schedule_bp.route('/clients/<int:client_id>/schedule/<int:inst_id>/paid', methods=['POST'])
def paid(client_id, inst_id):
    inst = mark_paid(inst_id, client_id, _org())
    flash(f"{inst['label']} marcada como pagada." if inst else 'No se pudo marcar.',
          'success' if inst else 'danger')
    return _back(client_id)


@payment_schedule_bp.route('/clients/<int:client_id>/schedule/<int:inst_id>/unpaid', methods=['POST'])
def unpaid(client_id, inst_id):
    inst = mark_unpaid(inst_id, client_id, _org())
    flash(f"{inst['label']} vuelve a pendiente." if inst else 'No se pudo revertir.',
          'success' if inst else 'danger')
    return _back(client_id)


@payment_schedule_bp.route('/clients/<int:client_id>/schedule/<int:inst_id>/delete', methods=['POST'])
def delete(client_id, inst_id):
    inst = delete_installment(inst_id, client_id, _org())
    flash('Cuota eliminada.' if inst else 'Cuota no encontrada.', 'success' if inst else 'danger')
    return _back(client_id)


# ── API para n8n (protegida con X-API-Key = N8N_PAYMENTS_TOKEN) ──────────────

def _check_token():
    expected = os.getenv('N8N_PAYMENTS_TOKEN', '')
    given = request.headers.get('X-API-Key', '')
    if not expected:
        abort(503, 'N8N_PAYMENTS_TOKEN no configurado')
    if not hmac.compare_digest(given, expected):
        abort(401)


def _email_html(r, payment_email, crm_name):
    nombre = escape(r['first_name'] or '')
    empresa = escape(r['company'] or '')
    pendiente_despues = max((r['total_cost'] or 0) - (r['amount_paid'] or 0) - r['amount'], 0)
    nota = empresa or f"{nombre} {escape(r['last_name'] or '')}".strip()
    return f"""<div style="font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:0 auto;color:#1f2937;">
  <p>Hola {nombre},</p>
  <p>Le recordamos que <strong>mañana, {fecha_larga(r['due_date'])}</strong>, vence el
  <strong>{escape(r['label'])}</strong> de su proyecto{(' para ' + empresa) if empresa else ''}.</p>
  <table style="border-collapse:collapse;width:100%;margin:16px 0;font-size:14px;">
    <tr><td style="padding:8px;border:1px solid #e5e7eb;">Monto</td>
        <td style="padding:8px;border:1px solid #e5e7eb;"><strong>${r['amount']:,.2f} USD</strong></td></tr>
    <tr><td style="padding:8px;border:1px solid #e5e7eb;">Fecha</td>
        <td style="padding:8px;border:1px solid #e5e7eb;">{fecha_larga(r['due_date']).capitalize()}</td></tr>
    <tr><td style="padding:8px;border:1px solid #e5e7eb;">Balance pendiente después de este pago</td>
        <td style="padding:8px;border:1px solid #e5e7eb;">${pendiente_despues:,.2f} USD</td></tr>
  </table>
  <p><strong>Método de pago:</strong> Zelle a <strong>{escape(payment_email)}</strong>
  ({escape(crm_name)}). Por favor incluya <em>“{nota}”</em> en la nota del pago.</p>
  <p>Si ya realizó el pago, ignore este mensaje. ¡Gracias por su confianza!</p>
  <p style="margin-top:24px;">Henrry Martin<br>{escape(crm_name)}<br>
  <a href="https://papiatech.com">papiatech.com</a></p>
</div>"""


@payment_schedule_bp.route('/api/payment-reminders/due')
def api_due():
    _check_token()
    try:
        days = int(request.args.get('days', 1))
    except ValueError:
        days = 1
    target = (datetime.now(TZ).date() + timedelta(days=days)).isoformat()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', request.args.get('date', '')):
        target = request.args['date']
    include_sent = request.args.get('include_sent') == '1'

    payment_email = os.getenv('PAYMENT_ZELLE_EMAIL', 'henrry@papiatech.com')
    company_name = os.getenv('COMPANY_NAME', 'Papia Technology Solutions LLC')
    base = os.getenv('APP_BASE_URL', 'https://datos.papiatech.com').rstrip('/')

    items = []
    for r in due_for_reminder(target, include_sent=include_sent):
        if not r.get('email'):
            continue
        items.append({
            'id': r['id'],
            'client_id': r['client_id'],
            'client_name': f"{r['first_name']} {r['last_name'] or ''}".strip(),
            'client_email': r['email'],
            'company': r['company'],
            'label': r['label'],
            'amount': r['amount'],
            'due_date': r['due_date'],
            'subject': f"Recordatorio de pago: {r['label']} — ${r['amount']:,.2f} vence mañana",
            'html': _email_html(r, payment_email, company_name),
            'crm_url': f"{base}/clients/{r['client_id']}#cronograma",
        })
    return jsonify({'date': target, 'count': len(items), 'items': items})


@payment_schedule_bp.route('/api/payment-reminders/<int:inst_id>/sent', methods=['POST'])
def api_sent(inst_id):
    _check_token()
    r = mark_reminder_sent(inst_id)
    if not r:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify({'ok': True, 'id': inst_id})
