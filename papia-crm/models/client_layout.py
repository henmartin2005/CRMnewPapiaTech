"""Diseño del perfil de cada cliente: posición/tamaño de los cuadros y su color."""
import json
import re

from database import get_db

ALLOWED_COLORS = {'white', 'yellow', 'green', 'pink', 'orange', 'blue', 'purple'}
_KEY = re.compile(r'^[a-z0-9-]{1,60}$')

_SCHEMA = """
CREATE TABLE IF NOT EXISTS client_layouts (
    client_id  INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    org_id     INTEGER NOT NULL DEFAULT 1,
    data       TEXT    NOT NULL DEFAULT '{}',
    updated_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
)
"""


def _db():
    db = get_db()
    db.execute(_SCHEMA)
    return db


def get_layout(client_id, org_id):
    db = _db()
    try:
        row = db.execute(
            "SELECT data FROM client_layouts WHERE client_id=? AND org_id=?", (client_id, org_id)
        ).fetchone()
    finally:
        db.close()
    if not row:
        return {'layout': {}, 'colors': {}}
    try:
        data = json.loads(row['data'])
    except ValueError:
        return {'layout': {}, 'colors': {}}
    return {'layout': data.get('layout', {}), 'colors': data.get('colors', {})}


def _clean(payload):
    layout, colors = {}, {}
    for key, v in (payload.get('layout') or {}).items():
        if not _KEY.match(str(key)) or not isinstance(v, dict):
            continue
        try:
            x, y, w, h = (int(v.get(k, 0)) for k in ('x', 'y', 'w', 'h'))
        except (TypeError, ValueError):
            continue
        if 0 <= x < 12 and 0 <= y < 2000 and 1 <= w <= 12 and 1 <= h <= 500:
            layout[key] = {'x': x, 'y': y, 'w': w, 'h': h}
    for key, c in (payload.get('colors') or {}).items():
        if _KEY.match(str(key)) and c in ALLOWED_COLORS and c != 'white':
            colors[key] = c
    return {'layout': layout, 'colors': colors}


def save_layout(client_id, org_id, payload):
    data = json.dumps(_clean(payload))
    db = _db()
    try:
        db.execute(
            "INSERT INTO client_layouts (client_id, org_id, data, updated_at) "
            "VALUES (?, ?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(client_id) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (client_id, org_id, data),
        )
        db.commit()
    finally:
        db.close()


def reset_layout(client_id, org_id):
    db = _db()
    try:
        db.execute("DELETE FROM client_layouts WHERE client_id=? AND org_id=?", (client_id, org_id))
        db.commit()
    finally:
        db.close()
