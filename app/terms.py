"""Indywidualne warunki klienta: koszty, limity, dostep do funkcji.

Kazdy klient (tenant) moze miec wlasne stawki i ograniczenia. Brak wartosci
(NULL) oznacza domyslne ustawienie aplikacji.
"""
from __future__ import annotations
from dataclasses import dataclass

DEFAULT_COST_DESCRIPTION = 1
DEFAULT_COST_IMAGE = 2

FEATURES = {
    "descriptions": ("allow_descriptions", "generowanie opisow"),
    "images":       ("allow_images",       "obrobka zdjec"),
    "products":     ("allow_products",     "tworzenie produktow ze zdjec"),
}


class FeatureDisabled(PermissionError):
    """Klient nie ma dostepu do tej funkcji."""


class LimitReached(RuntimeError):
    """Klient wyczerpal limit dzienny lub miesieczny."""


@dataclass
class Terms:
    cost_description: int
    cost_image: int
    limit_daily: int | None
    limit_monthly: int | None
    allow_descriptions: bool
    allow_images: bool
    allow_products: bool


def load(conn, tenant_id: int) -> Terms:
    r = conn.execute(
        "SELECT cost_description, cost_image, limit_daily, limit_monthly, "
        "       allow_descriptions, allow_images, allow_products "
        "FROM tenants WHERE id = %s", (tenant_id,)).fetchone()
    if not r:
        return Terms(DEFAULT_COST_DESCRIPTION, DEFAULT_COST_IMAGE, None, None, True, True, True)
    return Terms(
        cost_description=r["cost_description"] if r["cost_description"] is not None else DEFAULT_COST_DESCRIPTION,
        cost_image=r["cost_image"] if r["cost_image"] is not None else DEFAULT_COST_IMAGE,
        limit_daily=r["limit_daily"], limit_monthly=r["limit_monthly"],
        allow_descriptions=bool(r["allow_descriptions"]),
        allow_images=bool(r["allow_images"]),
        allow_products=bool(r["allow_products"]))


def check_feature(conn, tenant_id: int, feature: str) -> None:
    """Rzuca FeatureDisabled, gdy klient nie ma dostepu do funkcji."""
    t = load(conn, tenant_id)
    kolumna, opis = FEATURES[feature]
    if not getattr(t, kolumna):
        raise FeatureDisabled(f"Twoje konto nie ma dostepu do funkcji: {opis}.")


def usage(conn, tenant_id: int, period: str) -> int:
    """Liczba operacji klienta w biezacej dobie ('day') lub miesiacu ('month')."""
    interval = "1 day" if period == "day" else "1 month"
    r = conn.execute(
        "SELECT count(*) AS n FROM jobs "
        "WHERE tenant_id = %s AND status = 'done' "
        f"  AND updated_at > now() - interval '{interval}'",
        (tenant_id,)).fetchone()
    return r["n"] if r else 0


def check_limits(conn, tenant_id: int) -> None:
    """Rzuca LimitReached, gdy klient wyczerpal limit."""
    t = load(conn, tenant_id)
    if t.limit_daily is not None and usage(conn, tenant_id, "day") >= t.limit_daily:
        raise LimitReached(f"Wyczerpano dzienny limit ({t.limit_daily} operacji). "
                           "Sprobuj jutro albo skontaktuj sie z obsluga.")
    if t.limit_monthly is not None and usage(conn, tenant_id, "month") >= t.limit_monthly:
        raise LimitReached(f"Wyczerpano miesieczny limit ({t.limit_monthly} operacji).")
