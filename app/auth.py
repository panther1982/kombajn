"""Logowanie: hasla (bcrypt), pobieranie uzytkownika, token CSRF.

Sesje trzyma SessionMiddleware (podpisane ciasteczko) skonfigurowane w web.py.
"""
import secrets

import bcrypt


# --- hasla -------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# --- uzytkownicy -------------------------------------------------------------

def get_user_by_email(conn, email: str) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE email = %s", (email.lower(),)).fetchone()


def get_user_by_id(conn, user_id: int) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()


def create_user(conn, tenant_id: int, email: str, password: str, role: str = "owner") -> int:
    row = conn.execute(
        "INSERT INTO users (tenant_id, email, password_hash, role) VALUES (%s,%s,%s,%s) RETURNING id",
        (tenant_id, email.lower(), hash_password(password), role),
    ).fetchone()
    return row["id"]


# --- CSRF --------------------------------------------------------------------

def ensure_csrf_token(session: dict) -> str:
    token = session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def check_csrf(session: dict, form_token: str | None) -> bool:
    expected = session.get("csrf")
    return bool(expected) and bool(form_token) and secrets.compare_digest(expected, form_token)



# ---------------------------------------------------------------------------
# Ochrona logowania przed brute-force (licznik w bazie, bez Redisa)
# ---------------------------------------------------------------------------

MAX_LOGIN_ATTEMPTS = 5
LOGIN_WINDOW_MINUTES = 15


def record_login_attempt(conn, ip: str, email: str, success: bool) -> None:
    conn.execute(
        "INSERT INTO login_attempts (ip, email, success) VALUES (%s, %s, %s)",
        (ip or "?", (email or "").lower(), success),
    )


def is_login_blocked(conn, ip: str, email: str) -> bool:
    """True, gdy z tego IP LUB na ten email bylo >= MAX nieudanych prob w oknie."""
    row = conn.execute(
        "SELECT count(*) AS n FROM login_attempts "
        "WHERE success = false "
        "  AND created_at > now() - (%s || ' minutes')::interval "
        "  AND (ip = %s OR email = %s)",
        (LOGIN_WINDOW_MINUTES, ip or "?", (email or "").lower()),
    ).fetchone()
    return (row["n"] if row else 0) >= MAX_LOGIN_ATTEMPTS


def clear_login_attempts(conn, ip: str, email: str) -> None:
    """Po udanym logowaniu kasujemy licznik dla tego IP/emaila."""
    conn.execute(
        "DELETE FROM login_attempts WHERE ip = %s OR email = %s",
        (ip or "?", (email or "").lower()),
    )
