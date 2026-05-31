from flask import Blueprint, render_template, redirect, url_for, flash, jsonify, request
from database import get_db
from models.client import (
    complete_followup,
    get_all_tasks,
    get_due_tasks,
    get_todays_followups,
    FOLLOW_UP_METHODS,
    add_followup,
)

followups_bp = Blueprint('followups', __name__)


# ── Existing views ────────────────────────────────────────────────────────────

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
    due       = get_due_tasks()
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
    db      = get_db()
    clients = db.execute(
        "SELECT id, first_name, last_name FROM clients ORDER BY first_name"
    ).fetchall()
    db.close()
    return render_template('followups/calendar.html', clients=clients)


@followups_bp.route('/tasks/<int:followup_id>/complete', methods=['POST'])
def complete_task(followup_id):
    complete_followup(followup_id)
    flash('Tarea marcada como completada.', 'success')
    return redirect(url_for('followups.tasks'))


# ── Calendar JSON API ─────────────────────────────────────────────────────────

@followups_bp.route('/calendar/events')
def calendar_events():
    """Return all follow_ups with a date as FullCalendar event objects."""
    db   = get_db()
    rows = db.execute("""
        SELECT f.id, f.summary, f.method, f.next_at, f.next_date,
               f.completed, f.reminder_comment, f.client_id,
               c.first_name, c.last_name
        FROM follow_ups f
        JOIN clients c ON c.id = f.client_id
        WHERE f.next_at IS NOT NULL OR f.next_date IS NOT NULL
        ORDER BY COALESCE(f.next_at, f.next_date)
    """).fetchall()
    db.close()

    events = []
    for r in rows:
        if r['next_at']:
            # "2026-06-15 11:09:00" → "2026-06-15T11:09:00"
            start = r['next_at'][:19].replace(' ', 'T')
            # Default 1-hour duration
            from datetime import datetime, timedelta
            dt  = datetime.fromisoformat(start)
            end = (dt + timedelta(hours=1)).isoformat()
        elif r['next_date']:
            start = r['next_date'][:10]
            end   = None
        else:
            continue

        ev = {
            'id':    r['id'],
            'title': r['summary'],
            'start': start,
            'extendedProps': {
                'method':    r['method'] or 'other',
                'client':    f"{r['first_name']} {r['last_name']}",
                'client_id': r['client_id'],
                'completed': bool(r['completed']),
                'comment':   r['reminder_comment'] or '',
            },
        }
        if end:
            ev['end'] = end
        events.append(ev)

    return jsonify(events)


@followups_bp.route('/calendar/events/<int:event_id>/move', methods=['POST'])
def move_event(event_id):
    """Update follow_up dates after drag & drop or resize."""
    data      = request.get_json(silent=True) or {}
    start_iso = (data.get('start') or '').strip()

    if not start_iso:
        return jsonify({'success': False, 'error': 'start requerido'}), 400

    try:
        db = get_db()
        if 'T' in start_iso:
            # Timed event: "2026-06-15T11:00:00"
            dt_str   = start_iso[:19].replace('T', ' ')   # "2026-06-15 11:00:00"
            date_str = start_iso[:10]                       # "2026-06-15"
            db.execute(
                "UPDATE follow_ups SET next_at=?, next_date=? WHERE id=?",
                (dt_str, date_str, event_id),
            )
        else:
            # All-day event: "2026-06-15"
            db.execute(
                "UPDATE follow_ups SET next_date=?, next_at=NULL WHERE id=?",
                (start_iso[:10], event_id),
            )
        db.commit()
        db.close()
        return jsonify({'success': True})
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500


@followups_bp.route('/calendar/events/new', methods=['POST'])
def new_event():
    """Create a new follow_up from the calendar modal."""
    data      = request.get_json(silent=True) or {}
    client_id = data.get('client_id')
    summary   = (data.get('summary')  or '').strip()
    method    = (data.get('method')   or 'other').strip()
    dt_str    = (data.get('datetime') or '').strip()
    comment   = (data.get('comment')  or '').strip()

    if not client_id or not summary or not dt_str:
        return jsonify({'success': False, 'error': 'client_id, summary y datetime son requeridos'}), 400

    try:
        add_followup(
            client_id=int(client_id),
            method=method,
            summary=summary,
            result='',
            next_at=dt_str,          # _normalize_reminder_at handles both formats
            reminder_comment=comment,
        )
        return jsonify({'success': True}), 201
    except Exception as exc:
        return jsonify({'success': False, 'error': str(exc)}), 500
