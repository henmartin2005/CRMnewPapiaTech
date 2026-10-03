"""
zernio_setup.py — configuración y diagnóstico de WhatsApp vía Zernio.

Uso (desde la carpeta papia-crm con el venv activo):
    python zernio_setup.py accounts          # lista cuentas y muestra el accountId de WhatsApp
    python zernio_setup.py number            # estado del número en Meta (nombre, calidad, tier)
    python zernio_setup.py templates         # plantillas y su estado de aprobación
    python zernio_setup.py webhook           # registra <APP_BASE_URL>/webhook/zernio
    python zernio_setup.py test +1786XXXXXXX # envía un texto de prueba (requiere que ese número te haya escrito)
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

from services import zernio  # noqa: E402
import instance_config as cfg  # noqa: E402


def pretty(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_accounts():
    data = zernio.list_accounts()
    accounts = data.get('accounts') or data.get('data') or []
    wa = [a for a in accounts if a.get('platform') == 'whatsapp']
    for a in accounts:
        print(f"- {a.get('platform'):10} {a.get('_id') or a.get('id')}  "
              f"{a.get('displayName') or a.get('username') or ''}  "
              f"[{a.get('platformStatus') or a.get('status') or ''}]")
    if wa:
        acc = wa[0].get('_id') or wa[0].get('id')
        print(f"\nPon en .env:\nZERNIO_WA_ACCOUNT_ID={acc}")
    else:
        print('\nNo hay cuenta de WhatsApp conectada en Zernio.')


def cmd_number():
    pretty(zernio.number_info())


def cmd_templates():
    data = zernio.list_templates()
    for t in data.get('templates') or data.get('data') or []:
        print(f"- {t.get('name'):30} {t.get('language'):6} {t.get('category',''):12} {t.get('status')}")


def cmd_webhook():
    base = cfg.base_url()
    if not base:
        sys.exit('Define APP_BASE_URL en .env (ej. https://crm.tucliente.com).')
    secret = os.getenv('ZERNIO_WEBHOOK_SECRET', '').strip()
    if not secret:
        sys.exit('Define ZERNIO_WEBHOOK_SECRET en .env antes de registrar el webhook.')
    pretty(zernio.create_webhook(f'{base}/webhook/zernio', secret))


def cmd_test(phone):
    conv = zernio.find_conversation_id(phone)
    if not conv:
        sys.exit('Ese número no tiene conversación abierta: escríbele primero desde tu celular '
                 'al número del negocio, o usa una plantilla.')
    pretty(zernio.send_text(conv, f"Prueba desde el CRM de {cfg.get('company_name')} ✅"))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    commands = {'accounts': cmd_accounts, 'number': cmd_number, 'templates': cmd_templates,
                'webhook': cmd_webhook, 'test': lambda: cmd_test(args[0])}
    if cmd not in commands or (cmd == 'test' and not args):
        sys.exit(__doc__)
    try:
        commands[cmd]()
    except zernio.ZernioError as exc:
        sys.exit(f'Zernio respondió {exc.status}: {exc}')
