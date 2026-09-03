"""Automatyczne mapowanie kategorii: import ze sklepu + dopasowanie rozmyte.

Nazwy w plikach rzadko zgadzaja sie co do znaku z nazwami w sklepie
("Kolczyki Xuping" vs "Kolczyki Xuping Stal 316L", ogonki, wielkosc liter).
Modul normalizuje nazwy, liczy podobienstwo i dzieli wyniki na:
  - pewne   -> mozna zastosowac automatycznie,
  - do zatwierdzenia -> pokazujemy propozycje uzytkownikowi.
"""
from __future__ import annotations
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

from app.prestashop import PrestaShopClient

# progi podobienstwa
PEWNE = 0.90        # >= tego: dopasowanie uznajemy za pewne
PROPOZYCJA = 0.62   # >= tego: pokazujemy do zatwierdzenia; nizej pomijamy


def normalizuj(nazwa: str) -> str:
    """Do porownan: bez ogonkow, malymi literami, bez zbednych spacji."""
    s = unicodedata.normalize("NFKD", (nazwa or "").strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    for a, b in (("ł", "l"), ("ø", "o"), ("đ", "d")):
        s = s.replace(a, b)
    return " ".join(s.split())


def podobienstwo(a: str, b: str) -> float:
    """0..1. Premiuje przypadek, gdy jedna nazwa zawiera druga w calosci."""
    na, nb = normalizuj(a), normalizuj(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    baza = SequenceMatcher(None, na, nb).ratio()
    # "Kolczyki Xuping" vs "Kolczyki Xuping Stal 316L" - zawieranie sie
    if na in nb or nb in na:
        baza = max(baza, 0.90)
    # wspolne slowa
    sa, sb = set(na.split()), set(nb.split())
    if sa and sb:
        jaccard = len(sa & sb) / len(sa | sb)
        baza = max(baza, jaccard * 0.95)
    return round(baza, 3)


@dataclass
class Kategoria:
    id: int
    nazwa: str
    id_parent: int
    aktywna: bool


@dataclass
class Propozycja:
    source_name: str          # nazwa uzywana w plikach
    ps_category_id: int       # dopasowana kategoria w sklepie
    ps_name: str
    wynik: float              # podobienstwo 0..1
    pewne: bool


def pobierz_kategorie(base_url: str, auth_key: str) -> tuple[list[Kategoria], str]:
    """Pobiera drzewo kategorii ze sklepu. Zwraca (lista, komunikat_bledu)."""
    ps = PrestaShopClient(base_url, auth_key)
    out: list[Kategoria] = []
    try:
        r = ps._client.get(f"{ps.base}/categories",
                           params={"display": "[id,name,id_parent,active]"})
        if r.status_code == 401:
            return [], ("Klucz webservice nie ma uprawnienia 'categories: View'. "
                        "Wlacz je w panelu sklepu i sprobuj ponownie.")
        if r.status_code >= 400:
            return [], f"Sklep zwrocil HTTP {r.status_code} przy odczycie kategorii."
        for el in ET.fromstring(r.text).findall(".//category"):
            cid = el.findtext("id")
            lang = el.find(".//name/language")
            nazwa = (lang.text if lang is not None else el.findtext("name")) or ""
            if not cid or not nazwa.strip():
                continue
            akt = (el.findtext("active") or "1").strip() != "0"
            out.append(Kategoria(int(cid), nazwa.strip(),
                                 int(el.findtext("id_parent") or 0), akt))
    except Exception as e:
        return [], f"Blad odczytu kategorii: {type(e).__name__}"
    finally:
        ps.close()
    # pomijamy korzenie sklepu (Root/Home maja id <= 2)
    return [k for k in out if k.id > 2 and k.aktywna], ""


def dopasuj(nazwy_zrodlowe: list[str], kategorie: list[Kategoria]) -> list[Propozycja]:
    """Dla kazdej nazwy z plikow znajduje najlepsza kategorie w sklepie."""
    wynik: list[Propozycja] = []
    for src in nazwy_zrodlowe:
        najlepsza, punkty = None, 0.0
        for k in kategorie:
            p = podobienstwo(src, k.nazwa)
            if p > punkty:
                najlepsza, punkty = k, p
        if najlepsza and punkty >= PROPOZYCJA:
            wynik.append(Propozycja(src, najlepsza.id, najlepsza.nazwa,
                                    punkty, punkty >= PEWNE))
    return wynik
