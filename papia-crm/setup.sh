#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════
#  setup.sh — instala una instancia nueva del CRM (pensado para PythonAnywhere)
#
#  Uso (desde la carpeta papia-crm):
#     ./setup.sh                      # instancia nueva con la configuración genérica
#     ./setup.sh --preset papiatech   # copia config_presets/papiatech a instance/
#     PYTHON=python3.11 ./setup.sh    # elige la versión de Python
#
#  Es seguro volver a ejecutarlo: no pisa instance/, .env ni la base de datos existentes.
# ═══════════════════════════════════════════════════════════════════════════
set -euo pipefail

cd "$(dirname "$0")"
HERE="$(pwd)"
VENV="$(dirname "$HERE")/.venv"
PRESET=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset) PRESET="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
echo "▸ Python: $($PY --version)"

# ── 1. Entorno virtual + dependencias ──────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
  echo "▸ Creando entorno virtual en $VENV"
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
echo "▸ Instalando dependencias (puede tardar unos minutos)…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# ── 2. Configuración de la instancia (marca, negocio, prompt del bot) ──────
if [[ -d instance ]]; then
  echo "▸ instance/ ya existe: no se toca"
else
  mkdir -p instance
  if [[ -n "$PRESET" ]]; then
    [[ -d "config_presets/$PRESET" ]] || { echo "✗ No existe config_presets/$PRESET"; exit 1; }
    cp config_presets/"$PRESET"/* instance/
    echo "▸ instance/ creada desde el preset '$PRESET'"
  else
    cp config_defaults/brand.json config_defaults/business.json \
       config_defaults/business_info.md config_defaults/agent_prompt.md instance/
    echo "▸ instance/ creada con la configuración genérica — EDÍTALA antes de abrir el CRM"
  fi
fi

# ── 3. .env con secretos generados ─────────────────────────────────────────
ADMIN_PASS_MSG=""
if [[ -f .env ]]; then
  echo "▸ .env ya existe: no se toca"
else
  cp .env.example .env
  ADMIN_PASS="$(python -c 'import secrets; print(secrets.token_urlsafe(12))')"
  python - "$ADMIN_PASS" <<'PY'
import re, secrets, sys
from cryptography.fernet import Fernet
path = '.env'
s = open(path, encoding='utf-8').read()
def put(key, value):
    global s
    s = re.sub(rf'^{key}=.*$', f'{key}={value}', s, flags=re.M)
put('SECRET_KEY', secrets.token_hex(32))
put('VAULT_KEY', Fernet.generate_key().decode())
put('ZERNIO_WEBHOOK_SECRET', secrets.token_urlsafe(32))
put('META_VERIFY_TOKEN', secrets.token_urlsafe(16))
put('ADMIN_PASSWORD', sys.argv[1])
open(path, 'w', encoding='utf-8').write(s)
PY
  chmod 600 .env
  ADMIN_PASS_MSG="   Usuario: admin   Contraseña: $ADMIN_PASS   (cámbiala en .env si quieres; guárdala ya)"
  echo "▸ .env creado con SECRET_KEY, VAULT_KEY y contraseña de admin generadas"
fi

# ── 4. Base de datos (se crea vacía al importar la app) ────────────────────
python -c "import app" >/dev/null
echo "▸ Base de datos lista"

# ── 5. Diagnóstico ─────────────────────────────────────────────────────────
python doctor.py || true

cat <<EOF

══════════════════════════════════════════════════════════════════════
 ✔ Instalación lista. Siguientes pasos:
──────────────────────────────────────────────────────────────────────
 1. Edita la marca y el negocio:   instance/brand.json, instance/business.json,
    instance/business_info.md y (opcional) instance/agent_prompt.md
 2. Completa .env: APP_BASE_URL, Gmail, WhatsApp (Zernio o Twilio),
    ANTHROPIC_API_KEY y Stripe.
 3. PythonAnywhere → Web → Add a new web app → Manual configuration
      Virtualenv:   $VENV
      Static files: /static/  →  $HERE/static
      WSGI file (reemplaza todo su contenido):
        import sys
        path = '$HERE'
        if path not in sys.path:
            sys.path.insert(0, path)
        from wsgi import application
 4. Reload. Vuelve a correr "python doctor.py" para revisar qué falta.
$ADMIN_PASS_MSG
 Guía completa: TEMPLATE.md
══════════════════════════════════════════════════════════════════════
EOF
