"""Tareas programadas de Instagram Studio (PythonAnywhere → Tasks).

  python ig_studio_jobs.py daily     # snapshot diario + posts/insights + renovar tokens + pendientes
  python ig_studio_jobs.py weekly    # reporte de estrategia (solo actúa los lunes; --force para otro día)
  python ig_studio_jobs.py pending   # reprocesa comentarios que quedaron pendientes
  python ig_studio_jobs.py hourly    # pendientes + sync ligero (opcional, plan pago)
"""
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / '.env', override=True)
except ImportError:
    pass

from migrate_ig_studio import migrate
from services import ig_studio as igs

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('ig_studio_jobs')


def daily():
    log.info('Tokens renovados: %s', igs.refresh_tokens())
    log.info('Sync: %s', igs.sync_all())
    log.info('Pendientes: %s', igs.process_pending())


def weekly(org_id=1, force=False):
    # PythonAnywhere solo agenda tareas diarias: la tarea corre todos los días y actúa solo los lunes.
    if not force and igs.today_miami().weekday() != 0:
        log.info('Hoy no es lunes en Miami: no se genera el reporte.')
        return
    if not igs.list_accounts(org_id):
        log.info('Sin cuentas conectadas: no hay reporte.')
        return
    rid = igs.generate_weekly(org_id)
    log.info('Reporte semanal listo (id %s)', rid)
    try:
        log.info('Enviado a %s', igs.email_report(org_id))
    except Exception:
        log.exception('No se pudo enviar el reporte por email (queda en el CRM)')


def main(argv):
    migrate()
    cmd = argv[1] if len(argv) > 1 else 'daily'
    if cmd == 'daily':
        daily()
    elif cmd == 'weekly':
        weekly(force='--force' in argv)
    elif cmd == 'pending':
        log.info('Pendientes: %s', igs.process_pending())
    elif cmd == 'hourly':
        log.info('Pendientes: %s', igs.process_pending())
        log.info('Sync: %s', igs.sync_all())
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
