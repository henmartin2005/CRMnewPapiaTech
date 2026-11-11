import os
import time
from flask import Blueprint, request, jsonify, render_template, session, g
from database import get_db

payments_bp = Blueprint('payments', __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_org_id():
    return getattr(g, 'org_id', None) or session.get('org_id', 1)


def _get_org_stripe(org_id):
    """Return (secret_key, webhook_secret, base_url) for the org.
    Falls back to .env globals if the org has no keys configured."""
    db  = get_db()
    row = db.execute(
        "SELECT stripe_secret_key, stripe_webhook_secret, app_base_url FROM organizations WHERE id=?",
        (org_id,)
    ).fetchone()
    db.close()

    secret_key     = (row['stripe_secret_key']     or '').strip() if row else ''
    webhook_secret = (row['stripe_webhook_secret'] or '').strip() if row else ''
    base_url       = (row['app_base_url']          or '').strip() if row else ''

    # Fall back to .env if org hasn't configured their own keys
    if not secret_key:
        secret_key = os.getenv('STRIPE_SECRET_KEY', '')
    if not webhook_secret:
        webhook_secret = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    if not base_url:
        base_url = os.getenv('APP_BASE_URL', 'https://datos.papiatech.com')

    return secret_key, webhook_secret, base_url


def _stripe(secret_key):
    import stripe
    stripe.api_key = secret_key
    return stripe


# ── Create checkout session (payment link) ────────────────────────────────────

class PaymentLinkError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def create_checkout(org_id, client_id, amount, description='Pago — Papia Technology Solutions',
                    currency='usd', pass_fees=None, installment_id=None,
                    success_url=None, cancel_url=None):
    """Crea un Checkout de Stripe. `amount` es lo que el negocio quiere recibir
    (precio Zelle). Si pass_fees, el cliente paga el precio con tarjeta, que
    incluye la comisión de Stripe. Devuelve dict con url, cobro y desglose."""
    from models.payments_ledger import ensure_schema
    from services import stripe_fees

    try:
        amount = round(float(amount), 2)
    except (ValueError, TypeError):
        raise PaymentLinkError('Monto inválido')
    if amount < 0.50:
        raise PaymentLinkError('El monto mínimo es $0.50')

    secret_key, _, base_url = _get_org_stripe(org_id)
    if not secret_key:
        raise PaymentLinkError('Configura tu Stripe Secret Key en Configuración → Pagos', 503)

    cfg = stripe_fees.get_settings(org_id)
    if pass_fees is None:
        pass_fees = cfg['pass_fees']
    pct, fixed = cfg['fee_pct'], cfg['fee_fixed']
    if pass_fees:
        q = stripe_fees.quote(amount, pct, fixed)
        charge, fee_est, net_amount = q['card'], q['fee'], amount
    else:
        charge = amount
        fee_est = stripe_fees.fee_for(amount, pct, fixed)
        net_amount = round(amount - fee_est, 2)

    ensure_schema()
    stripe = _stripe(secret_key)
    db = get_db()
    client = db.execute("SELECT first_name, last_name, email FROM clients WHERE id=? AND org_id=?",
                        (client_id, org_id)).fetchone()
    db.close()
    if not client:
        raise PaymentLinkError('Cliente no encontrado', 404)

    params = dict(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': currency,
                'product_data': {
                    'name': description,
                    'description': 'Papia Technology Solutions LLC',
                },
                'unit_amount': int(round(charge * 100)),
            },
            'quantity': 1,
        }],
        mode='payment',
        customer_email=client['email'] or None,
        success_url=success_url or f'{base_url}/payments/success?session_id={{CHECKOUT_SESSION_ID}}',
        cancel_url=cancel_url or f'{base_url}/clients/{client_id}',
        metadata={'client_id': str(client_id), 'crm': 'papia',
                  'credit_amount': f'{amount:.2f}',
                  'installment_id': str(installment_id or '')},
    )
    if pass_fees:
        zelle = cfg.get('zelle_email') or ''
        msg = (f'Precio con tarjeta. Pagando directo por Zelle'
               f'{" a " + zelle if zelle else ""} el monto es ${amount:,.2f}.')
        params['custom_text'] = {'submit': {'message': msg}}

    session_obj = stripe.checkout.Session.create(**params)

    db = get_db()
    db.execute("""
        INSERT INTO payment_links
            (client_id, org_id, stripe_session_id, stripe_link_url, amount, currency, description,
             net_amount, fee_estimated, pass_fees, installment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (client_id, org_id, session_obj.id, session_obj.url, charge, currency, description,
          net_amount, fee_est, 1 if pass_fees else 0, installment_id or None))
    db.commit()
    db.close()

    return {'url': session_obj.url, 'session_id': session_obj.id, 'charge': charge,
            'net_amount': net_amount, 'fee_estimated': fee_est, 'pass_fees': bool(pass_fees)}


@payments_bp.route('/payments/create-link', methods=['POST'])
def create_link():
    data        = request.get_json(silent=True) or {}
    client_id   = data.get('client_id')
    amount      = data.get('amount')
    description = (data.get('description') or 'Pago — Papia Technology Solutions').strip()
    currency    = (data.get('currency') or 'usd').lower()
    pass_fees   = data.get('pass_fees')
    if pass_fees is not None:
        pass_fees = bool(pass_fees)

    if not client_id or not amount:
        return jsonify({'success': False, 'error': 'client_id y amount son requeridos'}), 400

    try:
        result = create_checkout(_get_org_id(), int(client_id), amount, description, currency,
                                 pass_fees=pass_fees, installment_id=data.get('installment_id'))
        return jsonify({'success': True, **result})
    except PaymentLinkError as exc:
        return jsonify({'success': False, 'error': str(exc)}), exc.status
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


def fetch_stripe_fee(stripe, payment_intent_id):
    """(fee, net) reales del cargo, en dólares, o (None, None) si aún no hay datos."""
    if not payment_intent_id:
        return None, None
    try:
        pi = stripe.PaymentIntent.retrieve(payment_intent_id,
                                           expand=['latest_charge.balance_transaction'])
        charge = pi.get('latest_charge')
        bt = charge.get('balance_transaction') if charge and not isinstance(charge, str) else None
        if not bt or isinstance(bt, str):
            return None, None
        return bt['fee'] / 100, bt['net'] / 100
    except Exception:
        return None, None


def refresh_missing_fees(org_id, limit=20):
    """Completa la comisión real de pagos Stripe que aún no la tienen."""
    from models.payments_ledger import links_missing_fee, save_link_fee
    secret_key, _, _ = _get_org_stripe(org_id)
    if not secret_key:
        return 0
    stripe = _stripe(secret_key)
    updated = 0
    for link in links_missing_fee(org_id, limit):
        pi_id = link.get('payment_intent')
        if not pi_id and link.get('stripe_session_id'):
            try:
                pi_id = stripe.checkout.Session.retrieve(link['stripe_session_id']).get('payment_intent')
            except Exception:
                pi_id = None
        fee, net = fetch_stripe_fee(stripe, pi_id)
        if fee is not None:
            save_link_fee(link['id'], fee, net, pi_id)
            updated += 1
    return updated


# ── Stripe webhook ────────────────────────────────────────────────────────────

@payments_bp.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    payload    = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')

    # Webhook doesn't have session context — try all active orgs
    # Match by webhook secret or fall back to .env
    db   = get_db()
    orgs = db.execute(
        "SELECT id, stripe_secret_key, stripe_webhook_secret FROM organizations WHERE is_active=1"
    ).fetchall()
    db.close()

    secret_key     = os.getenv('STRIPE_SECRET_KEY', '')
    wh_secret      = os.getenv('STRIPE_WEBHOOK_SECRET', '')
    matched_org_id = 1

    for org in orgs:
        org_sk  = (org['stripe_secret_key']     or '').strip()
        org_whs = (org['stripe_webhook_secret'] or '').strip()
        if org_whs and org_whs == sig_header[:len(org_whs)]:
            secret_key     = org_sk or secret_key
            wh_secret      = org_whs
            matched_org_id = org['id']
            break
        if org_sk:
            secret_key     = org_sk
            matched_org_id = org['id']

    if not secret_key:
        return 'Stripe not configured', 503

    try:
        stripe = _stripe(secret_key)
        if wh_secret:
            event = stripe.Webhook.construct_event(payload, sig_header, wh_secret)
        else:
            import json
            event = json.loads(payload)
    except Exception as exc:
        return f'Webhook error: {exc}', 400

    if event['type'] == 'checkout.session.completed':
        s           = event['data']['object']
        session_id  = s.get('id')
        meta        = s.get('metadata') or {}
        client_id   = meta.get('client_id')
        amount_paid = (s.get('amount_total') or 0) / 100
        pi_id       = s.get('payment_intent')

        try:
            from models.payments_ledger import ensure_schema
            from models.payment_schedule import mark_paid
            ensure_schema()
            db   = get_db()
            link = db.execute("SELECT * FROM payment_links WHERE stripe_session_id=?",
                              (session_id,)).fetchone()
            db.close()
            link = dict(link) if link else None

            # Stripe reintenta los webhooks: no contar dos veces el mismo pago.
            if not (link and link['status'] == 'paid'):
                fee, net = fetch_stripe_fee(stripe, pi_id)
                org_id   = (link or {}).get('org_id') or matched_org_id
                # Lo que se abona al saldo del cliente: el precio base (Zelle),
                # no el recargo de tarjeta.
                credited = None
                if link and link.get('pass_fees') and link.get('net_amount'):
                    credited = float(link['net_amount'])
                if credited is None:
                    try:
                        credited = float(meta.get('credit_amount') or 0) or amount_paid
                    except (TypeError, ValueError):
                        credited = amount_paid
                inst_id = (link or {}).get('installment_id') or meta.get('installment_id') or None

                db = get_db()
                db.execute(
                    "UPDATE payment_links SET status='paid', paid_at=CURRENT_TIMESTAMP, "
                    "stripe_fee=?, net_received=?, payment_intent=? WHERE stripe_session_id=?",
                    (fee, net, pi_id, session_id),
                )
                db.commit()
                db.close()

                inst = None
                if client_id and inst_id:
                    # Marca la cuota pagada (suma su monto al pagado y deja nota).
                    inst = mark_paid(int(inst_id), int(client_id), org_id)
                if client_id and not inst:
                    db = get_db()
                    db.execute(
                        "UPDATE clients SET amount_paid = amount_paid + ? WHERE id=?",
                        (credited, int(client_id)),
                    )
                    extra = f' (cobrado con tarjeta: ${amount_paid:,.2f})' if abs(credited - amount_paid) > 0.009 else ''
                    db.execute(
                        "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
                        (int(client_id), f'Pago recibido via Stripe: ${credited:,.2f} '
                                         f'{(s.get("currency") or "usd").upper()}{extra}'),
                    )
                    db.commit()
                    db.close()
        except Exception:
            pass

    return jsonify({'received': True})


# ── Success page ──────────────────────────────────────────────────────────────

@payments_bp.route('/payments/success')
def success():
    session_id = request.args.get('session_id', '')
    db  = get_db()
    row = db.execute("""
        SELECT pl.*, c.first_name, c.last_name, c.id AS client_id
        FROM payment_links pl
        JOIN clients c ON c.id = pl.client_id
        WHERE pl.stripe_session_id = ?
    """, (session_id,)).fetchone()
    db.close()
    return render_template('payments/success.html', payment=dict(row) if row else None)


# ── List links for a client (JSON) ───────────────────────────────────────────

@payments_bp.route('/payments/client/<int:client_id>')
def client_links(client_id):
    db   = get_db()
    rows = db.execute(
        "SELECT * FROM payment_links WHERE client_id=? ORDER BY created_at DESC",
        (client_id,),
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])
