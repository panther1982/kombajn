"""Bramka jakosci przed zapisem. Port validate_generated_content z Twojego skryptu."""

PRODUCT_TYPES = ["pierscionek", "kolczyki", "naszyjnik", "bransoletka",
                 "broszka", "zawieszka", "lancuszek", "komplet",
                 "pierścionek", "łańcuszek"]

MAIN_TYPES = ["kolczyki", "pierścionek", "pierscionek", "naszyjnik", "bransoletka"]


class ValidationError(Exception):
    pass


def validate_generated_content(product_name: str, description_html: str, meta: dict) -> None:
    name_lower = (product_name or "").lower()
    desc_lower = (description_html or "").lower()
    short_lower = (meta.get("description_short") or "").lower()
    meta_lower = (meta.get("meta_title", "") + " " + meta.get("meta_description", "")).lower()

    if len(description_html) < 800:
        raise ValidationError("Opis jest zbyt krotki (<800 znakow).")
    if "<h2" not in desc_lower:
        raise ValidationError("Opis nie zawiera naglowka H2.")

    detected = next((t for t in PRODUCT_TYPES if t in name_lower), None)
    if detected:
        if detected not in desc_lower:
            raise ValidationError(f"Opis nie zawiera typu produktu: {detected}")
        if detected not in short_lower:
            raise ValidationError(f"Krotki opis nie zawiera typu produktu: {detected}")
        if detected not in meta_lower:
            raise ValidationError(f"Meta dane nie zawieraja typu produktu: {detected}")

    forbidden = [t for t in MAIN_TYPES if t not in name_lower]
    for word in forbidden:
        if word in short_lower:
            raise ValidationError(f"Podejrzenie pomylki produktu w krotkim opisie: {word}")


# ---------------------------------------------------------------------------
# Walidacja wyniku z promptu jednoetapowego (JSON)
# Zgodna z regulami produkcyjnego promptu: dozwolone TYLKO <p>, <ul>, <li>,
# zadnych naglowkow, zadnego Markdown.
# ---------------------------------------------------------------------------

import re as _re

ALLOWED_TAGS = {"p", "ul", "li"}
REQUIRED_FIELDS = ["description", "description_short", "meta_title",
                   "meta_description", "alt"]

# limity SEO z promptu (twarde limity PrestaShop sa wyzsze)
SOFT_LIMITS = {"meta_title": 255, "meta_description": 512, "alt": 125}


def _visible_text(html: str) -> str:
    return " ".join(_re.sub(r"<[^>]+>", " ", html or "").split())


def validate_single_output(fields: dict, min_visible: int = 400) -> None:
    """Walidacja wyniku jednoetapowego. Rzuca ValidationError."""
    missing = [f for f in REQUIRED_FIELDS if not (fields.get(f) or "").strip()]
    if missing:
        raise ValidationError(f"Brak wymaganych pol: {', '.join(missing)}")

    html = fields["description"]

    used = {t.lower() for t in _re.findall(r"</?([A-Za-z][A-Za-z0-9]*)", html)}
    forbidden = used - ALLOWED_TAGS
    if forbidden:
        raise ValidationError(
            f"Niedozwolone tagi w opisie: {', '.join(sorted(forbidden))} "
            f"(dozwolone: p, ul, li)")

    text = _visible_text(html)
    if len(text) < min_visible:
        raise ValidationError(
            f"Opis za krotki: {len(text)} znakow widocznego tekstu (min {min_visible})")

    if "```" in html or _re.search(r"^\s*#{1,6}\s", html, _re.M):
        raise ValidationError("Opis zawiera Markdown zamiast czystego HTML.")

    for field, limit in SOFT_LIMITS.items():
        value = fields.get(field) or ""
        if len(value) > limit:
            raise ValidationError(f"Pole {field} przekracza limit {limit} znakow")

    short = fields["description_short"]
    if "<" in short and ">" in short:
        raise ValidationError("description_short musi byc czystym tekstem (bez HTML)")

    validate_no_quantity(fields)


# ---------------------------------------------------------------------------
# Kontrola liczby sztuk (regula 15/16 promptu produkcyjnego)
# Liczba sztuk moze wystapic WYLACZNIE w polu `name`, nigdzie indziej.
# ---------------------------------------------------------------------------

_NUM_WORDS = (r"jedn[aey]|dw[ai]e?|trzy|czter[yh]|pi[eę][cć]|sze[sś][cć]|siedem|osiem|"
              r"dziewi[eę][cć]|dziesi[eę][cć]|kilka|par[aeęy]|\d+")
_UNITS = r"sztuk\w*|szt\.?|element\w*|par[aeyię]\w*|kompletn?\w*|zestaw\w*"

QUANTITY_RE = _re.compile(rf"\b({_NUM_WORDS})\s+({_UNITS})", _re.IGNORECASE)
# takze odwrotna kolejnosc: "sztuk 3", "zestaw 6 par"
QUANTITY_RE_REV = _re.compile(rf"\b({_UNITS})\s+({_NUM_WORDS})\b", _re.IGNORECASE)

QUANTITY_CHECKED_FIELDS = ["description", "description_short", "meta_title",
                           "meta_description", "alt", "title"]


def find_quantity_mentions(fields: dict) -> list[str]:
    """Zwraca liste 'pole: fragment' tam, gdzie pojawia sie liczba sztuk."""
    hits = []
    for field in QUANTITY_CHECKED_FIELDS:
        text = _visible_text(fields.get(field) or "")
        for rx in (QUANTITY_RE, QUANTITY_RE_REV):
            m = rx.search(text)
            if m:
                hits.append(f"{field}: '{m.group(0)}'")
                break
    return hits


def validate_no_quantity(fields: dict) -> None:
    hits = find_quantity_mentions(fields)
    if hits:
        raise ValidationError(
            "Liczba sztuk/elementow poza polem name (regula 15): " + "; ".join(hits))
