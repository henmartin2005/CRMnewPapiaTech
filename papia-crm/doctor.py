"""
doctor.py — revisa que la instancia esté bien configurada.

Uso (desde papia-crm, con el venv activo):
    python doctor.py

No modifica nada. Muestra ✔ / ⚠ / ✗ por cada punto.
"""
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

try:
    from dotenv import load_dotenv
    load_dotenv(HERE / '.env', override=True)
except ImportError:
    pass

import instance_config as cfg  # noqa: E402

REQUIRED_STAGES = {'new_lead', 'contacted', 'proposal_sent', 'active_client', 'recurring'}
PLACEHOLDER_HINTS = ('example.com', 'Mi Empresa', 'Nombre Apellido', 'tucliente.com', 'XXXXXXXX')

problems = 0
warnings = 0


def ok(msg):
    print(f'  ✔ {msg}')


def warn(msg):
    global warnings
    warnings += 1
    print(f'  ⚠ {msg}')


def bad(msg):
    global problems
    problems += 1
    print(f'  ✗ {msg}')


def env(key):
    return (os.getenv(key) or '').strip()


print('\n── Configuración de la instancia ──')
inst = cfg.instance_dir()
if inst.is_dir():
    ok(f'Carpeta de instancia: {inst}')
else:
    bad(f'No existe {inst} — corre ./setup.sh o copia config_defaults/ a instance/')

for name in ('brand.json', 'business.json'):
    path = inst / name
    if path.exists():
        try:
            json.loads(path.read_text(encoding='utf-8'))
            ok(f'{name} válido')
        except Exception as exc:
            bad(f'{name} no es JSON válido: {exc}')
    else:
        warn(f'{name} no está en instance/ (se usan los valores genéricos)')

b = cfg.brand()
for key in ('company_name', 'legal_name', 'owner_name', 'website', 'contact_email'):
    val = str(b.get(key) or '')
    if not val:
        bad(f'brand.json → {key} vacío')
    elif any(h in val for h in PLACEHOLDER_HINTS):
        warn(f'brand.json → {key} sigue con el valor de ejemplo ({val})')
try:
    ZoneInfo(cfg.timezone_name())
    ok(f'Zona horaria: {cfg.timezone_name()}')
except Exception:
    bad(f'Zona horaria inválida: {cfg.timezone_name()}')
if not b.get('whatsapp_number'):
    warn('brand.json → whatsapp_number vacío (links de WhatsApp y aceptación de propuestas)')

keys = {s['key'] for s in cfg.business().get('pipeline_stages', [])}
missing = REQUIRED_STAGES - keys
if missing:
    bad(f'business.json → faltan etapas obligatorias: {", ".join(sorted(missing))}')
else:
    ok(f'Pipeline: {len(keys)} etapas')
types = [k for k, _ in cfg.project_types()]
if cfg.business().get('default_project_type', 'other') not in types:
    bad('business.json → default_project_type no está en project_types')
else:
    ok(f'Servicios: {", ".join(types)}')

info = cfg.render_text('business_info.md')
if '(describe aquí' in info:
    warn('business_info.md sigue con el texto de ejemplo: el bot no sabrá qué vende el negocio')

print('\n── .env ──')
if not (HERE / '.env').exists():
    bad('No existe .env (cp .env.example .env o ./setup.sh)')
for key in ('SECRET_KEY', 'ADMIN_PASSWORD', 'VAULT_KEY'):
    (ok if env(key) else bad)(f'{key} {"definida" if env(key) else "VACÍA"}')
base = env('APP_BASE_URL')
if not base or 'tucliente.com' in base:
    bad('APP_BASE_URL sin definir (webhooks y links de pago la necesitan)')
else:
    ok(f'APP_BASE_URL = {base}')

groups = {
    'Gmail/Calendar': ('GMAIL_CLIENT_ID', 'GMAIL_CLIENT_SECRET', 'GMAIL_REDIRECT_URI'),
    'Claude (bot)': ('ANTHROPIC_API_KEY',),
    'Stripe': ('STRIPE_SECRET_KEY', 'STRIPE_WEBHOOK_SECRET'),
}
provider = env('WHATSAPP_PROVIDER') or 'zernio'
groups[f'WhatsApp ({provider})'] = (
    ('ZERNIO_API_KEY', 'ZERNIO_WA_ACCOUNT_ID', 'ZERNIO_WEBHOOK_SECRET') if provider == 'zernio'
    else ('TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_WHATSAPP_NUMBER'))
for label, ks in groups.items():
    empty = [k for k in ks if not env(k)]
    if empty:
        warn(f'{label}: falta {", ".join(empty)}')
    else:
        ok(f'{label} configurado')
redirect = env('GMAIL_REDIRECT_URI')
if base and redirect and not redirect.startswith(base):
    warn(f'GMAIL_REDIRECT_URI ({redirect}) no coincide con APP_BASE_URL')

if base:
    print('\n── URLs para registrar en cada servicio ──')
    print(f'  Google OAuth redirect : {base}/emails/oauth2callback')
    print(f'  Zernio webhook        : {base}/webhook/zernio   (python zernio_setup.py webhook)')
    print(f'  Twilio webhook        : {base}/webhook/whatsapp')
    print(f'  Meta webhook          : {base}/webhook/meta')
    print(f'  Stripe webhook        : {base}/webhook/stripe')
    print(f'  Leads (POST)          : {base}/api/new-lead')

print(f'\nResultado: {problems} error(es), {warnings} aviso(s).\n')
sys.exit(1 if problems else 0)
