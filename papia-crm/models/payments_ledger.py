"""Registro unificado de pagos: Stripe (payment_links), pagos manuales
(Zelle/efectivo/transferencia) y cuotas marcadas como pagadas en el cronograma."""
from datetime import datetime

from database import get_db
from models.payment_schedule import _SCHEMA as _SCHEDULE_SCHEMA, mark_paid, mark_unpaid

METHODS = [
    ('zelle', 'Zelle'),
    ('stripe', 'Stripe (tarjeta)'),
    ('cash', 'Efectivo'),
    ('transfer', 'Transferencia'),
    ('other', 'Otro'),
]
METHOD_LABELS = dict(METHODS)
METHOD_LABELS['schedule'] = 'Cronograma'

_RECORDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_records (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id         INTEGER NOT NULL DEFAULT 1,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    installment_id INTEGER,
    amount         REAL    NOT NULL,
    method         TEXT    NOT NULL DEFAULT 'zelle',
    paid_on        DATE    NOT NULL,
    note           TEXT    DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""

_LINK_COLUMNS = [
    ("net_amount", "REAL"),          # lo que el negocio quiere recibir (precio Zelle)
    ("fee_estimated", "REAL"),       # comisión estimada al crear el link
    ("stripe_fee", "REAL"),          # comisión real (balance transaction)
    ("net_received", "REAL"),        # neto real depositado por Stripe
    ("pass_fees", "INTEGER DEFAULT 0"),
    ("installment_id", "INTEGER"),
    ("payment_intent", "TEXT"),
]


def ensure_schema():
    db = get_db()
    try:
        db.execute(_SCHEDULE_SCHEMA)
        db.execute(_RECORDS_SCHEMA)
        db.execute("CREATE INDEX IF NOT EXISTS idx_pay_records_org ON payment_records(org_id, paid_on)")
        cols = {r['name'] for r in db.execute("PRAGMA table_info(payment_links)").fetchall()}
        if 'org_id' not in cols:
            db.execute("ALTER TABLE payment_links ADD COLUMN org_id INTEGER NOT NULL DEFAULT 1")
        for name, ddl in _LINK_COLUMNS:
            if name not in cols:
                db.execute(f"ALTER TABLE payment_links ADD COLUMN {name} {ddl}")
        db.commit()
    finally:
        db.close()


def _db():
    ensure_schema()
    return get_db()


def _today():
    return datetime.now().strftime('%Y-%m-%d')


# ── Pagos manuales ───────────────────────────────────────────────────────────

def record_manual(org_id, client_id, amount, method, paid_on, note='', installment_id=None):
    """Registra un pago directo. Si está ligado a una cuota, la marca pagada
    (eso ya suma al monto pagado del cliente); si no, suma aquí."""
    amount = round(float(amount), 2)
    if installment_id:
        inst = mark_paid(int(installment_id), int(client_id), org_id)
        if not inst:
            return None
        amount = float(inst['amount'])
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO payment_records (org_id, client_id, installment_id, amount, method, paid_on, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (org_id, client_id, installment_id or None, amount, method, paid_on, (note or '')[:300]),
        )
        if not installment_id:
            db.execute("UPDATE clients SET amount_paid = amount_paid + ? WHERE id=? AND org_id=?",
                       (amount, client_id, org_id))
            db.execute("INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
                       (client_id, f"Pago recibido por {METHOD_LABELS.get(method, method)}: ${amount:,.2f}"
                                   + (f" — {note}" if note else '')))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def delete_manual(org_id, record_id):
    db = _db()
    try:
        r = db.execute("SELECT * FROM payment_records WHERE id=? AND org_id=?", (record_id, org_id)).fetchone()
        if not r:
            return None
        r = dict(r)
        db.execute("DELETE FROM payment_records WHERE id=?", (record_id,))
        if not r['installment_id']:
            db.execute("UPDATE clients SET amount_paid = MAX(amount_paid - ?, 0) WHERE id=? AND org_id=?",
                       (r['amount'], r['client_id'], org_id))
        db.commit()
    finally:
        db.close()
    if r['installment_id']:
        mark_unpaid(r['installment_id'], r['client_id'], org_id)
    return r


# ── Consultas ────────────────────────────────────────────────────────────────

def history(org_id, month=None, method=None, client_id=None):
    """Todos los pagos recibidos, normalizados y ordenados por fecha desc."""
    db = _db()
    try:
        rows = []
        for r in db.execute(
            "SELECT pl.*, c.first_name, c.last_name, c.company FROM payment_links pl "
            "JOIN clients c ON c.id = pl.client_id "
            "WHERE pl.org_id=? AND pl.status='paid'", (org_id,)
        ).fetchall():
            r = dict(r)
            gross = float(r['amount'] or 0)
            fee = r['stripe_fee'] if r['stripe_fee'] is not None else r['fee_estimated']
            net = r['net_received'] if r['net_received'] is not None else (
                gross - fee if fee is not None else None)
            rows.append({
                'source': 'stripe', 'id': r['id'], 'client_id': r['client_id'],
                'client': f"{r['first_name']} {r['last_name'] or ''}".strip(), 'company': r['company'],
                'date': (r['paid_at'] or r['created_at'] or '')[:10], 'concept': r['description'] or 'Pago Stripe',
                'method': 'stripe', 'gross': gross, 'fee': fee, 'net': net,
                'fee_real': r['stripe_fee'] is not None, 'installment_id': r['installment_id'],
            })
        covered = {r['installment_id'] for r in rows if r['installment_id']}
        for r in db.execute(
            "SELECT pr.*, c.first_name, c.last_name, c.company, ps.label FROM payment_records pr "
            "JOIN clients c ON c.id = pr.client_id "
            "LEFT JOIN payment_schedule ps ON ps.id = pr.installment_id "
            "WHERE pr.org_id=?", (org_id,)
        ).fetchall():
            r = dict(r)
            if r['installment_id']:
                covered.add(r['installment_id'])
            rows.append({
                'source': 'manual', 'id': r['id'], 'client_id': r['client_id'],
                'client': f"{r['first_name']} {r['last_name'] or ''}".strip(), 'company': r['company'],
                'date': r['paid_on'], 'concept': r['label'] or r['note'] or 'Pago directo',
                'method': r['method'], 'gross': float(r['amount']), 'fee': 0.0, 'net': float(r['amount']),
                'fee_real': True, 'installment_id': r['installment_id'], 'note': r['note'],
            })
        for r in db.execute(
            "SELECT ps.*, c.first_name, c.last_name, c.company FROM payment_schedule ps "
            "JOIN clients c ON c.id = ps.client_id WHERE ps.org_id=? AND ps.status='paid'", (org_id,)
        ).fetchall():
            r = dict(r)
            if r['id'] in covered:
                continue
            rows.append({
                'source': 'schedule', 'id': r['id'], 'client_id': r['client_id'],
                'client': f"{r['first_name']} {r['last_name'] or ''}".strip(), 'company': r['company'],
                'date': (r['paid_at'] or r['due_date'] or '')[:10], 'concept': r['label'],
                'method': 'schedule', 'gross': float(r['amount']), 'fee': 0.0, 'net': float(r['amount']),
                'fee_real': True, 'installment_id': r['id'],
            })
    finally:
        db.close()
    if month:
        rows = [r for r in rows if (r['date'] or '').startswith(month)]
    if method:
        rows = [r for r in rows if r['method'] == method]
    if client_id:
        rows = [r for r in rows if r['client_id'] == int(client_id)]
    rows.sort(key=lambda r: (r['date'] or '', r['id']), reverse=True)
    return rows


def pending_installments(org_id):
    today = _today()
    db = _db()
    try:
        rows = db.execute(
            "SELECT ps.*, c.first_name, c.last_name, c.company, c.email, c.phone FROM payment_schedule ps "
            "JOIN clients c ON c.id = ps.client_id "
            "WHERE ps.org_id=? AND ps.status='pending' ORDER BY ps.due_date, ps.id", (org_id,)
        ).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d['client'] = f"{d['first_name']} {d['last_name'] or ''}".strip()
        d['state'] = 'overdue' if d['due_date'] < today else ('today' if d['due_date'] == today else 'pending')
        out.append(d)
    return out


def client_balances(org_id):
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, first_name, last_name, company, total_cost, amount_paid, pipeline_stage FROM clients "
            "WHERE org_id=? AND (total_cost > 0 OR amount_paid > 0) "
            "ORDER BY (total_cost - amount_paid) DESC, last_name", (org_id,)
        ).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d['client'] = f"{d['first_name']} {d['last_name'] or ''}".strip()
        d['pending'] = max(float(d['total_cost'] or 0) - float(d['amount_paid'] or 0), 0.0)
        d['pct'] = min(round(float(d['amount_paid'] or 0) / float(d['total_cost']) * 100, 1), 100) \
            if d['total_cost'] else 100
        out.append(d)
    return out


def kpis(org_id, month):
    today = _today()
    paid = history(org_id, month=month)
    pend = pending_installments(org_id)
    balances = client_balances(org_id)
    return {
        'received_net': round(sum(r['net'] or 0 for r in paid), 2),
        'received_gross': round(sum(r['gross'] for r in paid), 2),
        'fees': round(sum(r['fee'] or 0 for r in paid), 2),
        'count': len(paid),
        'stripe_count': sum(1 for r in paid if r['method'] == 'stripe'),
        'pending_total': round(sum(p['amount'] for p in pend), 2),
        'overdue_total': round(sum(p['amount'] for p in pend if p['due_date'] < today), 2),
        'overdue_count': sum(1 for p in pend if p['due_date'] < today),
        'due_month': round(sum(p['amount'] for p in pend if p['due_date'].startswith(month)), 2),
        'contracts_pending': round(sum(b['pending'] for b in balances), 2),
    }


def links_missing_fee(org_id, limit=20):
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, stripe_session_id, payment_intent FROM payment_links "
            "WHERE org_id=? AND status='paid' AND stripe_fee IS NULL ORDER BY id DESC LIMIT ?",
            (org_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def save_link_fee(link_id, fee, net, payment_intent=None):
    db = _db()
    try:
        db.execute(
            "UPDATE payment_links SET stripe_fee=?, net_received=?, "
            "payment_intent=COALESCE(?, payment_intent) WHERE id=?",
            (fee, net, payment_intent, link_id),
        )
        db.commit()
    finally:
        db.close()
