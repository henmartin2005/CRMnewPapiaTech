"""Deja UNA entrada 'whatsapp_chat' por cliente y elimina las notas 'whatsapp'
que eran copias exactas de mensajes del chat. Respalda la DB antes."""
import os, shutil, sqlite3
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'papia_crm.db')
bak = DB + '.bak-pre-wa-chat-' + datetime.now().strftime('%Y%m%d%H%M%S')
shutil.copy2(DB, bak)
print('Respaldo:', bak)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT client_id, MAX(created_at) AS last_at FROM whatsapp_messages "
    "WHERE client_id IS NOT NULL GROUP BY client_id"
).fetchall()

deleted = created = updated = 0
for r in rows:
    cid = r['client_id']
    phone = conn.execute(
        "SELECT phone FROM whatsapp_messages WHERE client_id = ? ORDER BY id DESC LIMIT 1", (cid,)
    ).fetchone()['phone']
    cur = conn.execute(
        "DELETE FROM notes WHERE client_id = ? AND note_type = 'whatsapp' AND content IN "
        "(SELECT message FROM whatsapp_messages WHERE client_id = ?)", (cid, cid))
    deleted += cur.rowcount
    existing = conn.execute(
        "SELECT id FROM notes WHERE client_id = ? AND note_type = 'whatsapp_chat' ORDER BY id DESC",
        (cid,)).fetchall()
    if existing:
        for extra in existing[1:]:
            conn.execute("DELETE FROM notes WHERE id = ?", (extra['id'],))
            deleted += 1
        conn.execute("UPDATE notes SET content = ?, created_at = MAX(created_at, ?) WHERE id = ?",
                     (phone, r['last_at'], existing[0]['id']))
        updated += 1
    else:
        conn.execute("INSERT INTO notes (client_id, note_type, content, created_at) "
                     "VALUES (?, 'whatsapp_chat', ?, ?)", (cid, phone, r['last_at']))
        created += 1

conn.commit()
conn.close()
print('Clientes con WhatsApp:', len(rows))
print('Notas duplicadas eliminadas:', deleted)
print('Entradas creadas:', created, '| actualizadas:', updated)
