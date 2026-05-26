from flask import Blueprint, request, jsonify
from database import get_db

leads_bp = Blueprint('leads', __name__)

VALID_PROJECT_TYPES = {'website', 'crm', 'mobile_app', 'consulting', 'other'}


def _find_client_by_email(email: str):
    db = get_db()
    row = db.execute(
        "SELECT id FROM clients WHERE LOWER(email) = LOWER(?)", (email,)
    ).fetchone()
    db.close()
    return row


def _insert_client(data: dict) -> int:
    db = get_db()
    cursor = db.execute("""
        INSERT INTO clients
            (first_name, last_name, email, phone, company,
             project_type, project_details, pipeline_stage, source,
             total_cost, amount_paid)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'new_lead', 'website', 0, 0)
    """, (
        data['first_name'],
        data['last_name'],
        data['email'],
        data.get('phone', ''),
        data.get('company', ''),
        data['project_type'],
        data.get('project_details', ''),
    ))
    db.commit()
    new_id = cursor.lastrowid
    db.close()
    return new_id


def _insert_note(client_id: int, budget: str, project_details: str):
    parts = []
    if budget:
        parts.append(f"Presupuesto: {budget}")
    if project_details:
        parts.append(project_details)
    content = "Lead desde web" + (" | " + " | ".join(parts) if parts else "")

    db = get_db()
    db.execute(
        "INSERT INTO notes (client_id, note_type, content) VALUES (?, 'website_lead', ?)",
        (client_id, content),
    )
    db.commit()
    db.close()


@leads_bp.route('/api/new-lead', methods=['POST', 'OPTIONS'])
def new_lead():
    if request.method == 'OPTIONS':
        return '', 204

    data = request.get_json(silent=True) or {}

    # ── Handle full_name split ──
    if 'full_name' in data and 'first_name' not in data:
        parts = data['full_name'].strip().split(' ', 1)
        data['first_name'] = parts[0]
        data['last_name'] = parts[1] if len(parts) > 1 else ''

    first_name = (data.get('first_name') or '').strip()
    last_name  = (data.get('last_name')  or '').strip()
    email      = (data.get('email')      or '').strip()

    if not first_name or not email:
        return jsonify({'success': False, 'error': 'first_name y email son requeridos'}), 400

    # ── Normalize project_type ──
    project_type = (data.get('project_type') or 'other').strip().lower()
    if project_type not in VALID_PROJECT_TYPES:
        project_type = 'other'

    budget          = (data.get('budget')          or '').strip()
    project_details = (data.get('project_details') or '').strip()

    # ── Duplicate check ──
    existing = _find_client_by_email(email)
    if existing:
        _insert_note(existing['id'], budget, project_details)
        return jsonify({'success': True, 'message': 'Nota agregada al cliente existente'}), 200

    # ── Create new client + note ──
    try:
        client_id = _insert_client({
            'first_name':      first_name,
            'last_name':       last_name,
            'email':           email,
            'phone':           data.get('phone', ''),
            'company':         data.get('company', ''),
            'project_type':    project_type,
            'project_details': project_details,
        })
        _insert_note(client_id, budget, project_details)
        return jsonify({'success': True, 'message': 'Lead creado'}), 201

    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500
