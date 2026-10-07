"""
Papia Sign — tarea diaria: vence sobres pasados de fecha y envía recordatorios.

PythonAnywhere → Tasks → Scheduled tasks (diaria, p. ej. 13:00 UTC):
    cd /home/henmartin2005/CRMnewPapiaTech/papia-crm && /home/henmartin2005/CRMnewPapiaTech/.venv/bin/python contracts_cron.py
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'), override=True)
except ImportError:
    pass

from models import contract as C           # noqa: E402
from services import contract_notify as N   # noqa: E402


def main():
    C.ensure_schema()
    expired = C.expire_overdue()
    for env_id, org_id in expired:
        env = C.get_envelope(env_id, org_id)
        N.notify_sender(env, f'Vencido: {env["title"]}',
                        [f'El sobre "{env["title"]}" venció sin todas las firmas.',
                         'Puedes enviarlo de nuevo desde Contratos → Nuevo sobre.'])

    reminded = 0
    for item in C.due_reminders():
        env = C.get_envelope(item['env_id'], item['org_id'])
        rc = next((r for r in env['recipients'] if r['id'] == item['rid']), None)
        if not C.is_turn(rc):
            continue
        token = C.issue_token(rc['id'])     # enlace nuevo (el anterior deja de funcionar)
        if N.invite(env, rc, token, env.get('created_by_name') or N.org_name(env['org_id']), reminder=True):
            reminded += 1
        C.touch_reminder(rc['id'])
    print(f'Papia Sign: {len(expired)} vencido(s), {reminded} recordatorio(s) enviados')


if __name__ == '__main__':
    main()
