"""Pestaña Pagos: cobros recibidos, cuotas por cobrar, saldos por cliente y
cálculo del precio con tarjeta (comisión de Stripe) vs. pago directo (Zelle)."""
from datetime import datetime

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from models import payments_ledger as ledger
from services import stripe_fees

pagos_bp = Blueprint('pagos', __name__)


def _org():
    return g.org_id if hasattr(g, 'org_id') else 1


@pagos_bp.before_request
def _admin_only():
    if session.get('user_role') not in ('admin', 'superadmin'):
        abort(403)


def _month_arg():
    m = (request.args.get('mes') or '').strip()
    try:
        datetime.strptime(m, '%Y-%m')
        return m
    except ValueError:
        return datetime.now().strftime('%Y-%m')


def _shift_month(month, delta):
    y, m = map(int, month.split('-'))
    m += delta
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f'{y:04d}-{m:02d}'


_MONTHS_ES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
              'septiembre', 'octubre', 'noviembre', 'diciembre']


@pagos_bp.route('/pagos')
def index():
    org_id = _org()
    ledger.ensure_schema()
    month = _month_arg()
    method = request.args.get('metodo') or None
    client_id = request.args.get('cliente', type=int)
    cfg = stripe_fees.get_settings(org_id)

    pending = ledger.pending_installments(org_id)
    for p in pending:
        p['quote'] = stripe_fees.quote(p['amount'], cfg['fee_pct'], cfg['fee_fixed'])

    hist = ledger.history(org_id, month=month, method=method, client_id=client_id)
    balances = ledger.client_balances(org_id)
    from database import get_db
    db = get_db()
    clients = [dict(r) for r in db.execute(
        "SELECT id, first_name, last_name, company FROM clients WHERE org_id=? "
        "ORDER BY first_name, last_name", (org_id,)).fetchall()]
    db.close()
    y, m = map(int, month.split('-'))
    return render_template(
        'pagos/index.html',
        month=month,
        month_label=f'{_MONTHS_ES[m - 1].capitalize()} {y}',
        prev_month=_shift_month(month, -1),
        next_month=_shift_month(month, 1),
        kpi=ledger.kpis(org_id, month),
        pending=pending,
        history=hist,
        balances=balances,
        clients=clients,
        cfg=cfg,
        sample=stripe_fees.quote(250, cfg['fee_pct'], cfg['fee_fixed']),
        methods=ledger.METHODS,
        method_labels=ledger.METHOD_LABELS,
        f_method=method or '',
        f_client=client_id or '',
        today=datetime.now().strftime('%Y-%m-%d'),
    )


@pagos_bp.route('/pagos/ajustes', methods=['POST'])
def save_settings():
    try:
        stripe_fees.save_settings(
            _org(),
            float(request.form.get('fee_pct', stripe_fees.DEFAULT_PCT)),
            float(request.form.get('fee_fixed', stripe_fees.DEFAULT_FIXED)),
            request.form.get('pass_fees') == '1',
            request.form.get('zelle_email', ''),
        )
        flash('Ajustes de pagos guardados.', 'success')
    except ValueError:
        flash('Valores de comisión inválidos.', 'danger')
    return redirect(url_for('pagos.index', mes=request.form.get('mes') or None))


@pagos_bp.route('/pagos/cotizar')
def cotizar():
    """JSON con el desglose para un monto neto (lo usa el perfil del cliente)."""
    cfg = stripe_fees.get_settings(_org())
    try:
        net = float(request.args.get('neto', '0'))
    except ValueError:
        net = 0
    return jsonify({**stripe_fees.quote(net, cfg['fee_pct'], cfg['fee_fixed']),
                    'pass_fees': cfg['pass_fees'], 'fee_pct': cfg['fee_pct'],
                    'fee_fixed': cfg['fee_fixed']})


@pagos_bp.route('/pagos/manual', methods=['POST'])
def manual():
    org_id = _org()
    client_id = request.form.get('client_id', type=int)
    inst_id = request.form.get('installment_id', type=int)
    method = request.form.get('method', 'zelle')
    if method not in ledger.METHOD_LABELS:
        method = 'other'
    paid_on = (request.form.get('paid_on') or datetime.now().strftime('%Y-%m-%d'))[:10]
    try:
        datetime.strptime(paid_on, '%Y-%m-%d')
        amount = float((request.form.get('amount') or '0').replace(',', '').replace('$', ''))
    except ValueError:
        flash('Fecha o monto inválido.', 'danger')
        return redirect(request.referrer or url_for('pagos.index'))
    if not client_id or (amount <= 0 and not inst_id):
        flash('Elige el cliente y un monto mayor que 0.', 'danger')
        return redirect(request.referrer or url_for('pagos.index'))
    rid = ledger.record_manual(org_id, client_id, amount, method, paid_on,
                               request.form.get('note', '').strip(), inst_id)
    flash('Pago registrado.' if rid else 'No se pudo registrar (¿la cuota ya estaba pagada?).',
          'success' if rid else 'danger')
    return redirect(request.referrer or url_for('pagos.index'))


@pagos_bp.route('/pagos/manual/<int:record_id>/delete', methods=['POST'])
def manual_delete(record_id):
    r = ledger.delete_manual(_org(), record_id)
    flash('Pago eliminado y saldo del cliente ajustado.' if r else 'Pago no encontrado.',
          'success' if r else 'danger')
    return redirect(request.referrer or url_for('pagos.index'))


@pagos_bp.route('/pagos/cuota/<int:inst_id>/link', methods=['POST'])
def installment_link(inst_id):
    """Link de Stripe para una cuota; el webhook la marca pagada al cobrarse."""
    from models.payment_schedule import installment_with_client
    from routes.payments import PaymentLinkError, create_checkout

    org_id = _org()
    data = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    inst = installment_with_client(inst_id, int(client_id or 0), org_id)
    if not inst or inst['status'] == 'paid':
        return jsonify({'success': False, 'error': 'Cuota no encontrada o ya pagada'}), 404
    name = f"{inst['first_name']} {inst['last_name'] or ''}".strip()
    try:
        res = create_checkout(org_id, inst['client_id'], inst['amount'],
                              f"{inst['label']} — {name}", installment_id=inst_id)
        return jsonify({'success': True, **res, 'name': name})
    except PaymentLinkError as exc:
        return jsonify({'success': False, 'error': str(exc)}), exc.status
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@pagos_bp.route('/pagos/sincronizar', methods=['POST'])
def sync_fees():
    from routes.payments import refresh_missing_fees
    n = refresh_missing_fees(_org())
    flash(f'Comisiones reales actualizadas en {n} pago(s).' if n else
          'No hay comisiones nuevas para actualizar.', 'success')
    return redirect(request.referrer or url_for('pagos.index'))
