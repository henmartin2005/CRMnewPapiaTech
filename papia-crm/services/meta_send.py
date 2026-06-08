"""
services/meta_send.py
Envío de respuestas SALIENTES vía Send API de Meta (Messenger + Instagram).

Regla de negocio: solo puedes responder libremente dentro de la ventana de
24h desde el último mensaje del usuario. Fuera de eso, Meta lo rechaza.
"""
import requests

GRAPH = "https://graph.facebook.com/v21.0"


def send_message(page_access_token: str, recipient_id: str, text: str) -> dict:
    """
    page_access_token: el token de channel_connections de esa org.
    recipient_id:      PSID (Messenger) o IGSID (Instagram).
    Mismo endpoint sirve para ambos canales.
    """
    url = f"{GRAPH}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "messaging_type": "RESPONSE",
        "message": {"text": text},
    }
    resp = requests.post(
        url,
        params={"access_token": page_access_token},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()
