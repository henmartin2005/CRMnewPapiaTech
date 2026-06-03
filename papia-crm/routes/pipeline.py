from flask import Blueprint, render_template, request, jsonify, g
from models.client import (
    get_clients_by_stage, update_pipeline_stage,
    PIPELINE_STAGES, PROJECT_TYPES, STAGE_COLORS
)

pipeline_bp = Blueprint('pipeline', __name__, url_prefix='/pipeline')


@pipeline_bp.route('/')
def kanban():
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    stages_data = get_clients_by_stage(org_id)
    return render_template(
        'pipeline/kanban.html',
        stages_data=stages_data,
        pipeline_stages=PIPELINE_STAGES,
        stage_colors=STAGE_COLORS,
        project_labels=dict(PROJECT_TYPES),
    )


@pipeline_bp.route('/move', methods=['POST'])
def move_card():
    org_id = g.org_id if hasattr(g, 'org_id') else 1
    data = request.get_json()
    client_id = data.get('client_id')
    new_stage = data.get('stage')
    valid_stages = [s for s, _ in PIPELINE_STAGES]
    if client_id and new_stage in valid_stages:
        update_pipeline_stage(client_id, new_stage, org_id=org_id)
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': 'Invalid data'}), 400
