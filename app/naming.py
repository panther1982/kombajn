"""Parser nazwy pliku zdjecia produktu.

Format:
    Nazwa kategorii - SYMBOL!CENA_BRUTTO!ROZMIAR.jpg

Reguly:
- rozmiar jest opcjonalny:            "Kategoria - MF25!9.jpg"
- kilka zdjec jednego produktu:       "... (2).jpg" / "...#2.jpg"  -> ten sam symbol
- cena i rozmiar: przecinek lub kropka
- kategoria moze zawierac myslniki    ("Stal 316L - premium - MF25!9" dziala)
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

SUFFIX_RE = re.compile(r"(?:\s*\((\d+)\)|\s*#(\d+))$")


class FilenameError(ValueError):
    pass


@dataclass
class ParsedPhoto:
    category: str
    symbol: str
    price_gross: Decimal
    size: str | None      # np. "4,5" -> "4.5"; None gdy brak
    photo_index: int      # 1 = zdjecie glowne
    orig_name: str

    def price_net(self, vat_rate: Decimal) -> Decimal:
        """Cena netto do pola `price` w PrestaShop (zaokr. do 6 miejsc jak PS)."""
        return (self.price_gross / (Decimal(1) + vat_rate)).quantize(Decimal("0.000001"))


def _to_decimal(raw: str, label: str) -> Decimal:
    try:
        return Decimal(raw.strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        raise FilenameError(f"Nieczytelna wartosc ({label}): '{raw}'")


def parse_photo_filename(filename: str) -> ParsedPhoto:
    stem = Path(filename).stem.strip()

    # numer kolejnego zdjecia tego samego produktu
    photo_index = 1
    m = SUFFIX_RE.search(stem)
    if m:
        photo_index = int(m.group(1) or m.group(2))
        stem = SUFFIX_RE.sub("", stem).strip()

    parts = [p.strip() for p in stem.split("!")]
    if len(parts) < 2:
        raise FilenameError(
            "Brak ceny. Oczekiwany format: 'Kategoria - SYMBOL!CENA!ROZMIAR'")
    if len(parts) > 3:
        raise FilenameError("Za duzo czlonow '!' w nazwie pliku.")

    head, price_raw = parts[0], parts[1]
    size = None
    if len(parts) == 3 and parts[2]:
        size = parts[2].replace(",", ".")

    if " - " not in head:
        raise FilenameError(
            "Brak separatora ' - ' miedzy kategoria a symbolem.")
    category, symbol = head.rsplit(" - ", 1)  # kategoria moze zawierac ' - '
    category, symbol = category.strip(), symbol.strip()

    if not category:
        raise FilenameError("Pusta nazwa kategorii.")
    if not symbol:
        raise FilenameError("Pusty symbol produktu.")

    price = _to_decimal(price_raw, "cena")
    if price <= 0:
        raise FilenameError(f"Cena musi byc wieksza od zera: '{price_raw}'")

    return ParsedPhoto(category=category, symbol=symbol, price_gross=price,
                       size=size, photo_index=photo_index, orig_name=filename)


def group_by_symbol(parsed: list[ParsedPhoto]) -> dict[str, list[ParsedPhoto]]:
    """Grupuje zdjecia w produkty po symbolu; sortuje wg photo_index."""
    groups: dict[str, list[ParsedPhoto]] = {}
    for p in parsed:
        groups.setdefault(p.symbol, []).append(p)
    for items in groups.values():
        items.sort(key=lambda x: x.photo_index)
    return groups


# ---------------------------------------------------------------------------
# Marka (producent) z nazwy pliku
# ---------------------------------------------------------------------------

# Producenci w PrestaShop (ID sprawdzone w sklepie):
#   Merebilo = 2, Xuping = 3, CHUANGMEI JEWELERY = 107
MANUFACTURER_MEREBILO = 2
MANUFACTURER_XUPING = 3
MANUFACTURER_CHUANGMEI = 107

# Kolejnosc ma znaczenie: przy konflikcie wygrywa pierwsza pasujaca regula.
# CM (Chuangmei) przed Xuping, bo 'CM' w symbolu to mocniejszy wskaznik.
BRAND_RULES = [
    (re.compile(r"(?<![A-Za-z0-9])CM(?![a-z])"), ("Chuangmei", MANUFACTURER_CHUANGMEI)),
    (re.compile(r"uping", re.IGNORECASE), ("Xuping", MANUFACTURER_XUPING)),
    (re.compile(r"merebilo", re.IGNORECASE), ("Merebilo", MANUFACTURER_MEREBILO)),
]

# Gdy zadna regula nie pasuje - Merebilo jako marka domyslna (wlasny towar).
DEFAULT_BRAND = ("Merebilo", MANUFACTURER_MEREBILO)


def detect_brand(category: str | None, symbol: str) -> str:
    """Nazwa marki na podstawie kategorii i symbolu (czesc nazwy pliku)."""
    return detect_manufacturer(category, symbol)[0]


def detect_manufacturer(category: str | None, symbol: str) -> tuple[str, int]:
    """Zwraca (nazwa marki, ID producenta w PrestaShop).

    Merebilo/Xuping/CM wykrywane z nazwy; brak dopasowania -> Merebilo (domyslna).
    ID sa stale (sprawdzone w sklepie), wiec nie zalezymy od odczytu nazw przez API.
    """
    text = f"{category or ''} {symbol or ''}"
    for pattern, brand in BRAND_RULES:
        if pattern.search(text):
            return brand
    return DEFAULT_BRAND
