"""Walidacja base_url sklepu pod katem SSRF.

Klient podaje adres swojego sklepu, a serwer sie pod niego laczy. Bez tej
walidacji moglby wskazac adres sieci wewnetrznej (baza, metadane chmury,
localhost) i wyciagnac wrazliwe dane. Wymuszamy https i publiczny adres IP.
"""
from __future__ import annotations
import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURL(ValueError):
    """base_url wskazuje na adres wewnetrzny albo ma zly format."""


def validate_shop_url(raw: str, *, allow_http: bool = False) -> str:
    """Zwraca oczyszczony base_url albo rzuca UnsafeURL.

    - wymusza schemat https (http tylko gdy allow_http=True, np. sklep testowy)
    - odrzuca adresy prywatne, loopback, link-local, metadane chmury
    - rozwiazuje nazwe na IP i sprawdza KAZDY zwrocony adres
    """
    raw = (raw or "").strip()
    if not raw:
        raise UnsafeURL("Pusty adres sklepu.")

    parsed = urlparse(raw)
    if parsed.scheme not in (("https", "http") if allow_http else ("https",)):
        raise UnsafeURL("Adres musi zaczynac sie od https://")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("Brak nazwy hosta w adresie.")

    # blokada nazw oczywiscie wewnetrznych, zanim jeszcze rozwiazemy DNS
    low = host.lower()
    if low in ("localhost",) or low.endswith(".localhost") or low.endswith(".internal"):
        raise UnsafeURL("Adres wskazuje na host wewnetrzny.")

    # rozwiazanie DNS - sprawdzamy wszystkie zwrocone adresy (A i AAAA)
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise UnsafeURL(f"Nie mozna rozwiazac adresu '{host}'.")

    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            raise UnsafeURL(f"Nieprawidlowy adres IP: {ip_str}")
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL("Adres wskazuje na siec wewnetrzna - niedozwolony.")
        # metadane chmury (AWS/GCP/Azure) - 169.254.169.254 lapie link_local,
        # ale dokladamy jawnie dla czytelnosci
        if ip_str == "169.254.169.254":
            raise UnsafeURL("Adres metadanych chmury - niedozwolony.")

    return raw.rstrip("/")
