from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, g
from models.client import (
    get_all_clients, get_client, create_client, update_client,
    delete_client, get_client_notes, add_note, get_client_followups,
    add_followup, PIPELINE_STAGES, PROJECT_TYPES, NOTE_TYPES,
    FOLLOW_UP_METHODS, STAGE_COLORS
)

from database import get_db

import mimetypes
import os
import re
from uuid import uuid4

from flask import abort, send_file
from werkzeug.utils import secure_filename

from zoneinfo import ZoneInfo
from models.payment_schedule import list_installments
from models.client_layout import get_layout, reset_layout, save_layout
SCHED_TZ = ZoneInfo(os.getenv('APP_TIMEZONE', 'America/New_York'))

from models.client_document import (
    ACCEPT_ATTR, ALLOWED_EXTS, MAX_FILE_BYTES, add_file_document, add_link_document,
    client_dir, delete_client_files, delete_document, file_path, get_document,
    list_documents,
)

clients_bp = Blueprint('clients', __name__, url_prefix='/clients')


@clients_bp.route('/search')
def search_clients():
    q  = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    db   = get_db()
    like = f'%{q}%'
    rows = db.execute("""
        SELECT id, first_name, last_name, email, phone
        FROM clients
        WHERE org_id = ?
          AND (first_name LIKE ? OR last_name LIKE ? OR email LIKE ? OR phone LIKE ?)
        ORDER BY first_name LIMIT 10
    """, (org_id, like, like, like, like)).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@clients_bp.route('/')
def list_clients():
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    search = request.args.get('q', '').strip()
    clients = get_all_clients(org_id, search if search else None)
    return render_template(
        'clients/list.html',
        clients=clients,
        search=search,
        stage_labels=dict(PIPELINE_STAGES),
        stage_colors=STAGE_COLORS,
        project_labels=dict(PROJECT_TYPES),
    )


@clients_bp.route('/new', methods=['GET', 'POST'])
def new_client():
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    if request.method == 'POST':
        errors = _validate_client_form(request.form)
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template(
                'clients/form.html',
                client=request.form,
                pipeline_stages=PIPELINE_STAGES,
                project_types=PROJECT_TYPES,
                is_edit=False,
            )
        client_id = create_client(request.form, org_id=org_id)
        flash('Cliente creado exitosamente.', 'success')
        return redirect(url_for('clients.detail', client_id=client_id))

    return render_template(
        'clients/form.html',
        client={},
        pipeline_stages=PIPELINE_STAGES,
        project_types=PROJECT_TYPES,
        is_edit=False,
    )


@clients_bp.route('/<int:client_id>')
def detail(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    client = get_client(client_id, org_id=org_id)
    if not client:
        flash('Cliente no encontrado.', 'danger')
        return redirect(url_for('clients.list_clients'))

    notes = get_client_notes(client_id)
    followups = get_client_followups(client_id)
    documents = list_documents(client_id, org_id)
    try:
        from models.contract import list_envelopes
        contracts = list_envelopes(org_id, client_id=client_id, limit=20)
    except Exception:
        contracts = []
    schedule = list_installments(client_id, org_id, today=datetime.now(SCHED_TZ).strftime('%Y-%m-%d'))
    try:
        from database import get_db as _wa_db
        _wdb = _wa_db()
        wa_messages = _wdb.execute(
            "SELECT direction, message, created_at, status, phone FROM whatsapp_messages "
            "WHERE client_id = ? AND org_id = ? ORDER BY id ASC", (client_id, org_id)
        ).fetchall()
        _wdb.close()
    except Exception:
        wa_messages = []

    return render_template(
        'clients/detail.html',
        client=client,
        notes=notes,
        followups=followups,
        documents=documents,
        contracts=contracts,
        schedule=schedule,
        wa_messages=wa_messages,
        profile_layout=get_layout(client_id, org_id),
        docs_accept=ACCEPT_ATTR,
        docs_max_mb=MAX_FILE_BYTES // (1024 * 1024),
        pipeline_stages=PIPELINE_STAGES,
        project_types=PROJECT_TYPES,
        note_types=NOTE_TYPES,
        followup_methods=FOLLOW_UP_METHODS,
        stage_labels=dict(PIPELINE_STAGES),
        stage_colors=STAGE_COLORS,
        project_labels=dict(PROJECT_TYPES),
        now_str=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )


@clients_bp.route('/<int:client_id>/edit', methods=['GET', 'POST'])
def edit_client(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    client = get_client(client_id, org_id=org_id)
    if not client:
        flash('Cliente no encontrado.', 'danger')
        return redirect(url_for('clients.list_clients'))

    if request.method == 'POST':
        errors = _validate_client_form(request.form)
        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template(
                'clients/form.html',
                client=request.form,
                pipeline_stages=PIPELINE_STAGES,
                project_types=PROJECT_TYPES,
                is_edit=True,
                client_id=client_id,
            )
        update_client(client_id, request.form, org_id=org_id)
        flash('Cliente actualizado exitosamente.', 'success')
        return redirect(url_for('clients.detail', client_id=client_id))

    return render_template(
        'clients/form.html',
        client=client,
        pipeline_stages=PIPELINE_STAGES,
        project_types=PROJECT_TYPES,
        is_edit=True,
        client_id=client_id,
    )


@clients_bp.route('/<int:client_id>/delete', methods=['POST'])
def delete(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    delete_client(client_id, org_id=org_id)
    delete_client_files(client_id, org_id)
    flash('Cliente eliminado.', 'success')
    return redirect(url_for('clients.list_clients'))


@clients_bp.route('/<int:client_id>/note', methods=['POST'])
def add_client_note(client_id):
    note_type = request.form.get('note_type', 'note')
    content = request.form.get('content', '').strip()
    if content:
        add_note(client_id, note_type, content)
        flash('Nota agregada.', 'success')
    else:
        flash('El contenido de la nota no puede estar vacío.', 'danger')
    return redirect(url_for('clients.detail', client_id=client_id))


@clients_bp.route('/<int:client_id>/followup', methods=['POST'])
def add_client_followup(client_id):
    method = request.form.get('method', 'phone')
    summary = request.form.get('summary', '').strip()
    result = request.form.get('result', '').strip()
    reminder_at = request.form.get('reminder_at', '').strip()
    reminder_comment = request.form.get('reminder_comment', '').strip()

    if not summary:
        flash('La tarea del recordatorio no puede estar vacía.', 'danger')
    elif not reminder_at:
        flash('Selecciona una fecha y hora exacta para el recordatorio.', 'danger')
    else:
        add_followup(
            client_id,
            method,
            summary,
            result,
            next_at=reminder_at,
            reminder_comment=reminder_comment,
        )
        flash('Recordatorio creado.', 'success')
    return redirect(url_for('clients.detail', client_id=client_id))


@clients_bp.route('/<int:client_id>/stage', methods=['POST'])
def update_stage(client_id):
    from models.client import update_pipeline_stage
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    stage = request.form.get('stage')
    valid_stages = [s for s, _ in PIPELINE_STAGES]
    if stage in valid_stages:
        update_pipeline_stage(client_id, stage, org_id=org_id)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Invalid stage'}), 400


def _validate_client_form(form):
    errors = []
    if not form.get('first_name', '').strip():
        errors.append('El nombre es requerido.')
    # Leads from WhatsApp may arrive with only a name + phone
    if not form.get('email', '').strip() and not form.get('phone', '').strip():
        errors.append('Se requiere email o teléfono.')
    if not form.get('project_type', '').strip():
        errors.append('El tipo de proyecto es requerido.')
    try:
        total = float(form.get('total_cost', 0))
        paid = float(form.get('amount_paid', 0))
        if total < 0 or paid < 0:
            errors.append('Los montos no pueden ser negativos.')
        if paid > total:
            errors.append('El monto pagado no puede superar el costo total.')
    except (ValueError, TypeError):
        errors.append('Los montos deben ser números válidos.')
    return errors


# ── Documentos y enlaces del cliente ─────────────────────────────────────────

def _docs_redirect(client_id):
    return redirect(url_for('clients.detail', client_id=client_id) + '#documentos')


@clients_bp.route('/<int:client_id>/documents/upload', methods=['POST'])
def upload_document(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    if not get_client(client_id, org_id=org_id):
        abort(404)

    files = [f for f in request.files.getlist('files') if f and f.filename]
    if not files:
        flash('Selecciona al menos un archivo.', 'danger')
        return _docs_redirect(client_id)

    custom_title = request.form.get('title', '').strip()
    folder = client_dir(org_id, client_id)
    os.makedirs(folder, exist_ok=True)
    saved, rejected = 0, []

    for f in files:
        original = f.filename
        ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
        if ext not in ALLOWED_EXTS:
            rejected.append(f'{original} (tipo no permitido)')
            continue
        safe = secure_filename(original) or f'documento.{ext}'
        stored = f'{uuid4().hex}_{safe}'
        path = os.path.join(folder, stored)
        f.save(path)
        size = os.path.getsize(path)
        if size > MAX_FILE_BYTES:
            os.remove(path)
            rejected.append(f'{original} (supera {MAX_FILE_BYTES // (1024 * 1024)} MB)')
            continue
        title = custom_title if (custom_title and len(files) == 1) else original
        mime = f.mimetype or mimetypes.guess_type(original)[0] or 'application/octet-stream'
        add_file_document(client_id, org_id, title, original, stored, mime, size)
        saved += 1

    if saved:
        flash(f'{saved} documento(s) subido(s).', 'success')
    if rejected:
        flash('No se subieron: ' + ', '.join(rejected), 'danger')
    return _docs_redirect(client_id)


@clients_bp.route('/<int:client_id>/documents/link', methods=['POST'])
def add_document_link(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    if not get_client(client_id, org_id=org_id):
        abort(404)

    url = request.form.get('url', '').strip()
    if not re.match(r'^https?://\S+$', url, re.I):
        flash('El enlace debe empezar con http:// o https://', 'danger')
        return _docs_redirect(client_id)

    title = request.form.get('title', '').strip()
    if not title:
        low = url.lower()
        if 'docs.google.com/document' in low:
            title = 'Documento de Google Docs'
        elif 'docs.google.com/spreadsheets' in low:
            title = 'Hoja de Google Sheets'
        elif 'docs.google.com/presentation' in low:
            title = 'Presentación de Google Slides'
        elif 'drive.google.com' in low or 'docs.google.com' in low:
            title = 'Archivo de Google Drive'
        else:
            title = url[:120]

    add_link_document(client_id, org_id, title[:200], url)
    flash('Enlace agregado.', 'success')
    return _docs_redirect(client_id)


@clients_bp.route('/<int:client_id>/documents/<int:doc_id>')
def open_document(client_id, doc_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    doc = get_document(doc_id, client_id, org_id)
    if not doc:
        abort(404)
    if doc['kind'] == 'link':
        return redirect(doc['url'])

    path = file_path(doc)
    if not os.path.isfile(path):
        flash('El archivo ya no existe en el servidor.', 'danger')
        return _docs_redirect(client_id)
    return send_file(
        path,
        mimetype=doc.get('mime_type') or 'application/octet-stream',
        as_attachment=request.args.get('dl') == '1',
        download_name=doc.get('original_name') or doc['stored_name'],
    )


@clients_bp.route('/<int:client_id>/documents/<int:doc_id>/delete', methods=['POST'])
def remove_document(client_id, doc_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    if delete_document(doc_id, client_id, org_id):
        flash('Documento eliminado.', 'success')
    else:
        flash('Documento no encontrado.', 'danger')
    return _docs_redirect(client_id)


@clients_bp.route('/<int:client_id>/layout', methods=['POST'])
def save_profile_layout(client_id):
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    if not get_client(client_id, org_id=org_id):
        return jsonify({'ok': False}), 404
    payload = request.get_json(silent=True) or {}
    if payload.get('reset'):
        reset_layout(client_id, org_id)
    else:
        save_layout(client_id, org_id, payload)
    return jsonify({'ok': True})
