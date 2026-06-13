import os
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from database import get_db
from routes.auth import login_required


internal_chat_bp = Blueprint('internal_chat', __name__, url_prefix='/chat')
PRESENCE_STATUSES = {'available', 'busy', 'away'}
ONLINE_WINDOW_SECONDS = 90
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'chat_uploads')
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
ALLOWED_ATTACHMENT_EXTS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'csv', 'png', 'jpg', 'jpeg', 'gif', 'webp', 'zip',
}
ALLOWED_REACTIONS = {'👍', '❤️', '😂', '😮', '🙏', '🎉'}


def _wants_json():
    return (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or 'application/json' in request.headers.get('Accept', '')
    )


def _server_ts(db):
    return db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]


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


def _cleanup_expired_attachments(db):
    rows = db.execute("""
        SELECT id, stored_name
        FROM internal_chat_attachments
        WHERE expires_at <= CURRENT_TIMESTAMP
    """).fetchall()
    for row in rows:
        try:
            os.remove(os.path.join(UPLOAD_DIR, row['stored_name']))
        except (FileNotFoundError, OSError):
            pass
    if rows:
        db.executemany(
            "DELETE FROM internal_chat_attachments WHERE id=?",
            [(row['id'],) for row in rows],
        )


def _attachment_ext(filename):
    if '.' not in filename:
        return ''
    return filename.rsplit('.', 1)[-1].lower()


def _save_attachment(db, file_storage, org_id, conversation_type, message_id, uploader_user_id):
    if not file_storage or not file_storage.filename:
        return

    original_name = file_storage.filename.strip()
    ext = _attachment_ext(original_name)
    if ext not in ALLOWED_ATTACHMENT_EXTS:
        raise ValueError('Tipo de archivo no permitido.')

    file_storage.stream.seek(0, os.SEEK_END)
    file_size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if file_size > MAX_ATTACHMENT_BYTES:
        raise ValueError('El archivo no puede superar 10 MB.')

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    safe_name = secure_filename(original_name) or f'adjunto.{ext}'
    stored_name = f"{uuid4().hex}_{safe_name}"
    file_storage.save(os.path.join(UPLOAD_DIR, stored_name))

    db.execute("""
        INSERT INTO internal_chat_attachments
            (org_id, conversation_type, message_id, uploader_user_id, original_name,
             stored_name, mime_type, file_size, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime(CURRENT_TIMESTAMP, '+24 hours'))
    """, (
        org_id,
        conversation_type,
        message_id,
        uploader_user_id,
        original_name,
        stored_name,
        file_storage.mimetype or '',
        file_size,
    ))


def _attach_files_to_messages(db, messages, conversation_type):
    if not messages:
        return []

    message_dicts = [dict(message) for message in messages]
    ids = [message['id'] for message in message_dicts]
    placeholders = ','.join('?' for _ in ids)
    rows = db.execute(f"""
        SELECT *
        FROM internal_chat_attachments
        WHERE conversation_type=?
          AND message_id IN ({placeholders})
          AND expires_at > CURRENT_TIMESTAMP
        ORDER BY id ASC
    """, [conversation_type, *ids]).fetchall()

    attachments_by_message = {}
    for row in rows:
        attachments_by_message.setdefault(row['message_id'], []).append(dict(row))

    for message in message_dicts:
        message['attachments'] = attachments_by_message.get(message['id'], [])
    return message_dicts


def _format_size(num_bytes):
    try:
        size = float(num_bytes or 0)
    except (TypeError, ValueError):
        size = 0
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f"{size:.0f} {unit}" if unit == 'B' else f"{size:.1f} {unit}"
        size /= 1024


def _attachment_payload(file_row):
    return {
        'id': file_row['id'],
        'original_name': file_row['original_name'],
        'file_size': file_row['file_size'],
        'file_size_label': _format_size(file_row['file_size']),
        'url': url_for('internal_chat.attachment', attachment_id=file_row['id']),
    }


def _message_payload(message, conversation_type, current_user_id):
    broadcast_id = message.get('broadcast_id') if isinstance(message, dict) else message['broadcast_id']
    return {
        'id': message['id'],
        'org_id': message['org_id'],
        'conversation_type': conversation_type,
        'sender_user_id': message['sender_user_id'],
        'sender_name': message['sender_name'],
        'sender_role': message['sender_role'],
        'message': message['message'] or '',
        'broadcast_id': broadcast_id,
        'created_at': message['created_at'],
        'created_at_label': message['created_at'],
        'own': message['sender_user_id'] == current_user_id,
        'attachments': [_attachment_payload(file) for file in message.get('attachments', [])],
        'reactions': message.get('reactions', []),
    }


def _json_error(message, status=400):
    if _wants_json():
        return jsonify({'success': False, 'error': message}), status
    flash(message, 'danger')
    return None


def _can_write_general(db, user):
    if not user:
        return False
    if user['role'] in ('admin', 'superadmin'):
        return True
    row = db.execute("""
        SELECT can_write_general
        FROM internal_chat_permissions
        WHERE user_id=? AND org_id=?
    """, (user['id'], user['org_id'])).fetchone()
    return bool(row and row['can_write_general'])


def _attach_reactions_to_messages(db, messages, conversation_type):
    if not messages:
        return messages

    ids = [message['id'] for message in messages]
    placeholders = ','.join('?' for _ in ids)
    rows = db.execute(f"""
        SELECT r.message_id, r.emoji, COUNT(*) AS count,
               MAX(CASE WHEN r.user_id=? THEN 1 ELSE 0 END) AS reacted_by_me
        FROM internal_chat_reactions r
        WHERE r.conversation_type=?
          AND r.message_id IN ({placeholders})
        GROUP BY r.message_id, r.emoji
        ORDER BY MIN(r.id)
    """, [session.get('user_id'), conversation_type, *ids]).fetchall()

    reactions_by_message = {}
    for row in rows:
        reactions_by_message.setdefault(row['message_id'], []).append(dict(row))

    for message in messages:
        message['reactions'] = reactions_by_message.get(message['id'], [])
    return messages


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
    if current_user['role'] == 'superadmin':
        return True
    if current_user['role'] == 'admin':
        return peer['role'] == 'superadmin' or current_user['org_id'] == peer['org_id']
    return current_user['org_id'] == peer['org_id'] and peer['role'] == 'admin'


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
    _cleanup_expired_attachments(db)
    db.commit()
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
                   o.name AS org_name,
                   p.status AS presence_status, p.last_seen
            FROM users u
            JOIN organizations o ON o.id=u.org_id AND o.is_active=1
            LEFT JOIN internal_user_presence p ON p.user_id=u.id
            WHERE u.is_active=1 AND u.id!=?
            ORDER BY CASE u.role WHEN 'admin' THEN 0 ELSE 1 END,
                     o.sort_order ASC, o.name COLLATE NOCASE,
                     COALESCE(NULLIF(u.display_name, ''), u.username) COLLATE NOCASE
        """, (current_user_id,)).fetchall()
        contact_rows = [row for row in contact_rows if row['org_id'] in allowed_org_ids]
    elif current_user['role'] == 'admin':
        contact_rows = db.execute("""
            SELECT u.id, u.org_id, u.display_name, u.username, u.role,
                   o.name AS org_name,
                   p.status AS presence_status, p.last_seen
            FROM users u
            JOIN organizations o ON o.id=u.org_id
            LEFT JOIN internal_user_presence p ON p.user_id=u.id
            WHERE u.is_active=1 AND u.id!=?
              AND (u.org_id=? OR u.role='superadmin')
            ORDER BY CASE u.role WHEN 'superadmin' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END,
                     COALESCE(NULLIF(u.display_name, ''), u.username) COLLATE NOCASE
        """, (current_user_id, active_org_id)).fetchall()
    else:
        contact_rows = db.execute("""
            SELECT u.id, u.org_id, u.display_name, u.username, u.role,
                   o.name AS org_name,
                   p.status AS presence_status, p.last_seen
            FROM users u
            JOIN organizations o ON o.id=u.org_id
            LEFT JOIN internal_user_presence p ON p.user_id=u.id
            WHERE u.is_active=1 AND u.id!=?
              AND u.org_id=?
              AND u.role='admin'
            ORDER BY COALESCE(NULLIF(u.display_name, ''), u.username) COLLATE NOCASE
        """, (current_user_id, active_org_id)).fetchall()

    contacts = []
    for row in contact_rows:
        contact = dict(row)
        contact.update(_presence_payload(row))
        contact['name'] = row['display_name'] or row['username']
        contact['org_name'] = row['org_name']
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

    admin_contacts = [contact for contact in contacts if contact['role'] in ('admin', 'superadmin')]
    user_contacts = [contact for contact in contacts if contact['role'] not in ('admin', 'superadmin')]
    can_write_general = _can_write_general(db, current_user)

    requested_peer_id = request.args.get('user_id', type=int)
    active_contact = next((contact for contact in contacts if contact['id'] == requested_peer_id), None)
    conversation_type = 'direct' if active_contact else 'group'

    if active_contact:
        message_rows = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, NULL AS broadcast_id, created_at
            FROM internal_direct_messages
            WHERE (sender_user_id=? AND recipient_user_id=?)
               OR (sender_user_id=? AND recipient_user_id=?)
            ORDER BY id DESC LIMIT 250
        """, (current_user_id, active_contact['id'], active_contact['id'], current_user_id)).fetchall()
        messages = _attach_files_to_messages(db, list(reversed(message_rows)), 'direct')
        messages = _attach_reactions_to_messages(db, messages, 'direct')
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
        message_rows = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, broadcast_id, created_at
            FROM internal_chat_messages
            WHERE org_id=?
            ORDER BY id DESC LIMIT 250
        """, (active_org_id,)).fetchall()
        messages = _attach_files_to_messages(db, list(reversed(message_rows)), 'group')
        messages = _attach_reactions_to_messages(db, messages, 'group')
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
    initial_server_ts = _server_ts(db)
    db.close()

    return render_template(
        'chat/index.html',
        organizations=org_list,
        active_org=active_org,
        contacts=contacts,
        admin_contacts=admin_contacts,
        user_contacts=user_contacts,
        messages=messages,
        active_contact=active_contact,
        conversation_type=conversation_type,
        own_presence=own_presence,
        is_global_chat_admin=_is_superadmin(),
        format_file_size=_format_size,
        can_write_general=can_write_general,
        reaction_options=sorted(ALLOWED_REACTIONS),
        initial_server_ts=initial_server_ts,
    )


@internal_chat_bp.route('/send', methods=['POST'])
@login_required
def send():
    message = request.form.get('message', '').strip()
    attachment = request.files.get('attachment')
    has_attachment = bool(attachment and attachment.filename)
    if (not message and not has_attachment) or len(message) > 4000:
        if _wants_json():
            return jsonify({'success': False, 'error': 'Escribe un mensaje válido de hasta 4,000 caracteres.'}), 400
        flash('Escribe un mensaje válido de hasta 4,000 caracteres.', 'danger')
        return redirect(url_for('internal_chat.index'))

    db = get_db()
    _cleanup_expired_attachments(db)
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
        cursor = db.execute("""
            INSERT INTO internal_direct_messages
                (org_id, sender_user_id, recipient_user_id, sender_name, sender_role, message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (message_org_id, current_user['id'], peer['id'], sender_name, current_user['role'], message))
        try:
            _save_attachment(db, attachment, message_org_id, 'direct', cursor.lastrowid, current_user['id'])
        except ValueError as exc:
            db.rollback()
            db.close()
            if _wants_json():
                return jsonify({'success': False, 'error': str(exc)}), 400
            flash(str(exc), 'danger')
            return redirect(url_for('internal_chat.index', org_id=message_org_id, user_id=peer['id']))
        _touch_presence(db, current_user['id'])
        message_id = cursor.lastrowid
        message_row = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, NULL AS broadcast_id, created_at
            FROM internal_direct_messages
            WHERE id=?
        """, (message_id,)).fetchone()
        messages = _attach_files_to_messages(db, [message_row], 'direct')
        messages = _attach_reactions_to_messages(db, messages, 'direct')
        server_ts = _server_ts(db)
        db.commit()
        db.close()
        if _wants_json():
            return jsonify({
                'success': True,
                'server_ts': server_ts,
                'message': _message_payload(messages[0], 'direct', current_user['id']),
            })
        return redirect(url_for('internal_chat.index', org_id=message_org_id, user_id=peer['id']))

    allowed_org_ids = _allowed_org_ids(db)
    if current_user['role'] not in ('admin', 'superadmin') and not _can_write_general(db, current_user):
        db.close()
        if _wants_json():
            return jsonify({'success': False, 'error': 'Tu acceso al canal general es solo de lectura.'}), 403
        flash('Tu acceso al canal general es solo de lectura.', 'danger')
        return redirect(url_for('internal_chat.index', org_id=current_user['org_id']))

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
        if _wants_json():
            return jsonify({'success': False, 'error': 'Selecciona al menos una agencia.'}), 400
        flash('Selecciona al menos una agencia.', 'danger')
        return redirect(url_for('internal_chat.index'))

    broadcast_id = uuid4().hex if len(target_org_ids) > 1 else None
    created_message_ids = {}
    for org_id in target_org_ids:
        cursor = db.execute("""
            INSERT INTO internal_chat_messages
                (org_id, sender_user_id, sender_name, sender_role, message, broadcast_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (org_id, current_user['id'], sender_name, current_user['role'], message, broadcast_id))
        created_message_ids[org_id] = cursor.lastrowid
        if has_attachment:
            try:
                attachment.stream.seek(0)
                _save_attachment(db, attachment, org_id, 'group', cursor.lastrowid, current_user['id'])
            except ValueError as exc:
                db.rollback()
                db.close()
                if _wants_json():
                    return jsonify({'success': False, 'error': str(exc)}), 400
                flash(str(exc), 'danger')
                return redirect(url_for('internal_chat.index', org_id=org_id))
    _touch_presence(db, current_user['id'])
    active_org_id = request.form.get('active_org_id', type=int)
    if active_org_id not in target_org_ids:
        active_org_id = target_org_ids[0]
    message_row = db.execute("""
        SELECT id, org_id, sender_user_id, sender_name, sender_role,
               message, broadcast_id, created_at
        FROM internal_chat_messages
        WHERE id=?
    """, (created_message_ids[active_org_id],)).fetchone()
    messages = _attach_files_to_messages(db, [message_row], 'group')
    messages = _attach_reactions_to_messages(db, messages, 'group')
    server_ts = _server_ts(db)
    db.commit()
    db.close()
    if _wants_json():
        return jsonify({
            'success': True,
            'server_ts': server_ts,
            'message': _message_payload(messages[0], 'group', current_user['id']),
            'sent_org_count': len(target_org_ids),
        })
    if len(target_org_ids) > 1:
        flash(f'Mensaje enviado a {len(target_org_ids)} agencias.', 'success')
    return redirect(url_for('internal_chat.index', org_id=active_org_id))


@internal_chat_bp.route('/attachment/<int:attachment_id>')
@login_required
def attachment(attachment_id):
    db = get_db()
    _cleanup_expired_attachments(db)
    current_user = _get_user(db, session.get('user_id'))
    if not current_user:
        db.close()
        abort(403)

    row = db.execute("""
        SELECT *
        FROM internal_chat_attachments
        WHERE id=? AND expires_at > CURRENT_TIMESTAMP
    """, (attachment_id,)).fetchone()
    if not row:
        db.commit()
        db.close()
        abort(404)

    allowed = False
    if row['conversation_type'] == 'group':
        allowed = row['org_id'] in _allowed_org_ids(db)
    elif row['conversation_type'] == 'direct':
        msg = db.execute("""
            SELECT sender_user_id, recipient_user_id
            FROM internal_direct_messages
            WHERE id=?
        """, (row['message_id'],)).fetchone()
        allowed = bool(msg and current_user['id'] in (msg['sender_user_id'], msg['recipient_user_id']))

    db.commit()
    db.close()
    if not allowed:
        abort(403)

    return send_from_directory(
        UPLOAD_DIR,
        row['stored_name'],
        as_attachment=True,
        download_name=row['original_name'],
    )


@internal_chat_bp.route('/react', methods=['POST'])
@login_required
def react():
    data = request.get_json(silent=True) or {}
    conversation_type = data.get('conversation_type')
    message_id = data.get('message_id')
    emoji = data.get('emoji')

    if conversation_type not in ('group', 'direct') or emoji not in ALLOWED_REACTIONS:
        return jsonify({'success': False, 'error': 'Reacción inválida'}), 400

    try:
        message_id = int(message_id)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Mensaje inválido'}), 400

    db = get_db()
    current_user = _get_user(db, session.get('user_id'))
    if not current_user:
        db.close()
        abort(403)

    allowed = False
    reaction_org_id = None
    if conversation_type == 'group':
        row = db.execute(
            "SELECT org_id FROM internal_chat_messages WHERE id=?",
            (message_id,),
        ).fetchone()
        allowed = bool(row and row['org_id'] in _allowed_org_ids(db))
        reaction_org_id = row['org_id'] if row else None
    else:
        row = db.execute("""
            SELECT org_id, sender_user_id, recipient_user_id
            FROM internal_direct_messages
            WHERE id=?
        """, (message_id,)).fetchone()
        allowed = bool(row and current_user['id'] in (row['sender_user_id'], row['recipient_user_id']))
        reaction_org_id = row['org_id'] if row else None

    if not allowed:
        db.close()
        abort(403)

    existing = db.execute("""
        SELECT id FROM internal_chat_reactions
        WHERE conversation_type=? AND message_id=? AND user_id=? AND emoji=?
    """, (conversation_type, message_id, current_user['id'], emoji)).fetchone()
    if existing:
        db.execute("DELETE FROM internal_chat_reactions WHERE id=?", (existing['id'],))
        active = False
    else:
        db.execute("""
            INSERT OR IGNORE INTO internal_chat_reactions
                (conversation_type, message_id, user_id, emoji)
            VALUES (?, ?, ?, ?)
        """, (conversation_type, message_id, current_user['id'], emoji))
        active = True
    db.execute("""
        INSERT INTO internal_chat_reaction_events
            (conversation_type, message_id, org_id, user_id, emoji, active)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (conversation_type, message_id, reaction_org_id, current_user['id'], emoji, 1 if active else 0))
    db.commit()

    rows = db.execute("""
        SELECT emoji, COUNT(*) AS count,
               MAX(CASE WHEN user_id=? THEN 1 ELSE 0 END) AS reacted_by_me
        FROM internal_chat_reactions
        WHERE conversation_type=? AND message_id=?
        GROUP BY emoji
        ORDER BY MIN(id)
    """, (current_user['id'], conversation_type, message_id)).fetchall()
    db.close()
    return jsonify({
        'success': True,
        'active': active,
        'reactions': [dict(row) for row in rows],
    })


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


@internal_chat_bp.route('/sync')
@login_required
def sync():
    org_id = request.args.get('org_id', type=int)
    peer_user_id = request.args.get('user_id', type=int)
    since_id = request.args.get('since_id', default=0, type=int)
    since_ts = request.args.get('since_ts', default='', type=str)

    db = get_db()
    _cleanup_expired_attachments(db)
    current_user = _get_user(db, session.get('user_id'))
    if not current_user:
        db.close()
        abort(403)

    if peer_user_id:
        peer = _get_user(db, peer_user_id)
        if not _can_direct_message(current_user, peer):
            db.close()
            abort(403)
        conversation_type = 'direct'
        message_rows = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, NULL AS broadcast_id, created_at
            FROM internal_direct_messages
            WHERE id>?
              AND ((sender_user_id=? AND recipient_user_id=?)
                OR (sender_user_id=? AND recipient_user_id=?))
            ORDER BY id ASC
            LIMIT 100
        """, (since_id, current_user['id'], peer['id'], peer['id'], current_user['id'])).fetchall()
        changed_rows = db.execute("""
            SELECT DISTINCT e.message_id
            FROM internal_chat_reaction_events e
            JOIN internal_direct_messages m ON m.id=e.message_id
            WHERE e.conversation_type='direct'
              AND e.created_at > COALESCE(NULLIF(?, ''), '1970-01-01')
              AND ((m.sender_user_id=? AND m.recipient_user_id=?)
                OR (m.sender_user_id=? AND m.recipient_user_id=?))
        """, (since_ts, current_user['id'], peer['id'], peer['id'], current_user['id'])).fetchall()
    else:
        if org_id not in _allowed_org_ids(db):
            db.close()
            abort(403)
        conversation_type = 'group'
        message_rows = db.execute("""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, broadcast_id, created_at
            FROM internal_chat_messages
            WHERE org_id=? AND id>?
            ORDER BY id ASC
            LIMIT 100
        """, (org_id, since_id)).fetchall()
        changed_rows = db.execute("""
            SELECT DISTINCT message_id
            FROM internal_chat_reaction_events
            WHERE conversation_type='group'
              AND org_id=?
              AND created_at > COALESCE(NULLIF(?, ''), '1970-01-01')
        """, (org_id, since_ts)).fetchall()

    messages = _attach_files_to_messages(db, message_rows, conversation_type)
    messages = _attach_reactions_to_messages(db, messages, conversation_type)
    message_payloads = [
        _message_payload(message, conversation_type, current_user['id'])
        for message in messages
    ]

    changed_ids = sorted({row['message_id'] for row in changed_rows})
    reactions_changed = []
    if changed_ids:
        placeholders = ','.join('?' for _ in changed_ids)
        rows = db.execute(f"""
            SELECT id, org_id, sender_user_id, sender_name, sender_role,
                   message, NULL AS broadcast_id, created_at
            FROM {'internal_direct_messages' if conversation_type == 'direct' else 'internal_chat_messages'}
            WHERE id IN ({placeholders})
        """, changed_ids).fetchall()
        changed_messages = _attach_reactions_to_messages(db, [dict(row) for row in rows], conversation_type)
        reactions_changed = [
            {'message_id': message['id'], 'reactions': message.get('reactions', [])}
            for message in changed_messages
        ]

    latest_id = max([since_id] + [message['id'] for message in messages])
    if messages:
        if conversation_type == 'direct':
            db.execute("""
                INSERT INTO internal_direct_reads
                    (user_id, peer_user_id, last_read_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, peer_user_id) DO UPDATE SET
                    last_read_message_id=MAX(last_read_message_id, excluded.last_read_message_id),
                    updated_at=CURRENT_TIMESTAMP
            """, (current_user['id'], peer_user_id, latest_id))
        else:
            db.execute("""
                INSERT INTO internal_chat_reads
                    (user_id, org_id, last_read_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, org_id) DO UPDATE SET
                    last_read_message_id=MAX(last_read_message_id, excluded.last_read_message_id),
                    updated_at=CURRENT_TIMESTAMP
            """, (current_user['id'], org_id, latest_id))

    _touch_presence(db, current_user['id'])
    server_ts = _server_ts(db)
    db.commit()
    db.close()
    return jsonify({
        'success': True,
        'conversation_type': conversation_type,
        'latest_id': latest_id,
        'server_ts': server_ts,
        'messages': message_payloads,
        'reactions_changed': reactions_changed,
    })


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
