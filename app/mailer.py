"""Wysylka maili przez SMTP (konfiguracja w .env).

Gdy SMTP nie jest skonfigurowany, mail nie jest wysylany, a tresc trafia do
logu - dzieki temu mechanizm da sie przetestowac bez podpinania poczty.
"""
from __future__ import annotations
import os
import smtplib
import ssl
from email.message import EmailMessage


class MailError(RuntimeError):
    pass


def _cfg() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "587") or 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from": (os.environ.get("SMTP_FROM", "").strip()
                 or os.environ.get("SMTP_USER", "").strip()),
        "from_name": os.environ.get("SMTP_FROM_NAME", "Kombajn").strip(),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["user"] and c["password"])


def send(to: str, subject: str, body: str) -> bool:
    """Wysyla mail. Zwraca True, gdy poszedl; False = tryb bez SMTP (log)."""
    c = _cfg()
    if not configured():
        print(f"[mailer] SMTP nieskonfigurowany — mail do {to} NIE wyslany.\n"
              f"--- {subject} ---\n{body}\n---", flush=True)
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{c['from_name']} <{c['from']}>"
    msg["To"] = to
    msg.set_content(body)

    try:
        if c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=20,
                                  context=ssl.create_default_context()) as s:
                s.login(c["user"], c["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=20) as s:
                s.starttls(context=ssl.create_default_context())
                s.login(c["user"], c["password"])
                s.send_message(msg)
        return True
    except Exception as e:
        raise MailError(f"Nie udalo sie wyslac maila: {type(e).__name__}: {e}") from e


def invitation_body(link: str, tenant_name: str, invited_by: str) -> tuple[str, str]:
    """Zwraca (temat, tresc) zaproszenia."""
    subject = "Zaproszenie do panelu Kombajn"
    body = (
        f"Dzien dobry,\n\n"
        f"{invited_by} zaprasza Cie do panelu Kombajn — konto: {tenant_name}.\n\n"
        f"Ustaw haslo i aktywuj konto, klikajac w link:\n{link}\n\n"
        f"Link jest wazny przez 7 dni i mozna go uzyc raz.\n"
        f"Jesli nie spodziewales sie tej wiadomosci, po prostu ja zignoruj.\n"
    )
    return subject, body
