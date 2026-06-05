import os
import time
from flask import Blueprint, request, jsonify, render_template
from database import get_db

payments_bp = Blueprint('payments', __name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _stripe():
    import stripe
    stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
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

    if not os.getenv('STRIPE_SECRET_KEY'):
        return jsonify({'success': False, 'error': 'STRIPE_SECRET_KEY no configurada en .env'}), 503

    try:
        stripe     = _stripe()
        db         = get_db()
        client     = db.execute("SELECT first_name, last_name, email FROM clients WHERE id=?",
                                (client_id,)).fetchone()
        db.close()

        base_url = os.getenv('APP_BASE_URL', 'https://datos.papiatech.com')

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
            expires_at=int(time.time()) + 30 * 24 * 3600,   # 30 días
            metadata={'client_id': str(client_id), 'crm': 'papia'},
        )

        db = get_db()
        db.execute("""
            INSERT INTO payment_links
                (client_id, stripe_session_id, stripe_link_url, amount, currency, description)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (client_id, session.id, session.url, amount, currency, description))
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
    wh_secret  = os.getenv('STRIPE_WEBHOOK_SECRET')

    if not os.getenv('STRIPE_SECRET_KEY'):
        return 'Stripe not configured', 503

    try:
        stripe = _stripe()
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
