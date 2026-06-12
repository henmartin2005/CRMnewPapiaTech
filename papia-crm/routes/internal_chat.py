from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for

from database import get_db
from routes.auth import login_required


internal_chat_bp = Blueprint('internal_chat', __name__, url_prefix='/chat')
PRESENCE_STATUSES = {'available', 'busy', 'away'}
ONLINE_WINDOW_SECONDS = 90


def _is_superadmin():
    return session.get('user_role') == 'superadmin'


def _allowed_org_ids(db):
    if _is_superadmin():
        return {
            row['id']
            for row in db.execute(
                "SELECT id FROM organizations WHERE is_active=1"
            ).fetchall()
        }
    return {session.get('org_id', 1)}


def _touch_presence(db, user_id):
    db.execute("""
        INSERT INTO internal_user_presence (user_id, status, last_seen, updated_at)
        VALUES (?, 'available', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET last_seen=CURRENT_TIMESTAMP
    """, (user_id,))


def _presence_payload(row):
    last_seen = row['last_seen']
    is_online = False
    if last_seen:
        try:
            seen = datetime.strptime(str(last_seen)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            is_online = (datetime.now(timezone.utc) - seen).total_seconds() <= ONLINE_WINDOW_SECONDS
        except (TypeError, ValueError):
            pass

    if not is_online:
        return {'status': 'offline', 'label': 'No disponible', 'is_online': False}

    status = row['presence_status'] if row['presence_status'] in PRESENCE_STATUSES else 'available'
    labels = {
        'available': 'Disponible',
        'busy': 'Ocupado',
        'away': 'Ausente',
    }
    return {'status': status, 'label': labels[status], 'is_online': True}


def _can_direct_message(current_user, peer):
    if not current_user or not peer or current_user['id'] == peer['id']:
        return False
    if current_user['role'] == 'superadmin' or peer['role'] == 'superadmin':
        return True
    return current_user['org_id'] == peer['org_id']


def _get_user(db, user_id):
    return db.execute("""
        SELECT id, org_id, username, display_name, role, is_active
        FROM users WHERE id=? AND is_active=1
    """, (user_id,)).fetchone()


def get_unread_count(user_id, role, org_id):
    if not user_id:
        return 0

    db = get_db()
    if role == 'superadmin':
        group_row = db.execute("""
            SELECT COUNT(*)
            FROM internal_chat_messages m
            LEFT JOIN internal_chat_reads r
              ON r.user_id=? AND r.org_id=m.org_id
            JOIN organizations o ON o.id=m.org_id AND o.is_active=1
            WHERE m.id > COALESCE(r.last_read_message_id, 0)
              AND (m.sender_user_id IS NULL OR m.sender_user_id != ?)
        """, (user_id, user_id)).fetchone()
    else:
        group_row = db.execute("""
            SELECT COUNT(*)
            FROM internal_chat_messages m
            LEFT JOIN internal_chat_reads r
              ON r.user_id=? AND r.org_id=m.org_id
            WHERE m.org_id=?
              AND m.id > COALESCE(r.last_read_message_id, 0)
              AND (m.sender_user_id IS NULL OR m.sender_user_id != ?)
        """, (user_id, org_id, user_id)).fetchone()

    direct_row = db.execute("""
        SELECT COUNT(*)
        FROM internal_direct_messages m
        LEFT JOIN internal_direct_reads r
          ON r.user_id=? AND r.peer_user_id=m.sender_user_id
        WHERE m.recipient_user_id=?
          AND m.id > COALESCE(r.last_read_message_id, 0)
    """, (user_id, user_id)).fetchone()
    db.close()
    return (group_row[0] if group_row else 0) + (direct_row[0] if direct_row else 0)


@internal_chat_bp.route('/')
@login_required
def index():
    db = get_db()
    current_user_id = session.get('user_id')
    current_user = _get_user(db, current_user_id)
    if not current_user:
        db.close()
        abort(403)

    _touch_presence(db, current_user_id)
    db.commit()

    allowed_org_ids = _allowed_org_ids(db)
    organizations = db.execute("""
        SELECT o.id, o.name, o.logo_url, o.is_active,
               COUNT(DISTINCT CASE WHEN u.is_active=1 THEN u.id END) AS member_count,
               MAX(m.id) AS last_message_id,
               MAX(m.created_at) AS last_message_at
        FROM organizations o
        LEFT JOIN users u ON u.org_id=o.id
        LEFT JOIN internal_chat_messages m ON m.org_id=o.id
        WHERE o.is_active=1
        GROUP BY o.id
        ORDER BY o.sort_order ASC, o.name COLLATE NOCASE
    """).fetchall()
    organizations = [row for row in organizations if row['id'] in allowed_org_ids]
    if not organizations:
        db.close()
        abort(403)

    requested_org_id = request.args.get('org_id', type=int)
    default_org_id = session.get('org_id', 1)
    active_org_id = requested_org_id if requested_org_id in allowed_org_ids else default_org_id
    if active_org_id not in allowed_org_ids:
        active_org_id = organizations[0]['id']

    read_rows = db.execute(
        "SELECT org_id, last_read_message_id FROM internal_chat_reads WHERE user_id=?",
        (current_user_id,),
    ).fetchall()
    read_state = {row['org_id']: row['last_read_message_id'] for row in read_rows}

    org_list = []
    for org in organizations:
        item = dict(org)
        item['last_message'] = db.execute("""
            SELECT message FROM internal_chat_messages
            WHERE org_id=? ORDER BY id DESC LIMIT 1
        """, (org['id'],)).fetchone()
        item['unread_count'] = db.execute("""
            SELECT COUNT(*) FROM internal_chat_messages
            WHERE org_id=? AND id>?
              AND (sender_user_id IS NULL OR sender_user_id != ?)
        """, (org['id'], read_state.get(org['id'], 0), current_user_id)).fetchone()[0]
        org_list.append(item)

    active_org = next(org for org in org_list if org['id'] == active_org_id)

    if _is_superadmin():
        contact_rows = db.execute("""
            SELECT u.id, u.org_id, u.display_name, u.username, u.role,
                   p.status AS presence_status, p.last_seen
            FROM users u
            LEFT JOIN internal_user_presence p ON p.user_id=u.id
            WHERE u.org_id=? AND u.is_active=1 AND u.id!=?
            ORDER BY CASE u.role WHEN 'admin' THEN 0 ELSE 1 END,
                     COALESCE(NULLIF(u.display_name, ''), u.username) COLLATE NOCASE
        """, (active_org_id, current_user_id)).fetchall()
    else:
        contact_rows = db.execute("""
            SELECT u.id, u.org_id, u.display_name, u.username, u.role,
                   p.status AS presence_status, p.last_seen
            FROM users u
            LEFT JOIN internal_user_presence p ON p.user_id=u.id
            WHERE u.is_active=1 AND u.id!=?
              AND (u.org_id=? OR u.role='superadmin')
            ORDER BY CASE u.role WHEN 'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                     COALESCE(NULLIF(u.display_name, ''), u.username) COLLATE NOCASE
        """, (current_user_id, active_org_id)).fetchall()

    contacts = []
    for row in contact_rows:
        contact = dict(row)
        contact.update(_presence_payload(row))
        contact['name'] = row['display_name'] or row['username']
        contact['unread_count'] = db.execute("""
            SELECT COUNT(*)
            FROM internal_direct_messages m
            LEFT JOIN internal_direct_reads r
              ON r.user_id=? AND r.peer_user_id=m.sender_user_id
            WHERE m.recipient_user_id=? AND m.sender_user_id=?
              AND m.id > COALESCE(r.last_read_message_id, 0)
        """, (current_user_id, current_user_id, row['id'])).fetchone()[0]
        contact['last_message'] = db.execute("""
            SELECT message, created_at
            FROM internal_direct_messages
            WHERE (sender_user_id=? AND recipient_user_id=?)
               OR (sender_user_id=? AND recipient_user_id=?)
            ORDER BY id DESC LIMIT 1
        """, (current_user_id, row['id'], row['id'], current_user_id)).fetchone()
        contacts.append(contact)

    requested_peer_id = request.args.get('user_id', type=int)
    active_contact = next((contact for contact in contacts if contact['id'] == requested_peer_id), None)
    conversation_type = 'direct' if active_contact else 'group'

    if active_contact:
        messages = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, NULL AS broadcast_id, created_at
            FROM internal_direct_messages
            WHERE (sender_user_id=? AND recipient_user_id=?)
               OR (sender_user_id=? AND recipient_user_id=?)
            ORDER BY id DESC LIMIT 250
        """, (current_user_id, active_contact['id'], active_contact['id'], current_user_id)).fetchall()
        messages = list(reversed(messages))
        if messages:
            db.execute("""
                INSERT INTO internal_direct_reads
                    (user_id, peer_user_id, last_read_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, peer_user_id) DO UPDATE SET
                    last_read_message_id=excluded.last_read_message_id,
                    updated_at=CURRENT_TIMESTAMP
            """, (current_user_id, active_contact['id'], messages[-1]['id']))
            db.commit()
    else:
        messages = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, broadcast_id, created_at
            FROM internal_chat_messages
            WHERE org_id=?
            ORDER BY id DESC LIMIT 250
        """, (active_org_id,)).fetchall()
        messages = list(reversed(messages))
        if messages:
            db.execute("""
                INSERT INTO internal_chat_reads
                    (user_id, org_id, last_read_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, org_id) DO UPDATE SET
                    last_read_message_id=excluded.last_read_message_id,
                    updated_at=CURRENT_TIMESTAMP
            """, (current_user_id, active_org_id, messages[-1]['id']))
            db.commit()

    own_presence_row = db.execute("""
        SELECT status AS presence_status, last_seen
        FROM internal_user_presence WHERE user_id=?
    """, (current_user_id,)).fetchone()
    own_presence = _presence_payload(own_presence_row)
    db.close()

    return render_template(
        'chat/index.html',
        organizations=org_list,
        active_org=active_org,
        contacts=contacts,
        messages=messages,
        active_contact=active_contact,
        conversation_type=conversation_type,
        own_presence=own_presence,
        is_global_chat_admin=_is_superadmin(),
    )


@internal_chat_bp.route('/send', methods=['POST'])
@login_required
def send():
    message = request.form.get('message', '').strip()
    if not message or len(message) > 4000:
        flash('Escribe un mensaje válido de hasta 4,000 caracteres.', 'danger')
        return redirect(url_for('internal_chat.index'))

    db = get_db()
    current_user = _get_user(db, session.get('user_id'))
    if not current_user:
        db.close()
        abort(403)
    sender_name = current_user['display_name'] or current_user['username']

    recipient_user_id = request.form.get('recipient_user_id', type=int)
    if recipient_user_id:
        peer = _get_user(db, recipient_user_id)
        if not _can_direct_message(current_user, peer):
            db.close()
            abort(403)
        message_org_id = peer['org_id'] if current_user['role'] == 'superadmin' else current_user['org_id']
        db.execute("""
            INSERT INTO internal_direct_messages
                (org_id, sender_user_id, recipient_user_id, sender_name, sender_role, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (message_org_id, current_user['id'], peer['id'], sender_name, current_user['role'], message))
        _touch_presence(db, current_user['id'])
        db.commit()
        db.close()
        return redirect(url_for('internal_chat.index', org_id=message_org_id, user_id=peer['id']))

    allowed_org_ids = _allowed_org_ids(db)
    if _is_superadmin():
        target_org_ids = []
        for value in request.form.getlist('org_ids'):
            try:
                org_id = int(value)
            except (TypeError, ValueError):
                continue
            if org_id in allowed_org_ids and org_id not in target_org_ids:
                target_org_ids.append(org_id)
    else:
        target_org_ids = [current_user['org_id']]

    if not target_org_ids:
        db.close()
        flash('Selecciona al menos una agencia.', 'danger')
        return redirect(url_for('internal_chat.index'))

    broadcast_id = uuid4().hex if len(target_org_ids) > 1 else None
    db.executemany("""
        INSERT INTO internal_chat_messages
            (org_id, sender_user_id, sender_name, sender_role, message, broadcast_id)
        VALUES (?, ?, ?, ?, ?, ?)
    """, [
        (org_id, current_user['id'], sender_name, current_user['role'], message, broadcast_id)
        for org_id in target_org_ids
    ])
    _touch_presence(db, current_user['id'])
    db.commit()
    active_org_id = request.form.get('active_org_id', type=int)
    if active_org_id not in target_org_ids:
        active_org_id = target_org_ids[0]
    db.close()
    if len(target_org_ids) > 1:
        flash(f'Mensaje enviado a {len(target_org_ids)} agencias.', 'success')
    return redirect(url_for('internal_chat.index', org_id=active_org_id))


@internal_chat_bp.route('/heartbeat', methods=['POST'])
@login_required
def heartbeat():
    db = get_db()
    _touch_presence(db, session.get('user_id'))
    db.commit()
    db.close()
    return jsonify({'success': True})


@internal_chat_bp.route('/presence', methods=['POST'])
@login_required
def set_presence():
    data = request.get_json(silent=True) or {}
    status = data.get('status')
    if status not in PRESENCE_STATUSES:
        return jsonify({'success': False, 'error': 'Estado inválido'}), 400
    db = get_db()
    db.execute("""
        INSERT INTO internal_user_presence (user_id, status, last_seen, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            status=excluded.status,
            last_seen=CURRENT_TIMESTAMP,
            updated_at=CURRENT_TIMESTAMP
    """, (session.get('user_id'), status))
    db.commit()
    db.close()
    return jsonify({'success': True, 'status': status})


@internal_chat_bp.route('/latest')
@login_required
def latest():
    org_id = request.args.get('org_id', type=int)
    peer_user_id = request.args.get('user_id', type=int)
    db = get_db()
    current_user = _get_user(db, session.get('user_id'))

    if peer_user_id:
        peer = _get_user(db, peer_user_id)
        if not _can_direct_message(current_user, peer):
            db.close()
            abort(403)
        latest_id = db.execute("""
            SELECT COALESCE(MAX(id), 0) FROM internal_direct_messages
            WHERE (sender_user_id=? AND recipient_user_id=?)
               OR (sender_user_id=? AND recipient_user_id=?)
        """, (current_user['id'], peer['id'], peer['id'], current_user['id'])).fetchone()[0]
    else:
        if org_id not in _allowed_org_ids(db):
            db.close()
            abort(403)
        latest_id = db.execute(
            "SELECT COALESCE(MAX(id), 0) FROM internal_chat_messages WHERE org_id=?",
            (org_id,),
        ).fetchone()[0]

    _touch_presence(db, current_user['id'])
    db.commit()
    db.close()
    return jsonify({'latest_id': latest_id})
