"""Renderowanie placeholderow w skladni n8n.

Dzieki temu prompt z n8n mozna wkleic do panelu BEZ ZMIAN:
    {{ $json.name }}
    {{ $json.description || 'brak' }}
    {{ $json.link_rewrite || $json.slug || '' }}

Obslugiwane: odwolania $json.pole, operator ||, literaly w apostrofach
i cudzyslowach. Nieznane pole traktujemy jak puste (jak w n8n).
"""
import re

EXPR_RE = re.compile(r"\{\{(.+?)\}\}", re.DOTALL)


def _resolve_token(token: str, data: dict) -> str | None:
    """Zwraca wartosc tokenu albo None, gdy pusty/nieistniejacy."""
    token = token.strip()
    if not token:
        return None

    # literal w cudzyslowie / apostrofie
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        literal = token[1:-1]
        return literal if literal else None

    # $json.pole  (takze json.pole i samo pole)
    m = re.match(r"^\$?json\.([A-Za-z_][\w]*)$", token)
    key = m.group(1) if m else (token if re.match(r"^[A-Za-z_][\w]*$", token) else None)
    if key is None:
        return None

    value = data.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def render_n8n_template(template: str, data: dict) -> str:
    """Podstawia wartosci pod wyrazenia {{ ... }}."""
    def _sub(match: re.Match) -> str:
        expr = match.group(1)
        for part in expr.split("||"):
            value = _resolve_token(part, data)
            if value is not None:
                return value
        return ""
    return EXPR_RE.sub(_sub, template)


def has_n8n_placeholders(template: str) -> bool:
    return bool(EXPR_RE.search(template))
