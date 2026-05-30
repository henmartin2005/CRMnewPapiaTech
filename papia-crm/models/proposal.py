import json

from database import get_db
from models.proposal_template import (
    DEFAULT_TERMS_BY_LANGUAGE,
    PROPOSAL_LANGUAGES,
    PROPOSAL_PURPOSES,
    PROJECT_TYPES,
    SERVICE_CATALOG_BY_LANGUAGE,
    TEMPLATE_PRESETS,
    get_service,
    get_service_catalog,
    get_template,
    get_templates_for_ui,
)
from models.proposal_whatsapp import create_acceptance_link


PROPOSAL_STATUSES = [
    ('draft', 'Draft'),
    ('ready_to_send', 'Ready to Send'),
    ('sent', 'Sent'),
    ('approved', 'Approved'),
    ('rejected', 'Rejected'),
    ('paid', 'Paid'),
]

DEFAULT_TERMS = DEFAULT_TERMS_BY_LANGUAGE['en']
SERVICE_CATALOG = SERVICE_CATALOG_BY_LANGUAGE['en']


def _json_load(value, fallback):
    if not value:
        return list(fallback)
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return list(fallback)


def _json_dump(value):
    return json.dumps(value or [], ensure_ascii=False)


def get_template_payload(template_key='landing_page', language='en'):
    if ':' in template_key:
        language, template_key = template_key.split(':', 1)
    return get_template(language, template_key)


def serialize_proposal(row):
    if not row:
        return None
    data = dict(row)
    data.setdefault('proposal_language', 'en')
    data.setdefault('proposal_purpose', data.get('project_type') or 'landing_page')
    data['selected_services'] = _json_load(data.get('selected_services'), [])
    data['milestones'] = _json_load(data.get('milestones'), [])
    data['terms_and_conditions'] = _json_load(
        data.get('terms_and_conditions'),
        DEFAULT_TERMS_BY_LANGUAGE.get(data.get('proposal_language', 'en'), DEFAULT_TERMS),
    )
    data['pdf_url'] = data.get('pdf_url') or f"/proposals/{data['id']}/pdf"
    data['whatsapp_acceptance_link'] = data.get('whatsapp_acceptance_link') or create_acceptance_link(data)
    data.setdefault('client_approval_status', 'pending')
    return data


def list_proposals(search=None, status=None):
    db = get_db()
    sql = "SELECT * FROM proposals WHERE 1=1"
    params = []
    if search:
        q = f"%{search}%"
        sql += " AND (client_name LIKE ? OR business_name LIKE ? OR email LIKE ? OR phone LIKE ? OR project_type LIKE ? OR status LIKE ?)"
        params.extend([q, q, q, q, q, q])
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY updated_at DESC, created_at DESC"
    rows = db.execute(sql, params).fetchall()
    db.close()
    return [serialize_proposal(row) for row in rows]


def get_proposal(proposal_id):
    db = get_db()
    row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    db.close()
    return serialize_proposal(row)


def _proposal_values(data):
    selected_services = data.get('selected_services', [])
    subtotal = sum(float(s.get('price') or 0) for s in selected_services)
    discount = float(data.get('discount') or 0)
    taxes = float(data.get('taxes') or 0)
    total = max(0, subtotal - discount + taxes)
    initial_payment = float(data.get('initial_payment') or 0)
    remaining_balance = max(0, total - initial_payment)
    return (
        data.get('client_id') or None,
        data.get('client_name', '').strip(),
        data.get('business_name', '').strip(),
        data.get('email', '').strip(),
        data.get('phone', '').strip(),
        data.get('address', '').strip(),
        data.get('title', '').strip(),
        data.get('project_type', 'other'),
        data.get('proposal_language', 'en'),
        data.get('proposal_purpose', data.get('project_type', 'other')),
        data.get('project_description', '').strip(),
        data.get('client_needs', '').strip(),
        data.get('project_objective', '').strip(),
        _json_dump(selected_services),
        subtotal,
        discount,
        taxes,
        total,
        initial_payment,
        remaining_balance,
        data.get('payment_terms', '').strip(),
        data.get('payment_schedule', '').strip(),
        data.get('payment_methods', '').strip(),
        data.get('start_date') or None,
        data.get('estimated_delivery_date') or None,
        int(data.get('business_days') or 0),
        _json_dump(data.get('milestones', [])),
        _json_dump(data.get('terms_and_conditions', DEFAULT_TERMS_BY_LANGUAGE.get(data.get('proposal_language', 'en'), DEFAULT_TERMS))),
        data.get('status', 'draft'),
        data.get('pdf_url') or None,
        data.get('email_draft_id') or None,
        data.get('sent_at') or None,
        data.get('accepted_via_whatsapp_at') or None,
        data.get('whatsapp_acceptance_link') or create_acceptance_link(data),
        data.get('client_approval_status', 'pending'),
    )


def create_proposal(data):
    db = get_db()
    cursor = db.execute("""
        INSERT INTO proposals (
            client_id, client_name, business_name, email, phone, address, title,
            project_type, proposal_language, proposal_purpose, project_description, client_needs, project_objective,
            selected_services, subtotal, discount, taxes, total, initial_payment,
            remaining_balance, payment_terms, payment_schedule, payment_methods,
            start_date, estimated_delivery_date, business_days, milestones,
            terms_and_conditions, status, pdf_url, email_draft_id, sent_at,
            accepted_via_whatsapp_at, whatsapp_acceptance_link, client_approval_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, _proposal_values(data))
    db.commit()
    proposal_id = cursor.lastrowid
    db.close()
    return proposal_id


def update_proposal(proposal_id, data):
    db = get_db()
    db.execute("""
        UPDATE proposals SET
            client_id=?, client_name=?, business_name=?, email=?, phone=?, address=?,
            title=?, project_type=?, proposal_language=?, proposal_purpose=?, project_description=?, client_needs=?,
            project_objective=?, selected_services=?, subtotal=?, discount=?,
            taxes=?, total=?, initial_payment=?, remaining_balance=?,
            payment_terms=?, payment_schedule=?, payment_methods=?, start_date=?,
            estimated_delivery_date=?, business_days=?, milestones=?,
            terms_and_conditions=?, status=?, pdf_url=?, email_draft_id=?, sent_at=?,
            accepted_via_whatsapp_at=?, whatsapp_acceptance_link=?,
            client_approval_status=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    """, _proposal_values(data) + (proposal_id,))
    db.commit()
    db.close()


def delete_proposal(proposal_id):
    db = get_db()
    db.execute("DELETE FROM proposals WHERE id=?", (proposal_id,))
    db.commit()
    db.close()


def update_status(proposal_id, status):
    valid = [s for s, _ in PROPOSAL_STATUSES]
    if status not in valid:
        return False
    db = get_db()
    db.execute("UPDATE proposals SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, proposal_id))
    db.commit()
    db.close()
    return True


def duplicate_proposal(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        return None
    proposal['title'] = f"{proposal['title']} Copy"
    proposal['status'] = 'draft'
    proposal['email_draft_id'] = None
    proposal['sent_at'] = None
    proposal['accepted_via_whatsapp_at'] = None
    proposal['client_approval_status'] = 'pending'
    return create_proposal(proposal)
