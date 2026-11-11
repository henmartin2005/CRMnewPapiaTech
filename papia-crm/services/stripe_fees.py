"""Cálculo de comisiones de Stripe y precio con tarjeta (precio dual).

Modelo de precio dual: el precio con tarjeta incluye la comisión de Stripe y el
pago directo (Zelle/efectivo) es el precio con descuento. Así el negocio recibe
siempre el mismo neto sin importar cómo pague el cliente.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from database import get_db

DEFAULT_PCT = 2.9      # % por pago con tarjeta (Stripe estándar EE. UU.)
DEFAULT_FIXED = 0.30   # cargo fijo por pago
CENT = Decimal('0.01')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_settings (
    org_id      INTEGER PRIMARY KEY,
    fee_pct     REAL    NOT NULL DEFAULT 2.9,
    fee_fixed   REAL    NOT NULL DEFAULT 0.30,
    pass_fees   INTEGER NOT NULL DEFAULT 1,
    zelle_email TEXT    NOT NULL DEFAULT '',
    updated_at  TEXT    DEFAULT (datetime('now', 'localtime'))
)
"""


def _d(value):
    return Decimal(str(value or 0))


def fee_for(gross, pct=DEFAULT_PCT, fixed=DEFAULT_FIXED):
    """Comisión que Stripe cobra sobre un cargo bruto (redondeo al centavo)."""
    gross = _d(gross)
    if gross <= 0:
        return 0.0
    fee = (gross * _d(pct) / 100).quantize(CENT, rounding=ROUND_HALF_UP) + _d(fixed)
    return float(fee.quantize(CENT))


def card_amount(net, pct=DEFAULT_PCT, fixed=DEFAULT_FIXED):
    """Monto a cobrar con tarjeta para recibir `net` limpio después de la comisión."""
    net = _d(net)
    if net <= 0:
        return 0.0
    rate = _d(pct) / 100
    gross = ((net + _d(fixed)) / (1 - rate)).quantize(CENT, rounding=ROUND_CEILING)
    # Ajuste por redondeo: nunca quedar por debajo del neto deseado.
    while gross - _d(fee_for(gross, pct, fixed)) < net:
        gross += CENT
    # Y no pasarse si un centavo menos también alcanza.
    while gross - CENT > 0 and (gross - CENT) - _d(fee_for(gross - CENT, pct, fixed)) >= net:
        gross -= CENT
    return float(gross)


def quote(net, pct=DEFAULT_PCT, fixed=DEFAULT_FIXED):
    """Desglose para un neto deseado: precio Zelle, precio tarjeta, comisión."""
    net = float(_d(net).quantize(CENT))
    card = card_amount(net, pct, fixed)
    fee = fee_for(card, pct, fixed)
    return {
        'net': net,
        'zelle': net,
        'card': card,
        'fee': fee,
        'receive': round(card - fee, 2),
        'extra': round(card - net, 2),
        'extra_pct': round((card - net) / net * 100, 2) if net else 0.0,
    }


def _db():
    db = get_db()
    db.execute(_SCHEMA)
    return db


def get_settings(org_id):
    db = _db()
    try:
        row = db.execute("SELECT * FROM payment_settings WHERE org_id=?", (org_id,)).fetchone()
    finally:
        db.close()
    if row:
        s = dict(row)
    else:
        s = {'org_id': org_id, 'fee_pct': DEFAULT_PCT, 'fee_fixed': DEFAULT_FIXED,
             'pass_fees': 1, 'zelle_email': ''}
    s['pass_fees'] = bool(s.get('pass_fees'))
    return s


def save_settings(org_id, fee_pct, fee_fixed, pass_fees, zelle_email=''):
    fee_pct = max(0.0, min(float(fee_pct), 15.0))
    fee_fixed = max(0.0, min(float(fee_fixed), 5.0))
    db = _db()
    try:
        db.execute(
            "INSERT INTO payment_settings (org_id, fee_pct, fee_fixed, pass_fees, zelle_email, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now','localtime')) "
            "ON CONFLICT(org_id) DO UPDATE SET fee_pct=excluded.fee_pct, fee_fixed=excluded.fee_fixed, "
            "pass_fees=excluded.pass_fees, zelle_email=excluded.zelle_email, updated_at=excluded.updated_at",
            (org_id, fee_pct, fee_fixed, 1 if pass_fees else 0, (zelle_email or '').strip()[:120]),
        )
        db.commit()
    finally:
        db.close()
