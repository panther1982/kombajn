"""Przeliczanie zuzycia AI na zlotowki.

Stawki trzymamy w konfiguracji (.env), bo cenniki dostawcow i kurs walutowy
sie zmieniaja. Wartosci domyslne odpowiadaja cennikom z wrzesnia 2026:
  - Claude Sonnet 4.6: 3 USD / mln tokenow wejscia, 15 USD / mln wyjscia
  - gpt-image-2 (1536x1536): ok. 0,05 USD za obraz
Sprawdz aktualne stawki u dostawcow i popraw w .env, jesli sie roznia.
"""
from __future__ import annotations
import os


def _liczba(nazwa: str, domyslna: float) -> float:
    try:
        return float(os.environ.get(nazwa, "").replace(",", ".") or domyslna)
    except ValueError:
        return domyslna


def stawki() -> dict:
    return {
        "claude_in":  _liczba("PRICE_CLAUDE_IN_USD_MTOK", 3.0),
        "claude_out": _liczba("PRICE_CLAUDE_OUT_USD_MTOK", 15.0),
        # gpt-image-2 rozlicza sie po tokenach, z osobnymi stawkami:
        #   obraz wejsciowy 8, tekst wejsciowy 5, obraz wyjsciowy 30 USD/mln
        "obraz_in":   _liczba("PRICE_IMAGE_IN_USD_MTOK", 8.0),
        "obraz_txt":  _liczba("PRICE_IMAGE_TEXT_USD_MTOK", 5.0),
        "obraz_out":  _liczba("PRICE_IMAGE_OUT_USD_MTOK", 30.0),
        # zapasowa stawka ryczaltowa, gdy API nie poda tokenow
        "obraz":      _liczba("PRICE_IMAGE_USD", 0.048),
        "kurs":       _liczba("USD_PLN", 3.65),
    }


def koszt_obrazu_usd(tok_we: int, tok_wy: int, liczba: int = 0) -> float:
    """Koszt obrobki zdjec. Gdy znamy tokeny - liczymy dokladnie,
    inaczej mnozymy liczbe obrazow przez stawke ryczaltowa."""
    s = stawki()
    if tok_we or tok_wy:
        return (tok_we / 1_000_000) * s["obraz_in"] + (tok_wy / 1_000_000) * s["obraz_out"]
    return liczba * s["obraz"]


def koszt_opisu_usd(tok_we: int, tok_wy: int) -> float:
    s = stawki()
    return (tok_we / 1_000_000) * s["claude_in"] + (tok_wy / 1_000_000) * s["claude_out"]


def koszt_pln(tok_we: int, tok_wy: int, liczba_obrazow: int = 0,
              obraz_we: int = 0, obraz_wy: int = 0) -> float:
    """Laczny koszt w PLN.

    tok_we/tok_wy   - tokeny Claude (opisy)
    obraz_we/obraz_wy - tokeny gpt-image (obrobka zdjec); gdy 0, uzywamy
                        stawki ryczaltowej razy liczba_obrazow
    """
    s = stawki()
    usd = (koszt_opisu_usd(tok_we, tok_wy)
           + koszt_obrazu_usd(obraz_we, obraz_wy, liczba_obrazow))
    return round(usd * s["kurs"], 4)


def opis_stawek() -> str:
    s = stawki()
    return (f"Claude {s['claude_in']:.2f}/{s['claude_out']:.2f} USD za mln tokenow, "
            f"obrazy {s['obraz_in']:.2f}/{s['obraz_out']:.2f} USD za mln tokenow "
            f"(bez pomiaru: {s['obraz']:.3f} USD/szt.), kurs {s['kurs']:.2f} PLN/USD")
