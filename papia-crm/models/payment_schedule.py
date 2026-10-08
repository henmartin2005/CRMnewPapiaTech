"""Cronograma de pagos (cuotas) por cliente + datos para recordatorios vía n8n."""
from datetime import datetime

from database import get_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_schedule (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id           INTEGER NOT NULL DEFAULT 1,
    client_id        INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    label            TEXT    NOT NULL,
    due_date         DATE    NOT NULL,
    amount           REAL    NOT NULL,
    status           TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid')),
    paid_at          TEXT,
    counted_in_paid  INTEGER NOT NULL DEFAULT 0,
    reminder_sent_at TEXT,
    followup_id      INTEGER,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


def _db():
    db = get_db()
    db.execute(_SCHEMA)
    db.execute("CREATE INDEX IF NOT EXISTS idx_pay_sched_client ON payment_schedule(client_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_pay_sched_due ON payment_schedule(due_date, status)")
    return db


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def list_installments(client_id, org_id, today=None):
    today = today or datetime.now().strftime('%Y-%m-%d')
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM payment_schedule WHERE client_id=? AND org_id=? ORDER BY due_date, id",
            (client_id, org_id),
        ).fetchall()
    finally:
        db.close()
    items, running = [], 0.0
    for r in rows:
        d = dict(r)
        running += d['amount']
        d['running_total'] = running
        if d['status'] == 'paid':
            d['state'] = 'paid'
        elif d['due_date'] < today:
            d['state'] = 'overdue'
        elif d['due_date'] == today:
            d['state'] = 'today'
        else:
            d['state'] = 'pending'
        items.append(d)
    return items


def get_installment(inst_id, client_id, org_id):
    db = _db()
    try:
        r = db.execute(
            "SELECT * FROM payment_schedule WHERE id=? AND client_id=? AND org_id=?",
            (inst_id, client_id, org_id),
        ).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def add_installment(client_id, org_id, label, due_date, amount, already_paid=False,
                    create_task=True, client_name=''):
    """Crea la cuota y, si está pendiente, un recordatorio (follow_up) para cobrarla."""
    db = _db()
    try:
        followup_id = None
        if create_task and not already_paid:
            cur = db.execute(
                "INSERT INTO follow_ups (client_id, method, summary, result, next_date, next_at, "
                "reminder_comment, org_id) VALUES (?, 'phone', ?, '', ?, ?, ?, ?)",
                (
                    client_id,
                    f"Cobrar {label} — ${amount:,.2f}",
                    due_date,
                    f"{due_date} 09:00:00",
                    f"Cuota del cronograma de pagos{(' de ' + client_name) if client_name else ''}. "
                    "Confirmar recepción por Zelle y marcarla como pagada en el CRM.",
                    org_id,
                ),
            )
            followup_id = cur.lastrowid
        db.execute(
            "INSERT INTO payment_schedule (org_id, client_id, label, due_date, amount, status, "
            "paid_at, followup_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (org_id, client_id, label, due_date, amount,
             'paid' if already_paid else 'pending',
             _now() if already_paid else None, followup_id),
        )
        db.commit()
    finally:
        db.close()


def mark_paid(inst_id, client_id, org_id):
    inst = get_installment(inst_id, client_id, org_id)
    if not inst or inst['status'] == 'paid':
        return None
    db = _db()
    try:
        db.execute(
            "UPDATE payment_schedule SET status='paid', paid_at=?, counted_in_paid=1 WHERE id=?",
            (_now(), inst_id),
        )
        db.execute(
            "UPDATE clients SET amount_paid = amount_paid + ? WHERE id=? AND org_id=?",
            (inst['amount'], client_id, org_id),
        )
        if inst.get('followup_id'):
            db.execute("UPDATE follow_ups SET completed=1 WHERE id=?", (inst['followup_id'],))
        db.execute(
            "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'note', ?)",
            (client_id, f"Pago recibido: {inst['label']} — ${inst['amount']:,.2f} (cronograma)"),
        )
        db.commit()
    finally:
        db.close()
    return inst


def mark_unpaid(inst_id, client_id, org_id):
    inst = get_installment(inst_id, client_id, org_id)
    if not inst or inst['status'] != 'paid':
        return None
    db = _db()
    try:
        db.execute(
            "UPDATE payment_schedule SET status='pending', paid_at=NULL, counted_in_paid=0 WHERE id=?",
            (inst_id,),
        )
        if inst.get('counted_in_paid'):
            db.execute(
                "UPDATE clients SET amount_paid = MAX(amount_paid - ?, 0) WHERE id=? AND org_id=?",
                (inst['amount'], client_id, org_id),
            )
        if inst.get('followup_id'):
            db.execute("UPDATE follow_ups SET completed=0 WHERE id=?", (inst['followup_id'],))
        db.commit()
    finally:
        db.close()
    return inst


def delete_installment(inst_id, client_id, org_id):
    inst = get_installment(inst_id, client_id, org_id)
    if not inst:
        return None
    db = _db()
    try:
        if inst.get('followup_id'):
            db.execute("DELETE FROM follow_ups WHERE id=?", (inst['followup_id'],))
        db.execute("DELETE FROM payment_schedule WHERE id=?", (inst_id,))
        db.commit()
    finally:
        db.close()
    return inst


# ── Para n8n ─────────────────────────────────────────────────────────────────

def due_for_reminder(target_date, include_sent=False):
    """Cuotas pendientes que vencen en target_date (YYYY-MM-DD), con datos del cliente."""
    db = _db()
    try:
        sql = (
            "SELECT p.*, c.first_name, c.last_name, c.email, c.company, c.total_cost, c.amount_paid "
            "FROM payment_schedule p JOIN clients c ON c.id = p.client_id "
            "WHERE p.status='pending' AND p.due_date=?"
        )
        if not include_sent:
            sql += " AND p.reminder_sent_at IS NULL"
        rows = db.execute(sql + " ORDER BY p.id", (target_date,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def installment_with_client(inst_id, client_id, org_id):
    """Una cuota con los datos del cliente necesarios para el recordatorio."""
    db = _db()
    try:
        r = db.execute(
            "SELECT p.*, c.first_name, c.last_name, c.email, c.company, c.total_cost, c.amount_paid "
            "FROM payment_schedule p JOIN clients c ON c.id = p.client_id "
            "WHERE p.id=? AND p.client_id=? AND p.org_id=?",
            (inst_id, client_id, org_id),
        ).fetchone()
        return dict(r) if r else None
    finally:
        db.close()


def mark_reminder_sent(inst_id):
    db = _db()
    try:
        r = db.execute("SELECT * FROM payment_schedule WHERE id=?", (inst_id,)).fetchone()
        if not r:
            return None
        db.execute("UPDATE payment_schedule SET reminder_sent_at=? WHERE id=?", (_now(), inst_id))
        db.execute(
            "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'email', ?)",
            (r['client_id'],
             f"Recordatorio de pago enviado por email: {r['label']} — ${r['amount']:,.2f} "
             f"vence {r['due_date']}"),
        )
        db.commit()
        return dict(r)
    finally:
        db.close()
