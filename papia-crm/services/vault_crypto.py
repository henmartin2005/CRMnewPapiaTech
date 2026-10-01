"""
Encryption helpers for the per-client credential vault.

Credentials are encrypted at rest with Fernet (AES-128-CBC + HMAC-SHA256)
using a server-side key stored in the VAULT_KEY environment variable.
The 4-digit PIN is only an access gate (hashed + rate limited); it is NOT
the encryption key, because 10,000 combinations would be trivial to brute
force offline if someone ever copied the database file.

Generate a key once and put it in .env:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import json
import os

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - surfaced to the UI as "not configured"
    Fernet = None
    InvalidToken = Exception


class VaultNotConfigured(RuntimeError):
    pass


def _fernet():
    if Fernet is None:
        raise VaultNotConfigured("Falta la librería 'cryptography' (pip install cryptography).")
    key = os.getenv('VAULT_KEY', '').strip()
    if not key:
        raise VaultNotConfigured("Falta VAULT_KEY en el .env del servidor.")
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise VaultNotConfigured("VAULT_KEY no es una clave Fernet válida.") from exc


def is_configured() -> bool:
    try:
        _fernet()
        return True
    except VaultNotConfigured:
        return False


def encrypt_entry(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()


def decrypt_entry(token: str) -> dict:
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except InvalidToken:
        return {'platform': '[no se pudo descifrar — ¿cambió VAULT_KEY?]',
                'url': '', 'username': '', 'password': '', 'notes': ''}
