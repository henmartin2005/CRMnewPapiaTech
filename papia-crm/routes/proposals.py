from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file

from models.client import get_all_clients, get_client
from models.proposal import (
    DEFAULT_TERMS,
    PROJECT_TYPES,
    PROPOSAL_LANGUAGES,
    PROPOSAL_PURPOSES,
    PROPOSAL_STATUSES,
    SERVICE_CATALOG,
    TEMPLATE_PRESETS,
    create_proposal,
    delete_proposal,
    duplicate_proposal,
    get_proposal,
    get_template_payload,
    list_proposals,
    update_proposal,
    update_status,
)
from models.proposal_email import proposalEmailService
from models.proposal_pdf import proposalPdfService
from models.proposal_template import get_service_catalog, get_templates_for_ui

proposals_bp = Blueprint('proposals', __name__, url_prefix='/proposals')


@proposals_bp.route('/')
def index():
    search = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    proposals = list_proposals(search or None, status or None)
    return render_template(
        'proposals/list.html',
        proposals=proposals,
        search=search,
        active_status=status,
        statuses=PROPOSAL_STATUSES,
        project_types=dict(PROJECT_TYPES),
    )


@proposals_bp.route('/new', methods=['GET', 'POST'])
def new():
    if request.method == 'POST':
        data = _proposal_from_form(request.form)
        errors = _validate_proposal(data)
        if errors:
            for error in errors:
                flash(error, 'danger')
            return _render_form(data, is_edit=False)
        proposal_id = create_proposal(data)
        flash('Proposal created.', 'success')
        return redirect(url_for('proposals.view', proposal_id=proposal_id))

    language = request.args.get('language', 'en')
    purpose = request.args.get('purpose', request.args.get('template', 'landing_page'))
    data = get_template_payload(purpose, language)
    client_id = request.args.get('client_id', type=int)
    if client_id:
        client = get_client(client_id)
        if client:
            data.update({
                'client_id': client['id'],
                'client_name': f"{client['first_name']} {client['last_name']}",
                'business_name': client['company'] or '',
                'email': client['email'],
                'phone': client['phone'] or '',
            })
    data.setdefault('status', 'draft')
    data.setdefault('discount', 0)
    data.setdefault('taxes', 0)
    data.setdefault('initial_payment', 0)
    return _render_form(data, is_edit=False)


@proposals_bp.route('/<int:proposal_id>')
def view(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))
    return render_template(
        'proposals/preview.html',
        proposal=proposal,
        statuses=PROPOSAL_STATUSES,
        languages=dict(PROPOSAL_LANGUAGES),
        purposes=dict(PROPOSAL_PURPOSES),
        project_types=dict(PROJECT_TYPES),
    )


@proposals_bp.route('/<int:proposal_id>/edit', methods=['GET', 'POST'])
def edit(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))

    if request.method == 'POST':
        data = _proposal_from_form(request.form)
        errors = _validate_proposal(data)
        if errors:
            for error in errors:
                flash(error, 'danger')
            data['id'] = proposal_id
            return _render_form(data, is_edit=True)
        update_proposal(proposal_id, data)
        flash('Proposal updated.', 'success')
        return redirect(url_for('proposals.view', proposal_id=proposal_id))

    return _render_form(proposal, is_edit=True)


@proposals_bp.route('/<int:proposal_id>/delete', methods=['POST'])
def delete(proposal_id):
    delete_proposal(proposal_id)
    flash('Proposal deleted.', 'success')
    return redirect(url_for('proposals.index'))


@proposals_bp.route('/<int:proposal_id>/duplicate', methods=['POST'])
def duplicate(proposal_id):
    new_id = duplicate_proposal(proposal_id)
    if not new_id:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))
    flash('Proposal duplicated as draft.', 'success')
    return redirect(url_for('proposals.edit', proposal_id=new_id))


@proposals_bp.route('/<int:proposal_id>/status', methods=['POST'])
def status(proposal_id):
    status_value = request.form.get('status', 'draft')
    if update_status(proposal_id, status_value):
        flash('Proposal status updated.', 'success')
    else:
        flash('Invalid proposal status.', 'danger')
    return redirect(request.referrer or url_for('proposals.view', proposal_id=proposal_id))


@proposals_bp.route('/<int:proposal_id>/pdf')
def pdf(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))
    pdf_buffer = proposalPdfService.generate(proposal, dict(PROJECT_TYPES))
    filename = f"proposal-{proposal_id}-{_slug(proposal['client_name'])}.pdf"
    return send_file(pdf_buffer, as_attachment=True, download_name=filename, mimetype='application/pdf')


@proposals_bp.route('/<int:proposal_id>/email-draft', methods=['POST'])
def email_draft(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))
    draft_id = proposalEmailService.createDraftFromProposal(proposal)
    flash('Proposal email draft is ready to review.', 'success')
    return redirect(url_for('emails.index', proposal_draft=draft_id))


@proposals_bp.route('/<int:proposal_id>/print')
def print_view(proposal_id):
    proposal = get_proposal(proposal_id)
    if not proposal:
        flash('Proposal not found.', 'danger')
        return redirect(url_for('proposals.index'))
    return render_template(
        'proposals/pdf.html',
        proposal=proposal,
        project_types=dict(PROJECT_TYPES),
    )


@proposals_bp.route('/template/<language>/<purpose>')
def template_payload(language, purpose):
    return jsonify(get_template_payload(purpose, language))


@proposals_bp.route('/services/<language>')
def services_payload(language):
    return jsonify(get_service_catalog(language))


def _render_form(proposal, is_edit):
    return render_template(
        'proposals/form.html',
        proposal=proposal,
        is_edit=is_edit,
        clients=get_all_clients(),
        statuses=PROPOSAL_STATUSES,
        languages=PROPOSAL_LANGUAGES,
        purposes=PROPOSAL_PURPOSES,
        project_types=PROJECT_TYPES,
        service_catalog=get_service_catalog(proposal.get('proposal_language', 'en')),
        templates=get_templates_for_ui(),
        default_terms=DEFAULT_TERMS,
    )


def _proposal_from_form(form):
    service_ids = form.getlist('service_id[]')
    services = []
    for service_id in service_ids:
        services.append({
            'id': service_id,
            'name': form.get(f'service_name_{service_id}', '').strip(),
            'category': form.get(f'service_category_{service_id}', '').strip(),
            'description': form.get(f'service_description_{service_id}', '').strip(),
            'price': float(form.get(f'service_price_{service_id}') or 0),
            'deliveryTime': form.get(f'service_delivery_{service_id}', '').strip(),
            'notes': form.get(f'service_notes_{service_id}', '').strip(),
        })

    milestones = [m.strip() for m in form.getlist('milestones[]') if m.strip()]
    terms = [t.strip() for t in form.getlist('terms[]') if t.strip()]

    return {
        'client_id': form.get('client_id') or None,
        'client_name': form.get('client_name', '').strip(),
        'business_name': form.get('business_name', '').strip(),
        'email': form.get('email', '').strip(),
        'phone': form.get('phone', '').strip(),
        'address': form.get('address', '').strip(),
        'title': form.get('title', '').strip(),
        'project_type': form.get('project_type', 'other'),
        'proposal_language': form.get('proposal_language', 'en'),
        'proposal_purpose': form.get('proposal_purpose', form.get('project_type', 'other')),
        'project_description': form.get('project_description', '').strip(),
        'client_needs': form.get('client_needs', '').strip(),
        'project_objective': form.get('project_objective', '').strip(),
        'selected_services': services,
        'discount': float(form.get('discount') or 0),
        'taxes': float(form.get('taxes') or 0),
        'initial_payment': float(form.get('initial_payment') or 0),
        'payment_terms': form.get('payment_terms', '').strip(),
        'payment_schedule': form.get('payment_schedule', '').strip(),
        'payment_methods': form.get('payment_methods', '').strip(),
        'start_date': form.get('start_date') or None,
        'estimated_delivery_date': form.get('estimated_delivery_date') or None,
        'business_days': int(form.get('business_days') or 0),
        'milestones': milestones,
        'terms_and_conditions': terms or DEFAULT_TERMS,
        'status': form.get('status', 'draft'),
        'client_approval_status': form.get('client_approval_status', 'pending'),
    }


def _validate_proposal(data):
    errors = []
    if not data['client_name']:
        errors.append('Client name is required.')
    if not data['email']:
        errors.append('Email is required.')
    if not data['title']:
        errors.append('Proposal title is required.')
    if not data['selected_services']:
        errors.append('Select at least one service.')
    return errors


def _slug(value):
    clean = ''.join(ch.lower() if ch.isalnum() else '-' for ch in value)
    return '-'.join(part for part in clean.split('-') if part) or 'client'
