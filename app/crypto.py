"""Szyfrowanie danych logowania sklepow w spoczynku.

Klucz webservice PrestaShop daje pelny dostep do sklepu klienta,
wiec nigdy nie trafia do bazy ani do logow jako plaintext.
"""
from cryptography.fernet import Fernet


def generate_key() -> str:
    """Jednorazowo: wygeneruj klucz i wstaw do .env jako FERNET_KEY."""
    return Fernet.generate_key().decode()


def encrypt(plaintext: str, fernet_key: str) -> bytes:
    return Fernet(fernet_key.encode()).encrypt(plaintext.encode())


def decrypt(token: bytes, fernet_key: str) -> str:
    return Fernet(fernet_key.encode()).decrypt(token).decode()
