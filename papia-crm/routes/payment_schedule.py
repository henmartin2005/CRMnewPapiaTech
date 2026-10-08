"""Cronograma de pagos en el perfil del cliente + API para recordatorios con n8n."""
import hmac
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for

from models.client import get_client
from models.payment_schedule import (
    add_installment, delete_installment, due_for_reminder, installment_with_client,
    mark_paid, mark_reminder_sent, mark_unpaid,
)
from services.mailer import MailerError, html_to_text, save_sent_copy
from services.payment_reminders import build_reminder, run_daily, send_reminder

payment_schedule_bp = Blueprint('payment_schedule', __name__)

TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))


def _org():
    return g.org_id if hasattr(g, 'org_id') else 1


def _back(client_id):
    return redirect(url_for('clients.detail', client_id=client_id) + '#cronograma')


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


@payment_schedule_bp.route('/clients/<int:client_id>/schedule/<int:inst_id>/remind', methods=['POST'])
def remind(client_id, inst_id):
    """Envía (o reenvía) ahora el recordatorio de pago de una cuota."""
    r = installment_with_client(inst_id, client_id, _org())
    if not r or r['status'] == 'paid':
        flash('Cuota no encontrada o ya pagada.', 'danger')
        return _back(client_id)
    try:
        send_reminder(r)
        flash(f"Recordatorio de {r['label']} enviado a {r['email']}.", 'success')
    except MailerError as exc:
        flash(f"No se pudo enviar el recordatorio: {exc}", 'danger')
    return _back(client_id)


# ── API para n8n (protegida con X-API-Key = N8N_PAYMENTS_TOKEN) ──────────────

def _check_token():
    expected = os.getenv('N8N_PAYMENTS_TOKEN', '')
    given = request.headers.get('X-API-Key', '')
    if not expected:
        abort(503, 'N8N_PAYMENTS_TOKEN no configurado')
    if not hmac.compare_digest(given, expected):
        abort(401)


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

    base = os.getenv('APP_BASE_URL', 'https://datos.papiatech.com').rstrip('/')

    items = []
    for r in due_for_reminder(target, include_sent=include_sent):
        if not r.get('email'):
            continue
        subject, html = build_reminder(r)
        items.append({
            'id': r['id'],
            'client_id': r['client_id'],
            'client_name': f"{r['first_name']} {r['last_name'] or ''}".strip(),
            'client_email': r['email'],
            'company': r['company'],
            'label': r['label'],
            'amount': r['amount'],
            'due_date': r['due_date'],
            'subject': subject,
            'html': html,
            'crm_url': f"{base}/clients/{r['client_id']}#cronograma",
        })
    return jsonify({'date': target, 'count': len(items), 'items': items})


@payment_schedule_bp.route('/api/payment-reminders/<int:inst_id>/sent', methods=['POST'])
def api_sent(inst_id):
    """n8n avisa que envió el recordatorio por su cuenta: se marca la cuota y se guarda
    la copia en Emails → Enviados (todo correo del sistema queda registrado)."""
    _check_token()
    r = mark_reminder_sent(inst_id)
    if not r:
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    full = installment_with_client(inst_id, r['client_id'], r['org_id'])
    if full and full.get('email'):
        subject, html = build_reminder(full)
        save_sent_copy(full['org_id'], full['email'], subject, html_to_text(html),
                       client_id=full['client_id'])
    return jsonify({'ok': True, 'id': inst_id})


@payment_schedule_bp.route('/api/payment-reminders/run', methods=['POST'])
def api_run():
    """Ejecuta el envío diario desde el propio CRM (alternativa al scheduled task)."""
    _check_token()
    try:
        days = int(request.args.get('days', 1))
    except ValueError:
        days = 1
    return jsonify(run_daily(days_ahead=days))
