"""Instala Instagram Studio sobre una copia del CRM con cambios locales (sin merge ni checkout).

Uso (desde papia-crm, con el venv activo):
    python install_ig_studio.py            # aplica
    python install_ig_studio.py --check    # solo dice qué haría

Hace copia de seguridad de los archivos que toca en backups/ig_studio_<fecha>/ y es idempotente:
si un bloque ya está, no lo vuelve a poner.
  - app.py                  registra el blueprint y corre la migración
  - routes/meta_webhook.py  pasa los comentarios del webhook a Instagram Studio
  - templates/base.html     agrega la pestaña "Instagram Studio" al sidebar
  - .env                    IG_APP_ID (público) si falta
"""
import py_compile
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECK = '--check' in sys.argv
IG_APP_ID = next((a.split('=', 1)[1] for a in sys.argv if a.startswith('--ig-app-id=')), '')

APP_BLOCK = """
# ── Instagram Studio ──
from migrate_ig_studio import migrate as migrate_ig_studio
from routes.ig_studio import ig_studio_bp
migrate_ig_studio()
app.register_blueprint(ig_studio_bp)
"""

WEBHOOK_BLOCK = """    # ── Instagram Studio: comentarios (field=comments) → agente con Claude ──
    try:
        from services import ig_studio
        ig_studio.handle_webhook(data)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Instagram Studio: fallo al procesar el webhook')

"""

SIDEBAR_BLOCK = """
    {% if is_admin %}
    <li>
      <a href="{{ url_for('ig_studio.analitica') }}"
         class="sb-link {% if request.endpoint and request.endpoint.startswith('ig_studio.') %}active{% endif %}">
        <i class="bi bi-graph-up-arrow" style="color:#E1306C;"></i>
        <span class="sb-label">Instagram Studio</span>
      </a>
    </li>
    {% endif %}
"""


def patch_app(text):
    if 'ig_studio_bp' in text:
        return text, 'ya estaba'
    regs = list(re.finditer(r'^app\.register_blueprint\([^\n]*\)\n', text, re.M))
    if not regs:
        raise SystemExit('app.py: no encontré app.register_blueprint(...)')
    end = regs[-1].end()
    return text[:end] + APP_BLOCK + text[end:], 'blueprint registrado'


def patch_webhook(text):
    if 'ig_studio.handle_webhook' in text:
        return text, 'ya estaba'
    m = re.search(r'^def receive\(', text, re.M)
    if not m:
        raise SystemExit('meta_webhook.py: no encontré def receive(')
    nxt = re.search(r'^(?:def |@)', text[m.end():], re.M)
    body_end = m.end() + (nxt.start() if nxt else len(text) - m.end())
    rets = list(re.finditer(r'^    return jsonify\(\s*\{\s*[\'"]status[\'"]\s*:\s*[\'"]ok[\'"]\s*\}\s*\)[^\n]*\n',
                            text[m.end():body_end], re.M))
    if not rets:
        raise SystemExit("meta_webhook.py: no encontré el return jsonify({'status': 'ok'}) final de receive()")
    pos = m.end() + rets[-1].start()
    if 'data' not in text[m.end():pos]:
        raise SystemExit('meta_webhook.py: receive() no usa la variable data; revisa a mano')
    return text[:pos] + WEBHOOK_BLOCK + text[pos:], 'hook de comentarios agregado'


def patch_base(text):
    if 'ig_studio.analitica' in text:
        return text, 'ya estaba'
    i = text.find('<span class="sb-label">Instagram</span>')
    if i < 0:
        raise SystemExit('base.html: no encontré la entrada Instagram del sidebar')
    j = text.find('{% endif %}', i)
    if j < 0:
        raise SystemExit('base.html: no encontré el {% endif %} de la entrada Instagram')
    j = text.find('\n', j) + 1
    return text[:j] + SIDEBAR_BLOCK + text[j:], 'pestaña agregada al sidebar'


def patch_env(text):
    if re.search(r'^\s*IG_APP_ID\s*=', text, re.M):
        return text, 'IG_APP_ID ya estaba'
    if not IG_APP_ID:
        return text, 'falta IG_APP_ID (pásalo con --ig-app-id=...)'
    sep = '' if text.endswith('\n') or not text else '\n'
    return text + sep + f'IG_APP_ID={IG_APP_ID}\n', 'IG_APP_ID agregado'


def main():
    targets = [('app.py', patch_app), ('routes/meta_webhook.py', patch_webhook),
               ('templates/base.html', patch_base), ('.env', patch_env)]
    backup = HERE / 'backups' / f"ig_studio_{datetime.now():%Y%m%d_%H%M%S}"
    results = []
    for rel, fn in targets:
        path = HERE / rel
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        new, msg = fn(old)
        results.append((rel, msg, new != old))
        if new != old and not CHECK:
            backup.mkdir(parents=True, exist_ok=True)
            if path.exists():
                shutil.copy2(path, backup / rel.replace('/', '__'))
            path.write_text(new, encoding='utf-8')
    for rel in ('app.py', 'routes/meta_webhook.py', 'routes/ig_studio.py', 'services/ig_studio.py',
                'migrate_ig_studio.py', 'ig_studio_jobs.py'):
        py_compile.compile(str(HERE / rel), doraise=True)
    for rel, msg, changed in results:
        print(f"{'*' if changed else '-'} {rel}: {msg}{' (solo revisión)' if CHECK and changed else ''}")
    if not CHECK and any(c for _, _, c in results):
        print(f'Copia de seguridad: {backup}')
    print('OK')


if __name__ == '__main__':
    main()
