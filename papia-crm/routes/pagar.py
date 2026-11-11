"""Página pública de pago de una cuota: /pagar/<token>.

El link es permanente (sirve en el perfil, el portal y el email de recordatorio).
Muestra el precio por Zelle y el precio con tarjeta; al elegir tarjeta se crea
(o reutiliza) un Checkout de Stripe, que solo dura 24 h."""
import os

from flask import Blueprint, abort, redirect, render_template, request

from models import payments_ledger as ledger
from services import stripe_fees

pagar_bp = Blueprint('pagar', __name__)


def base_url():
    return (os.getenv('APP_BASE_URL') or 'https://datos.papiatech.com').rstrip('/')


def pay_url(inst_id):
    """URL pública permanente para pagar una cuota (crea el token si falta)."""
    try:
        token = ledger.pay_token_for(int(inst_id))
    except Exception:
        return ''
    return f'{base_url()}/pagar/{token}' if token else ''


@pagar_bp.app_context_processor
def _inject():
    return {'pay_url': pay_url}


def _company():
    return os.getenv('COMPANY_NAME', 'Papia Technology Solutions LLC')


@pagar_bp.route('/pagar/<token>')
def page(token):
    ledger.ensure_schema()
    inst = ledger.installment_by_token(token)
    if not inst:
        abort(404)
    cfg = stripe_fees.get_settings(inst['org_id'] or 1)
    q = stripe_fees.quote(inst['amount'], cfg['fee_pct'], cfg['fee_fixed'])
    return render_template(
        'pagos/pagar.html', inst=inst, q=q, cfg=cfg, token=token,
        name=f"{inst['first_name']} {inst['last_name'] or ''}".strip(),
        company=_company(),
        zelle_email=cfg.get('zelle_email') or os.getenv('ZELLE_EMAIL', 'henrry@papiatech.com'),
        whatsapp=os.getenv('PAYMENTS_WHATSAPP', '13059024632'),
        cancelled=request.args.get('cancelado') == '1',
        just_paid=request.args.get('pagado') == '1',
    )


@pagar_bp.route('/pagar/<token>/tarjeta', methods=['POST'])
def card(token):
    from routes.payments import PaymentLinkError, create_checkout

    inst = ledger.installment_by_token(token)
    if not inst:
        abort(404)
    if inst['status'] == 'paid':
        return redirect(f'/pagar/{token}')
    org_id = inst['org_id'] or 1
    cfg = stripe_fees.get_settings(org_id)
    charge = stripe_fees.quote(inst['amount'], cfg['fee_pct'], cfg['fee_fixed'])['card'] \
        if cfg['pass_fees'] else float(inst['amount'])
    existing = ledger.open_checkout_for(inst['id'], charge)
    if existing:
        return redirect(existing, code=303)
    name = f"{inst['first_name']} {inst['last_name'] or ''}".strip()
    page_url = f'{base_url()}/pagar/{token}'
    try:
        res = create_checkout(
            org_id, inst['client_id'], inst['amount'], f"{inst['label']} — {name}",
            installment_id=inst['id'],
            success_url=page_url + '?pagado=1',
            cancel_url=page_url + '?cancelado=1',
        )
    except PaymentLinkError:
        return redirect(page_url + '?cancelado=1')
    return redirect(res['url'], code=303)
