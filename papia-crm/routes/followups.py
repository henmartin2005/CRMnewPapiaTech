from flask import Blueprint, render_template
from models.client import get_todays_followups, FOLLOW_UP_METHODS

followups_bp = Blueprint('followups', __name__, url_prefix='/followups')


@followups_bp.route('/')
def index():
    todays = get_todays_followups()
    return render_template(
        'followups/index.html',
        followups=todays,
        method_labels=dict(FOLLOW_UP_METHODS),
    )
