"""
Pizarra de proyectos: lienzo libre (estilo Canva/FigJam) con notas adhesivas, tareas,
figuras, stickers y conexiones. Una pestaña (tablero) por proyecto.
"""
import json

from flask import Blueprint, render_template, request, jsonify, g

from database import get_db

boards_bp = Blueprint('boards', __name__, url_prefix='/pizarra')

MAX_BOARD_BYTES = 3 * 1024 * 1024   # 3 MB por tablero


def _org_id():
    return g.org_id if hasattr(g, 'org_id') else 1


def _ensure_table(db):
    db.execute("""CREATE TABLE IF NOT EXISTS boards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        org_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        data TEXT NOT NULL DEFAULT '{}',
        position INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_boards_org ON boards(org_id, position)")
    cols = {r[1] for r in db.execute("PRAGMA table_info(boards)").fetchall()}
    if 'client_id' not in cols:
        db.execute("ALTER TABLE boards ADD COLUMN client_id INTEGER")


def _list(db, org_id):
    rows = db.execute("SELECT id, name, position, updated_at, client_id FROM boards WHERE org_id = ? "
                      "ORDER BY position, id", (org_id,)).fetchall()
    return [{'id': r[0], 'name': r[1], 'position': r[2], 'updated_at': r[3], 'client_id': r[4]} for r in rows]


def _clients(db, org_id):
    rows = db.execute("SELECT id, first_name, last_name, company FROM clients WHERE org_id = ? "
                      "ORDER BY first_name, last_name", (org_id,)).fetchall()
    out = []
    for r in rows:
        name = ' '.join(x for x in ((r[1] or '').strip(), (r[2] or '').strip()) if x) or f'Cliente #{r[0]}'
        if (r[3] or '').strip():
            name += f' — {r[3].strip()}'
        out.append({'id': r[0], 'name': name})
    return out


STAGE_TYPES = ('shape', 'stage')


def _stages(content):
    """Etapas del tablero (figuras con texto), en el orden de las flechas y luego de izquierda a derecha."""
    nodes = [n for n in (content.get('nodes') or [])
             if n.get('type') in STAGE_TYPES and (n.get('text') or '').strip() and n.get('id')]
    by_id = {n['id']: n for n in nodes}
    pos = {i: (by_id[i].get('x', 0), by_id[i].get('y', 0)) for i in by_id}
    indeg = {i: 0 for i in by_id}
    adj = {i: [] for i in by_id}
    for e in content.get('edges') or []:
        a, b = e.get('from'), e.get('to')
        if a in by_id and b in by_id and a != b:
            adj[a].append(b)
            indeg[b] += 1
    ready = sorted([i for i in by_id if indeg[i] == 0], key=lambda i: pos[i])
    order = []
    while ready:
        i = ready.pop(0)
        order.append(i)
        for j in adj[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                ready.append(j)
        ready.sort(key=lambda k: pos[k])
    order += sorted([i for i in by_id if i not in order], key=lambda i: pos[i])   # ciclos
    return [{'id': i, 'name': ' '.join(by_id[i]['text'].split())[:80],
             'status': by_id[i].get('status') or '', 'done': by_id[i].get('status') == 'done'} for i in order]


def _clean_name(name, default='Proyecto'):
    name = ' '.join((name or '').split())[:60]
    return name or default


@boards_bp.route('/')
def index():
    org_id = _org_id()
    db = get_db()
    try:
        _ensure_table(db)
        boards = _list(db, org_id)
        if not boards:
            db.execute("INSERT INTO boards (org_id, name, data, position) VALUES (?, ?, '{}', 0)",
                       (org_id, 'Proyecto 1'))
            db.commit()
            boards = _list(db, org_id)
    finally:
        db.close()
    active = request.args.get('id', type=int)
    if active not in [b['id'] for b in boards]:
        active = boards[0]['id']
    db = get_db()
    try:
        clients = _clients(db, org_id)
    except Exception:
        clients = []
    finally:
        db.close()
    return render_template('boards/index.html', boards=boards, active_id=active, clients=clients)


@boards_bp.route('/api/boards', methods=['GET'])
def api_list():
    db = get_db()
    try:
        _ensure_table(db)
        return jsonify({'ok': True, 'boards': _list(db, _org_id())})
    finally:
        db.close()


@boards_bp.route('/api/boards', methods=['POST'])
def api_create():
    org_id = _org_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        _ensure_table(db)
        pos = db.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM boards WHERE org_id = ?",
                         (org_id,)).fetchone()[0]
        cur = db.execute("INSERT INTO boards (org_id, name, data, position) VALUES (?, ?, '{}', ?)",
                         (org_id, _clean_name(data.get('name'), f'Proyecto {pos + 1}'), pos))
        db.commit()
        return jsonify({'ok': True, 'id': cur.lastrowid, 'boards': _list(db, org_id)})
    finally:
        db.close()


@boards_bp.route('/api/boards/<int:board_id>', methods=['GET'])
def api_get(board_id):
    db = get_db()
    try:
        _ensure_table(db)
        row = db.execute("SELECT id, name, data, updated_at FROM boards WHERE id = ? AND org_id = ?",
                         (board_id, _org_id())).fetchone()
    finally:
        db.close()
    if not row:
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    try:
        content = json.loads(row[2] or '{}')
    except ValueError:
        content = {}
    return jsonify({'ok': True, 'id': row[0], 'name': row[1], 'data': content, 'updated_at': row[3]})


@boards_bp.route('/api/boards/<int:board_id>', methods=['PATCH'])
def api_update(board_id):
    org_id = _org_id()
    data = request.get_json(silent=True) or {}
    db = get_db()
    try:
        _ensure_table(db)
        if 'name' in data:
            db.execute("UPDATE boards SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND org_id = ?",
                       (_clean_name(data.get('name')), board_id, org_id))
        if 'client_id' in data:
            cid = data.get('client_id')
            if cid in (None, '', 0, '0'):
                cid = None
            else:
                cid = int(cid)
                if not db.execute("SELECT 1 FROM clients WHERE id = ? AND org_id = ?", (cid, org_id)).fetchone():
                    return jsonify({'ok': False, 'error': 'Cliente no válido'}), 400
            db.execute("UPDATE boards SET client_id = ? WHERE id = ? AND org_id = ?", (cid, board_id, org_id))
        if isinstance(data.get('order'), list):
            for i, bid in enumerate(data['order']):
                db.execute("UPDATE boards SET position = ? WHERE id = ? AND org_id = ?", (i, int(bid), org_id))
        db.commit()
        return jsonify({'ok': True, 'boards': _list(db, org_id)})
    finally:
        db.close()


@boards_bp.route('/api/boards/<int:board_id>/data', methods=['PUT'])
def api_save(board_id):
    raw = request.get_data() or b'{}'
    if len(raw) > MAX_BOARD_BYTES:
        return jsonify({'ok': False, 'error': 'El tablero es demasiado grande'}), 413
    try:
        content = json.loads(raw)
        if not isinstance(content, dict):
            raise ValueError
    except ValueError:
        return jsonify({'ok': False, 'error': 'JSON inválido'}), 400
    db = get_db()
    try:
        _ensure_table(db)
        cur = db.execute("UPDATE boards SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND org_id = ?",
                         (json.dumps(content, ensure_ascii=False), board_id, _org_id()))
        db.commit()
    finally:
        db.close()
    if not cur.rowcount:
        return jsonify({'ok': False, 'error': 'No encontrado'}), 404
    return jsonify({'ok': True})


@boards_bp.route('/api/boards/<int:board_id>', methods=['DELETE'])
def api_delete(board_id):
    org_id = _org_id()
    db = get_db()
    try:
        _ensure_table(db)
        total = db.execute("SELECT COUNT(*) FROM boards WHERE org_id = ?", (org_id,)).fetchone()[0]
        if total <= 1:
            return jsonify({'ok': False, 'error': 'Debe quedar al menos un tablero'}), 400
        db.execute("DELETE FROM boards WHERE id = ? AND org_id = ?", (board_id, org_id))
        db.commit()
        return jsonify({'ok': True, 'boards': _list(db, org_id)})
    finally:
        db.close()


# ── Etapas del proyecto en el perfil del cliente ─────────────────────────────

@boards_bp.route('/api/client/<int:client_id>/stages', methods=['GET'])
def api_client_stages(client_id):
    org_id = _org_id()
    db = get_db()
    try:
        _ensure_table(db)
        rows = db.execute("SELECT id, name, data FROM boards WHERE org_id = ? AND client_id = ? "
                          "ORDER BY position, id", (org_id, client_id)).fetchall()
    finally:
        db.close()
    projects = []
    for r in rows:
        try:
            content = json.loads(r[2] or '{}')
        except ValueError:
            content = {}
        stages = _stages(content)
        projects.append({'id': r[0], 'name': r[1], 'stages': stages,
                         'done': sum(1 for s in stages if s['done']), 'total': len(stages)})
    return jsonify({'ok': True, 'projects': projects})


@boards_bp.route('/api/boards/<int:board_id>/stage/<node_id>', methods=['POST'])
def api_toggle_stage(board_id, node_id):
    """Marca o desmarca una etapa como concluida (también se refleja en la pizarra)."""
    org_id = _org_id()
    done = bool((request.get_json(silent=True) or {}).get('done'))
    db = get_db()
    try:
        _ensure_table(db)
        row = db.execute("SELECT data FROM boards WHERE id = ? AND org_id = ?", (board_id, org_id)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'No encontrado'}), 404
        content = json.loads(row[0] or '{}')
        node = next((n for n in content.get('nodes') or [] if n.get('id') == node_id and n.get('type') in STAGE_TYPES), None)
        if not node:
            return jsonify({'ok': False, 'error': 'Etapa no encontrada'}), 404
        node['status'] = 'done' if done else 'todo'
        db.execute("UPDATE boards SET data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND org_id = ?",
                   (json.dumps(content, ensure_ascii=False), board_id, org_id))
        db.commit()
    finally:
        db.close()
    stages = _stages(content)
    return jsonify({'ok': True, 'stages': stages, 'done': sum(1 for s in stages if s['done']), 'total': len(stages)})
