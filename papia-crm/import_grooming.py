"""Importa al CRM los códigos de Groomingmiami que vivían en Supabase.

Uso (una vez, desde papia-crm):  python import_grooming.py
Idempotente: los códigos que ya existen en el CRM se saltan, con su historial.
Fuente: data/grooming_links.json y data/grooming_logs.txt (exportados de Supabase).
"""
import json
from pathlib import Path

from database import get_db
from migrate_demos import migrate

DATA = Path(__file__).parent / 'data'


def run(org_id=1):
    migrate()
    links = json.loads((DATA / 'grooming_links.json').read_text(encoding='utf-8'))
    logs = [line.split('|') for line in (DATA / 'grooming_logs.txt').read_text(encoding='utf-8').splitlines() if line.strip()]
    db = get_db()
    try:
        demo = db.execute("SELECT id FROM demos WHERE org_id=? AND slug='grooming'", (org_id,)).fetchone()
        if not demo:
            raise SystemExit('No existe el demo "grooming" en el catálogo.')
        nuevos = {}
        for codigo, nombre, negocio, max_v, usadas, vence, activo, ilimitado, creado in links:
            if db.execute('SELECT 1 FROM demo_links WHERE codigo=?', (codigo,)).fetchone():
                continue
            cur = db.execute('''INSERT INTO demo_links(org_id, demo_id, codigo, nombre_prospecto, negocio, max_visitas,
                                    visitas_usadas, vence_el, activo, ilimitado, creado_el)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                             (org_id, demo['id'], codigo, nombre, negocio, max_v, usadas, vence, activo, ilimitado, creado))
            nuevos[codigo] = cur.lastrowid
        n_logs = 0
        for codigo, fecha, tipo, seccion, dispositivo, duracion in logs:
            if codigo not in nuevos:
                continue
            db.execute('''INSERT INTO demo_logs(link_id, fecha_hora, tipo, seccion, dispositivo, duracion_segundos)
                          VALUES (?,?,?,?,?,?)''',
                       (nuevos[codigo], fecha, tipo, seccion or None, dispositivo or None,
                        int(duracion) if duracion else None))
            n_logs += 1
        db.commit()
    finally:
        db.close()
    return len(nuevos), n_logs


if __name__ == '__main__':
    c, l = run()
    print(f'Importados {c} códigos y {l} eventos de Groomingmiami.')
