from datetime import datetime

from database import get_db

PIPELINE_STAGES = [
    ('new_lead', 'Nuevo Lead'),
    ('contacted', 'Contactado'),
    ('proposal_sent', 'Propuesta Enviada'),
    ('negotiation', 'Negociación'),
    ('active_client', 'Cliente Activo'),
    ('recurring', 'Recurrente'),
]

PROJECT_TYPES = [
    ('website', 'Website'),
    ('crm', 'CRM'),
    ('mobile_app', 'Mobile App'),
    ('consulting', 'Consulting'),
    ('other', 'Other'),
]

NOTE_TYPES = [
    ('note', 'Nota'),
    ('call', 'Llamada'),
    ('meeting', 'Reunión'),
    ('email', 'Email'),
    ('whatsapp', 'WhatsApp'),
    ('website_lead', 'Lead Web'),
]

FOLLOW_UP_METHODS = [
    ('phone', 'Teléfono'),
    ('email', 'Email'),
    ('whatsapp', 'WhatsApp'),
    ('meeting', 'Reunión'),
    ('other', 'Otro'),
]

STAGE_COLORS = {
    'new_lead': 'info',
    'contacted': 'primary',
    'proposal_sent': 'warning',
    'negotiation': 'orange',
    'active_client': 'success',
    'recurring': 'purple',
}


def get_all_clients(search=None):
    db = get_db()
    if search:
        q = f"%{search}%"
        rows = db.execute("""
            SELECT *, (total_cost - amount_paid) AS pending
            FROM clients
            WHERE first_name LIKE ? OR last_name LIKE ? OR email LIKE ?
               OR company LIKE ? OR project_type LIKE ?
            ORDER BY created_at DESC
        """, (q, q, q, q, q)).fetchall()
    else:
        rows = db.execute("""
            SELECT *, (total_cost - amount_paid) AS pending
            FROM clients
            ORDER BY created_at DESC
        """).fetchall()
    db.close()
    return rows


def get_client(client_id):
    db = get_db()
    row = db.execute("""
        SELECT *, (total_cost - amount_paid) AS pending
        FROM clients WHERE id = ?
    """, (client_id,)).fetchone()
    db.close()
    return row


def create_client(data):
    db = get_db()
    cursor = db.execute("""
        INSERT INTO clients
            (first_name, last_name, email, phone, company, project_type,
             project_details, pipeline_stage, total_cost, amount_paid)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data['first_name'], data['last_name'], data['email'],
        data.get('phone', ''), data.get('company', ''),
        data['project_type'], data.get('project_details', ''),
        data.get('pipeline_stage', 'new_lead'),
        float(data.get('total_cost', 0)),
        float(data.get('amount_paid', 0)),
    ))
    db.commit()
    new_id = cursor.lastrowid
    db.close()
    return new_id


def update_client(client_id, data):
    db = get_db()
    db.execute("""
        UPDATE clients SET
            first_name=?, last_name=?, email=?, phone=?, company=?,
            project_type=?, project_details=?, pipeline_stage=?,
            total_cost=?, amount_paid=?, brochure_sent=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (
        data['first_name'], data['last_name'], data['email'],
        data.get('phone', ''), data.get('company', ''),
        data['project_type'], data.get('project_details', ''),
        data.get('pipeline_stage', 'new_lead'),
        float(data.get('total_cost', 0)),
        float(data.get('amount_paid', 0)),
        1 if data.get('brochure_sent') else 0,
        client_id,
    ))
    db.commit()
    db.close()


def update_pipeline_stage(client_id, stage):
    db = get_db()
    db.execute("""
        UPDATE clients SET pipeline_stage=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, (stage, client_id))
    db.commit()
    db.close()


def delete_client(client_id):
    db = get_db()
    db.execute("DELETE FROM clients WHERE id=?", (client_id,))
    db.commit()
    db.close()


def get_client_notes(client_id):
    db = get_db()
    rows = db.execute("""
        SELECT * FROM notes WHERE client_id=? ORDER BY created_at DESC
    """, (client_id,)).fetchall()
    db.close()
    return rows


def add_note(client_id, note_type, content):
    db = get_db()
    db.execute("""
        INSERT INTO notes (client_id, note_type, content) VALUES (?, ?, ?)
    """, (client_id, note_type, content))
    db.commit()
    db.close()


def _now_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _normalize_reminder_at(value):
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M').strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        pass
    try:
        return datetime.strptime(value, '%Y-%m-%d %H:%M').strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return value


def get_client_followups(client_id):
    db = get_db()
    rows = db.execute("""
        SELECT * FROM follow_ups
        WHERE client_id=?
        ORDER BY COALESCE(next_at, next_date, created_at) DESC
    """, (client_id,)).fetchall()
    db.close()
    return rows


def add_followup(client_id, method, summary, result, next_date=None, next_at=None, reminder_comment=''):
    normalized_next_at = _normalize_reminder_at(next_at)
    reminder_date = next_date
    if normalized_next_at:
        reminder_date = normalized_next_at[:10]

    db = get_db()
    db.execute("""
        INSERT INTO follow_ups
            (client_id, method, summary, result, next_date, next_at, reminder_comment)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        client_id,
        method,
        summary,
        result,
        reminder_date or None,
        normalized_next_at,
        reminder_comment or '',
    ))
    db.commit()
    db.close()


def get_todays_followups():
    db = get_db()
    rows = db.execute("""
        SELECT f.*, c.first_name, c.last_name, c.email, c.phone
        FROM follow_ups f
        JOIN clients c ON c.id = f.client_id
        WHERE COALESCE(date(f.next_at), f.next_date) = date('now', 'localtime')
          AND f.completed = 0
        ORDER BY COALESCE(f.next_at, f.next_date, f.created_at)
    """).fetchall()
    db.close()
    return rows


def get_due_tasks():
    db = get_db()
    rows = db.execute("""
        SELECT f.*, c.first_name, c.last_name, c.email, c.phone
        FROM follow_ups f
        JOIN clients c ON c.id = f.client_id
        WHERE f.completed = 0
          AND (
            (f.next_at IS NOT NULL AND f.next_at <= ?)
            OR (f.next_at IS NULL AND f.next_date IS NOT NULL AND f.next_date <= date('now', 'localtime'))
          )
        ORDER BY COALESCE(f.next_at, f.next_date, f.created_at)
    """, (_now_str(),)).fetchall()
    db.close()
    return rows


def get_due_task_count():
    db = get_db()
    row = db.execute("""
        SELECT COUNT(*)
        FROM follow_ups
        WHERE completed = 0
          AND (
            (next_at IS NOT NULL AND next_at <= ?)
            OR (next_at IS NULL AND next_date IS NOT NULL AND next_date <= date('now', 'localtime'))
          )
    """, (_now_str(),)).fetchone()
    db.close()
    return row[0]


def get_all_tasks():
    db = get_db()
    rows = db.execute("""
        SELECT f.*, c.first_name, c.last_name, c.email, c.phone
        FROM follow_ups f
        JOIN clients c ON c.id = f.client_id
        WHERE f.next_at IS NOT NULL OR f.next_date IS NOT NULL
        ORDER BY f.completed ASC, COALESCE(f.next_at, f.next_date, f.created_at) ASC
    """).fetchall()
    db.close()
    return rows


def complete_followup(followup_id):
    db = get_db()
    db.execute("UPDATE follow_ups SET completed=1 WHERE id=?", (followup_id,))
    db.commit()
    db.close()


def get_dashboard_stats():
    db = get_db()

    total_leads = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]

    active_clients = db.execute(
        "SELECT COUNT(*) FROM clients WHERE pipeline_stage IN ('active_client','recurring')"
    ).fetchone()[0]

    pending_proposals = db.execute(
        "SELECT COUNT(*) FROM clients WHERE pipeline_stage = 'proposal_sent'"
    ).fetchone()[0]

    financials = db.execute("""
        SELECT
            COALESCE(SUM(total_cost), 0) AS total_billed,
            COALESCE(SUM(amount_paid), 0) AS total_collected,
            COALESCE(SUM(total_cost - amount_paid), 0) AS total_pending
        FROM clients
    """).fetchone()

    db.close()
    return {
        'total_leads': total_leads,
        'active_clients': active_clients,
        'pending_proposals': pending_proposals,
        'total_billed': financials['total_billed'],
        'total_collected': financials['total_collected'],
        'total_pending': financials['total_pending'],
    }


def get_clients_by_stage():
    db = get_db()
    rows = db.execute("""
        SELECT *, (total_cost - amount_paid) AS pending
        FROM clients ORDER BY created_at DESC
    """).fetchall()
    db.close()

    stages = {stage: [] for stage, _ in PIPELINE_STAGES}
    for row in rows:
        stage = row['pipeline_stage']
        if stage in stages:
            stages[stage].append(row)
    return stages
