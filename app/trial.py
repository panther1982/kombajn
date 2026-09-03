"""Darmowe kredyty na test, z ograniczeniem per domena sklepu.

Adresow e-mail mozna zalozyc dowolnie wiele, wiec liczenie prob po mailu
niczego nie chroni. Ograniczamy po domenie podlaczonego sklepu: jeden sklep
= jeden test, niezaleznie od liczby kont.
"""
from __future__ import annotations
import os
from urllib.parse import urlparse

from app import credits


def domyslna_pula() -> int:
    """Ile kredytow na test. 0 wylacza mechanizm."""
    try:
        return max(0, int(os.environ.get("TRIAL_CREDITS", "10")))
    except ValueError:
        return 10


def normalizuj_domene(base_url: str) -> str:
    """https://Sklep.Merebilo.eu/ -> merebilo.eu

    Obcinamy 'www' i poddomeny sklepowe, zeby 'sklep.x.pl' i 'test.x.pl'
    liczyly sie jako ta sama firma. Zostawiamy dwa ostatnie czlony,
    a dla domen typu 'co.uk' - trzy.
    """
    host = (urlparse(base_url or "").hostname or "").lower().strip(".")
    if not host:
        return ""
    czlony = host.split(".")
    zlozone = {"com", "co", "net", "org", "gov", "edu"}
    if len(czlony) >= 3 and czlony[-2] in zlozone and len(czlony[-1]) == 2:
        return ".".join(czlony[-3:])      # np. sklep.firma.com.pl -> firma.com.pl
    return ".".join(czlony[-2:]) if len(czlony) >= 2 else host


def czy_juz_wykorzystana(conn, domena: str) -> dict | None:
    """Zwraca wpis, gdy ta domena juz dostala darmowe kredyty."""
    if not domena:
        return None
    r = conn.execute(
        "SELECT tenant_id, credits, granted_at FROM trial_grants WHERE domain = %s",
        (domena,)).fetchone()
    return dict(r) if r else None


def przyznaj_jesli_mozna(conn, tenant_id: int, base_url: str) -> tuple[int, str]:
    """Przyznaje kredyty na test przy podlaczeniu sklepu.

    Zwraca (ile_przyznano, komunikat). 0 oznacza, ze nie przyznano -
    komunikat mowi dlaczego.
    """
    pula = domyslna_pula()
    if pula == 0:
        return 0, ""

    t = conn.execute("SELECT trial_granted FROM tenants WHERE id = %s",
                     (tenant_id,)).fetchone()
    if t and t["trial_granted"]:
        return 0, ""            # ten klient juz dostal - cicho, bez komunikatu

    domena = normalizuj_domene(base_url)
    if not domena:
        return 0, ""

    zajete = czy_juz_wykorzystana(conn, domena)
    if zajete:
        return 0, (f"Darmowe kredyty dla domeny {domena} zostaly juz wykorzystane. "
                   f"Skontaktuj sie z obsluga, jesli to pomylka.")

    conn.execute(
        "INSERT INTO trial_grants (domain, tenant_id, credits) VALUES (%s,%s,%s) "
        "ON CONFLICT (domain) DO NOTHING", (domena, tenant_id, pula))
    conn.execute("UPDATE tenants SET trial_granted = true WHERE id = %s", (tenant_id,))
    credits.topup(conn, tenant_id, pula, reason=f"test dla domeny {domena}")
    return pula, f"Przyznano {pula} kredytow na test dla domeny {domena}."
