from flask import Blueprint, render_template, redirect, url_for, flash
from models.client import (
    complete_followup,
    get_all_tasks,
    get_due_tasks,
    get_todays_followups,
    FOLLOW_UP_METHODS,
)

followups_bp = Blueprint('followups', __name__)


@followups_bp.route('/followups/')
def index():
    todays = get_todays_followups()
    return render_template(
        'followups/index.html',
        followups=todays,
        method_labels=dict(FOLLOW_UP_METHODS),
    )


@followups_bp.route('/tasks/')
def tasks():
    due = get_due_tasks()
    all_tasks = get_all_tasks()
    return render_template(
        'followups/tasks.html',
        due_tasks=due,
        all_tasks=all_tasks,
        due_ids={task['id'] for task in due},
        method_labels=dict(FOLLOW_UP_METHODS),
    )


@followups_bp.route('/calendar/')
def calendar():
    tasks = get_all_tasks()
    due_ids = {task['id'] for task in get_due_tasks()}
    return render_template(
        'followups/calendar.html',
        tasks=tasks,
        due_ids=due_ids,
        method_labels=dict(FOLLOW_UP_METHODS),
    )


@followups_bp.route('/tasks/<int:followup_id>/complete', methods=['POST'])
def complete_task(followup_id):
    complete_followup(followup_id)
    flash('Tarea marcada como completada.', 'success')
    return redirect(url_for('followups.tasks'))
