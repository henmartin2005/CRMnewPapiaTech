"""
services/n8n_bot.py
Conecta el WhatsApp del CRM con un bot en n8n.

Flujo:
  1. Llega un WhatsApp (webhook de Zernio) → el CRM lo guarda.
  2. Si el bot está activo para ese chat, el CRM hace POST a N8N_WEBHOOK_URL con
     el mensaje, el historial reciente y lo que ya sabemos del cliente.
  3. n8n contesta de una de dos formas (se pueden combinar):
       a) En la misma respuesta HTTP (nodo "Respond to Webhook"):
            {"reply": "texto", "lead": {...}, "handoff": false}
       b) Más tarde, llamando al CRM:
            POST /api/whatsapp/bot/reply   {"phone", "message", "lead"?, "handoff"?}
            POST /api/whatsapp/bot/lead    {"phone", "first_name", "email", ...}
          con el header  X-Papia-Bot-Secret: <N8N_BOT_SECRET>

Variables de entorno (.env):
  N8N_WEBHOOK_URL            URL de producción del nodo Webhook de n8n
  N8N_BOT_SECRET             secreto compartido (el CRM lo envía y lo exige)
  N8N_BOT_ENABLED            1 = activo (default 1 si hay URL), 0 = apagado global
  N8N_TIMEOUT                segundos a esperar respuesta síncrona (default 20)
  N8N_PAUSE_ON_AGENT_REPLY   1 = si tú respondes desde el CRM, el bot se pausa en ese chat (default 1)
  N8N_HISTORY_LIMIT          mensajes de historial que se envían (default 12)
"""
import hmac
import logging
import os

import requests

log = logging.getLogger(__name__)


def webhook_url() -> str:
    return os.getenv('N8N_WEBHOOK_URL', '').strip()


def secret() -> str:
    return os.getenv('N8N_BOT_SECRET', '').strip()


def _flag(name, default='1') -> bool:
    return os.getenv(name, default).strip().lower() in ('1', 'true', 'yes', 'on', 'si', 'sí')


def is_enabled() -> bool:
    return bool(webhook_url()) and _flag('N8N_BOT_ENABLED')


def pause_on_agent_reply() -> bool:
    return _flag('N8N_PAUSE_ON_AGENT_REPLY')


def history_limit() -> int:
    try:
        return max(0, int(os.getenv('N8N_HISTORY_LIMIT', '12')))
    except ValueError:
        return 12


def check_secret(provided: str) -> bool:
    """Valida el header de las llamadas de n8n al CRM. Sin secreto configurado se rechaza todo."""
    s = secret()
    return bool(s) and hmac.compare_digest(s, (provided or '').strip())


def notify(payload: dict):
    """
    Envía el mensaje entrante a n8n. Devuelve el JSON de respuesta (dict) o None.
    Nunca lanza: si n8n falla, el CRM sigue funcionando normal.
    """
    url = webhook_url()
    if not url:
        return None
    try:
        timeout = float(os.getenv('N8N_TIMEOUT', '20'))
    except ValueError:
        timeout = 20.0
    headers = {'Content-Type': 'application/json'}
    if secret():
        headers['X-Papia-Bot-Secret'] = secret()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        log.warning('n8n bot: no se pudo contactar (%s)', exc)
        return None
    if resp.status_code >= 400:
        log.warning('n8n bot: HTTP %s %s', resp.status_code, resp.text[:300])
        return None
    try:
        data = resp.json()
    except ValueError:
        text = (resp.text or '').strip()
        return {'reply': text} if text else None
    # n8n a veces devuelve una lista con un item
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict) and isinstance(data.get('json'), dict):
        data = data['json']
    return data if isinstance(data, dict) else None
