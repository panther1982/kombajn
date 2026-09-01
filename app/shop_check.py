"""Diagnostyka polaczenia ze sklepem PrestaShop.

Sprawdza po kolei kazde uprawnienie klucza webservice, ktorego aplikacja
potrzebuje, i wykrywa ustawienia sklepu (regula podatkowa, cecha rozmiaru).
Kazdy brak = konkretna wskazowka, co wlaczyc w panelu sklepu - zamiast
zagadkowego HTTP 401/405 w trakcie pracy.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

from app.prestashop import PrestaShopClient


# (zasob, metoda, czy wymagane, do czego sluzy)
CHECKS = [
    ("products",              "GET",  True,  "odczyt produktow"),
    ("products",              "POST", True,  "tworzenie produktow ze zdjec"),
    ("products",              "PUT",  True,  "zapis opisow i SEO"),
    ("categories",            "GET",  True,  "przypisanie do kategorii"),
    ("images",                "GET",  True,  "odczyt zdjec do analizy AI"),
    ("images",                "POST", True,  "wgrywanie zdjec do produktow"),
    ("product_features",      "GET",  False, "odczyt cech (rozmiar)"),
    ("product_feature_values","GET",  False, "odczyt wartosci cech"),
    ("product_feature_values","POST", False, "zapis rozmiaru jako cechy"),
    ("manufacturers",         "GET",  False, "odczyt producentow (opcjonalne)"),
    ("tax_rule_groups",       "GET",  False, "wykrycie reguly podatkowej (VAT)"),
]


@dataclass
class CheckResult:
    resource: str
    method: str
    required: bool
    purpose: str
    ok: bool
    detail: str = ""


@dataclass
class ShopReport:
    reachable: bool = False
    connection_error: str = ""
    checks: list[CheckResult] = field(default_factory=list)
    tax_groups: list[tuple[str, str]] = field(default_factory=list)   # (id, nazwa)
    features: list[tuple[str, str]] = field(default_factory=list)     # (id, nazwa)
    suggested_tax_group: str | None = None
    suggested_size_feature: str | None = None
    tax_candidates: list = field(default_factory=list)  # reguly pasujace do 23%
    tax_readable: bool = True      # czy udalo sie odczytac liste regul podatkowych
    features_readable: bool = True  # czy udalo sie odczytac liste cech

    @property
    def blocking(self) -> list[CheckResult]:
        """Braki, ktore uniemozliwiaja prace."""
        return [c for c in self.checks if c.required and not c.ok]

    @property
    def optional_missing(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.required and not c.ok]

    @property
    def ready(self) -> bool:
        return self.reachable and not self.blocking


def _probe(ps: PrestaShopClient, resource: str, method: str) -> tuple[bool, str]:
    """Sprawdza jedno uprawnienie. Nie tworzy trwalych danych.

    GET  - zwykly odczyt.
    POST - proba z pustym/niepelnym cialem: 401/405 = brak uprawnienia,
           400/500 = uprawnienie JEST (serwer doszedl do walidacji danych).
    PUT  - jak POST, na nieistniejacym id.
    """
    url = f"{ps.base}/{resource}"
    try:
        if method == "GET":
            r = ps._client.get(url, params={"limit": "1"})
            return (r.status_code < 400, f"HTTP {r.status_code}")

        body = f'<?xml version="1.0" encoding="UTF-8"?><prestashop><{resource[:-1]}/></prestashop>'
        headers = {"Content-Type": "application/xml"}
        if method == "POST":
            r = ps._client.post(url, content=body.encode(), headers=headers)
        else:
            r = ps._client.put(f"{url}/999999999", content=body.encode(), headers=headers)

        # 401/405 = brak prawa; reszta oznacza, ze metoda jest dozwolona
        if r.status_code in (401, 405):
            return (False, f"HTTP {r.status_code} - brak uprawnienia")
        return (True, f"HTTP {r.status_code}")
    except Exception as e:
        return (False, f"{type(e).__name__}")


def _list_resource(ps: PrestaShopClient, resource: str, node: str) -> tuple[list[tuple[str, str]], bool]:
    """Zwraca ([(id, nazwa)], czy_odczyt_sie_powiodl)."""
    out: list[tuple[str, str]] = []
    try:
        r = ps._client.get(f"{ps.base}/{resource}", params={"display": "[id,name,active]"})
        if r.status_code >= 400:
            return out, False
        for el in ET.fromstring(r.text).findall(f".//{node}"):
            nid = el.findtext("id") or el.get("id") or ""
            # nazwa bywa prosta (<name>X</name>) albo wielojezyczna
            # (<name><language id="1">X</language></name>) - obsluz oba
            lang = el.find(".//name/language")
            if lang is not None and (lang.text or "").strip():
                nm = lang.text
            else:
                nm = el.findtext("name") or ""
            if nid:
                act = el.findtext("active")
                # brak pola 'active' traktujemy jak aktywne (nie kazdy zasob je ma)
                if act is not None and act.strip() == "0":
                    continue          # pomijamy nieaktywne (np. stare reguly po migracji sklepu)
                out.append((nid, (nm or "").strip()))
    except Exception:
        return out, False
    return out, True


def run_diagnostics(base_url: str, auth_key: str) -> ShopReport:
    """Pelna diagnostyka sklepu: dostepnosc, uprawnienia, wykryte ustawienia."""
    rep = ShopReport()
    ps = PrestaShopClient(base_url, auth_key)
    try:
        try:
            r = ps._client.get(f"{ps.base}/", params={"limit": "1"})
            if r.status_code == 401:
                rep.connection_error = ("Sklep odpowiada, ale klucz webservice zostal odrzucony. "
                                        "Sprawdz, czy klucz jest poprawny i wlaczony.")
                return rep
            rep.reachable = r.status_code < 500
            if not rep.reachable:
                rep.connection_error = f"Sklep zwrocil HTTP {r.status_code}."
                return rep
        except Exception as e:
            rep.connection_error = (f"Nie udalo sie polaczyc ze sklepem ({type(e).__name__}). "
                                    "Sprawdz adres i czy webservice jest wlaczony.")
            return rep

        for resource, method, required, purpose in CHECKS:
            ok, detail = _probe(ps, resource, method)
            rep.checks.append(CheckResult(resource, method, required, purpose, ok, detail))

        # wykrywanie ustawien
        rep.tax_groups, rep.tax_readable = _list_resource(ps, "tax_rule_groups", "tax_rule_group")
        # sklep potrafi miec kilka regul o tej samej nazwie (stare, po migracji).
        # Zbieramy wszystkie kandydatki i podpowiadamy NAJNOWSZA (najwyzsze id),
        # ale pokazujemy uzytkownikowi, ze byl wybor.
        rep.tax_candidates = [(gid, nm) for gid, nm in rep.tax_groups if "23" in nm]
        if rep.tax_candidates:
            rep.suggested_tax_group = max(rep.tax_candidates, key=lambda x: int(x[0]))[0]

        rep.features, rep.features_readable = _list_resource(ps, "product_features", "product_feature")
        for fid, name in rep.features:
            if "rozmiar" in name.lower() or "size" in name.lower():
                rep.suggested_size_feature = fid
                break
    finally:
        ps.close()
    return rep
