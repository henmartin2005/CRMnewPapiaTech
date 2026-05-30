from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from models.client import (
    get_all_clients, get_client, create_client, update_client,
    delete_client, get_client_notes, add_note, get_client_followups,
    add_followup, PIPELINE_STAGES, PROJECT_TYPES, NOTE_TYPES,
    FOLLOW_UP_METHODS, STAGE_COLORS
)

clients_bp = Blueprint('clients', __name__, url_prefix='/clients')


@clients_bp.route('/')
def list_clients():
    search = request.args.get('q', '').strip()
    clients = get_all_clients(search if search else None)
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
        client_id = create_client(request.form)
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
    client = get_client(client_id)
    if not client:
        flash('Cliente no encontrado.', 'danger')
        return redirect(url_for('clients.list_clients'))

    notes = get_client_notes(client_id)
    followups = get_client_followups(client_id)

    return render_template(
        'clients/detail.html',
        client=client,
        notes=notes,
        followups=followups,
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
    client = get_client(client_id)
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
        update_client(client_id, request.form)
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
    delete_client(client_id)
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
    stage = request.form.get('stage')
    valid_stages = [s for s, _ in PIPELINE_STAGES]
    if stage in valid_stages:
        update_pipeline_stage(client_id, stage)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Invalid stage'}), 400


def _validate_client_form(form):
    errors = []
    if not form.get('first_name', '').strip():
        errors.append('El nombre es requerido.')
    if not form.get('last_name', '').strip():
        errors.append('El apellido es requerido.')
    if not form.get('email', '').strip():
        errors.append('El email es requerido.')
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
