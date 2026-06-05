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

@payments_bp.route('/payments/create-link', methods=['POST'])
def create_link():
    data        = request.get_json(silent=True) or {}
    client_id   = data.get('client_id')
    amount      = data.get('amount')
    description = (data.get('description') or 'Pago — Papia Technology Solutions').strip()
    currency    = (data.get('currency') or 'usd').lower()

    if not client_id or not amount:
        return jsonify({'success': False, 'error': 'client_id y amount son requeridos'}), 400

    try:
        amount = float(amount)
        if amount <= 0:
            return jsonify({'success': False, 'error': 'El monto debe ser mayor a 0'}), 400
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Monto inválido'}), 400

    org_id = _get_org_id()
    secret_key, _, base_url = _get_org_stripe(org_id)

    if not secret_key:
        return jsonify({'success': False, 'error': 'Configura tu Stripe Secret Key en Configuración → Pagos'}), 503

    try:
        stripe     = _stripe(secret_key)
        db         = get_db()
        client     = db.execute("SELECT first_name, last_name, email FROM clients WHERE id=?",
                                (client_id,)).fetchone()
        db.close()

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': currency,
                    'product_data': {
                        'name': description,
                        'description': 'Papia Technology Solutions LLC',
                    },
                    'unit_amount': int(round(amount * 100)),
                },
                'quantity': 1,
            }],
            mode='payment',
            customer_email=client['email'] if client and client['email'] else None,
            success_url=f'{base_url}/payments/success?session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url=f'{base_url}/clients/{client_id}',
            metadata={'client_id': str(client_id), 'crm': 'papia'},
        )

        db = get_db()
        db.execute("""
            INSERT INTO payment_links
                (client_id, org_id, stripe_session_id, stripe_link_url, amount, currency, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (client_id, org_id, session.id, session.url, amount, currency, description))
        db.commit()
        db.close()

        return jsonify({'success': True, 'url': session.url, 'session_id': session.id})

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


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
        client_id   = s.get('metadata', {}).get('client_id')
        amount_paid = s.get('amount_total', 0) / 100

        try:
            db = get_db()
            db.execute(
                "UPDATE payment_links SET status='paid', paid_at=CURRENT_TIMESTAMP WHERE stripe_session_id=?",
                (session_id,),
            )
            if client_id:
                db.execute(
                    "UPDATE clients SET amount_paid = amount_paid + ? WHERE id=?",
                    (amount_paid, int(client_id)),
                )
                db.execute(
                    "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
                    (int(client_id), f'Pago recibido via Stripe: ${amount_paid:,.2f} {s.get("currency","usd").upper()}'),
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
