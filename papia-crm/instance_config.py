"""
Configuración por instancia del CRM (plantilla).

Cada instalación (un cliente = una web app) tiene su propia carpeta `instance/`
(ignorada por git) con:

    instance/brand.json         marca: nombre, dueño, logo, colores, zona horaria, contacto
    instance/business.json      negocio: etapas del pipeline, servicios, módulos, horario del bot
    instance/agent_prompt.md    prompt del asistente de WhatsApp (Claude)
    instance/business_info.md   información del negocio que el bot usa como fuente de verdad

Si un archivo o una clave no existe en `instance/`, se usa el valor de
`config_defaults/`. Los JSON se mezclan clave por clave, así que en `instance/`
basta con poner lo que cambia.

La ruta de la carpeta se puede cambiar con la variable de entorno INSTANCE_DIR.
Los archivos se recargan solos cuando cambian (no hace falta reiniciar).
"""
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULTS_DIR = BASE_DIR / 'config_defaults'


def instance_dir() -> Path:
    return Path(os.getenv('INSTANCE_DIR') or (BASE_DIR / 'instance'))


_cache = {}


def _read(path: Path):
    """Lee un archivo con caché por mtime."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    content = path.read_text(encoding='utf-8')
    _cache[path] = (mtime, content)
    return content


def _merge(base, extra):
    if not isinstance(base, dict) or not isinstance(extra, dict):
        return extra
    out = dict(base)
    for k, v in extra.items():
        out[k] = _merge(base.get(k), v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def _json(name):
    data = {}
    for folder in (DEFAULTS_DIR, instance_dir()):
        raw = _read(folder / name)
        if raw:
            data = _merge(data, json.loads(raw))
    return data


def text(name, default=''):
    """Archivo de texto de la instancia (o el de config_defaults si no existe)."""
    for folder in (instance_dir(), DEFAULTS_DIR):
        raw = _read(folder / name)
        if raw is not None:
            return raw
    return default


# ── Marca ────────────────────────────────────────────────────────────────────

def brand():
    b = _json('brand.json')
    # Variables de entorno tienen prioridad para lo que depende del despliegue
    if os.getenv('APP_TIMEZONE'):
        b['timezone'] = os.getenv('APP_TIMEZONE')
    if os.getenv('COMPANY_NAME'):
        b['legal_name'] = os.getenv('COMPANY_NAME')
    if os.getenv('PAYMENT_ZELLE_EMAIL'):
        b['payment_email'] = os.getenv('PAYMENT_ZELLE_EMAIL')
    b.setdefault('company_name', 'Mi Empresa')
    b.setdefault('legal_name', b['company_name'])
    b.setdefault('short_name', b['company_name'])
    b.setdefault('crm_name', 'CRM')
    b.setdefault('owner_name', '')
    b.setdefault('owner_first_name', (b.get('owner_name') or '').split(' ')[0])
    b.setdefault('timezone', 'America/New_York')
    b.setdefault('city', '')
    b['website_host'] = re.sub(r'^https?://', '', (b.get('website') or '')).rstrip('/')
    b['whatsapp_number'] = re.sub(r'\D', '', b.get('whatsapp_number') or '')
    b['proposal_whatsapp_number'] = re.sub(r'\D', '', b.get('proposal_whatsapp_number') or '') or b['whatsapp_number']
    b['tz_label'] = f"hora de {b['city']}" if b.get('city') else 'hora local'
    return b


def get(key, default=''):
    return brand().get(key) or default


def timezone_name():
    return brand().get('timezone') or 'America/New_York'


def base_url():
    """URL pública del CRM: APP_BASE_URL del .env o, si falta, la del request actual."""
    env = (os.getenv('APP_BASE_URL') or '').strip()
    if env:
        return env.rstrip('/')
    try:
        from flask import has_request_context, request
        if has_request_context():
            return request.url_root.rstrip('/')
    except Exception:
        pass
    return ''


def cors_origins():
    env = os.getenv('CORS_ORIGINS', '').strip()
    if env:
        return [o.strip() for o in env.split(',') if o.strip()]
    return brand().get('cors_origins') or []


# ── Negocio ──────────────────────────────────────────────────────────────────

def business():
    return _json('business.json')


def pipeline_stages():
    """[(key, label), ...]"""
    return [(s['key'], s['label']) for s in business().get('pipeline_stages', [])]


def stage_colors():
    return {s['key']: s.get('color', 'secondary') for s in business().get('pipeline_stages', [])}


def project_types():
    return [(p['key'], p['label']) for p in business().get('project_types', [])]


def service_keywords():
    """Palabras que el bot reconoce → clave de project_types."""
    out = {}
    for p in business().get('project_types', []):
        out[p['key'].lower()] = p['key']
        for kw in p.get('keywords', []):
            out[kw.lower()] = p['key']
    return out


def modules():
    return list(business().get('modules', []))


def calendar_settings():
    return business().get('calendar', {})


# ── Plantillas de texto ─────────────────────────────────────────────────────

_VAR = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')


def render(template, **extra):
    """Reemplaza {{variable}} con valores de la marca o de `extra`."""
    values = {k: v for k, v in brand().items() if isinstance(v, (str, int, float))}
    values.update(extra)
    return _VAR.sub(lambda m: str(values.get(m.group(1), m.group(0))), template or '')


def render_text(name, **extra):
    return render(text(name), **extra)


# ── Firma electrónica (módulo Contratos) ────────────────────────────────────

def _luminance(hex_color):
    h = (hex_color or '').lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    try:
        rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return 0.0
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def esign():
    """Marca del módulo de firma: nombre y colores de las páginas del firmante,
    los emails y el PDF. Se configura en brand.json → esign_name y colors.esign_*."""
    b = brand()
    c = b.get('colors') or {}
    dark = c.get('esign_dark') or c.get('sidebar_bg') or '#0B0E14'
    cta = c.get('esign_cta') or c.get('accent') or '#2A5BFF'
    link = c.get('esign_link') or (cta if _contrast(cta, '#FFFFFF') >= 4.5 else dark)
    return {
        'name': b.get('esign_name') or f"{b.get('short_name') or b.get('company_name')} Sign",
        'dark': dark,
        'cta': cta,
        'on_cta': '#FFFFFF' if _contrast(cta, '#FFFFFF') >= _contrast(cta, dark) else dark,
        'link': link,
        'logo': b.get('logo_white_url') or '',
        'logo_light': b.get('logo_url') or '',
        'company': b.get('company_name') or '',
        'legal_name': b.get('legal_name') or b.get('company_name') or '',
    }
