"""Testy trybu 'single'. Uruchom: python -m tests.test_single_prompt"""
import json

from app.ai_gateway import _normalize_fields
from app.templating import render_n8n_template, has_n8n_placeholders
from app.validation import validate_single_output, ValidationError

# fragment DANYCH PRODUKTU z produkcyjnego promptu — wklejony bez zmian
PROMPT_TAIL = """DANE PRODUKTU

Nazwa: {{ $json.name }}

ID: {{ $json.id }}

Adres zdjecia: {{ $json.image_api_url }}

Obecny link_rewrite / slug: {{ $json.link_rewrite || $json.slug || '' }}

Opis obecny: {{ $json.description || 'brak' }}

Krotki opis: {{ $json.short_description || 'brak' }}

Fraza glowna: {{ $json.primary_keyword || '' }}

Ostatnio uzyte zwroty: {{ $json.recent_phrases || '' }}
"""


def test_templating():
    product = {
        "name": "Kolczyki Xuping Stal 316L MF25000",
        "id": 4821,
        "image_api_url": "https://test.merebilo.eu/api/images/products/4821/9",
        "link_rewrite": "kolczyki-xuping-stal-316l-mf25000",
        "description": "",              # puste -> ma zadzialac fallback 'brak'
        "short_description": None,
    }
    out = render_n8n_template(PROMPT_TAIL, product)

    assert "Nazwa: Kolczyki Xuping Stal 316L MF25000" in out
    assert "ID: 4821" in out
    # istniejacy link_rewrite skopiowany (ochrona URL)
    assert "Obecny link_rewrite / slug: kolczyki-xuping-stal-316l-mf25000" in out
    # puste pole -> literal 'brak'
    assert "Opis obecny: brak" in out
    assert "Krotki opis: brak" in out
    # brak wartosci i literal '' -> pusty string
    assert "Fraza glowna: \n" in out or out.rstrip().endswith("Ostatnio uzyte zwroty:")
    assert "{{" not in out, "wszystkie placeholdery musza zniknac"
    print("  [ok] skladnia n8n: podstawienia, fallback ||, literaly 'brak' i ''")

    # nowy produkt bez sluga -> puste, zeby model wygenerowal nowy
    fresh = render_n8n_template("slug: {{ $json.link_rewrite || $json.slug || '' }}", {})
    assert fresh.strip() == "slug:"
    print("  [ok] brak sluga -> puste pole (model wygeneruje nowy)")

    assert has_n8n_placeholders(PROMPT_TAIL) and not has_n8n_placeholders("zwykly tekst")
    print("  [ok] wykrywanie promptu w skladni n8n")


def test_field_mapping():
    raw = json.loads("""{
      "id": "4821", "name": "Kolczyki MF25000",
      "title": "Kolczyki w zlotym odcieniu",
      "description_short": "Krotki opis produktu.",
      "description_html": "<p>A</p><ul><li>B</li></ul>",
      "meta_title": "Kolczyki | Merebilo",
      "meta_description": "Opis SEO",
      "image_alt": "Kolczyki w zlotym odcieniu",
      "slug": "kolczyki-mf25000",
      "link_rewrite": "kolczyki-mf25000"
    }""")
    f = _normalize_fields(raw)
    assert f["description"] == "<p>A</p><ul><li>B</li></ul>", "description_html -> description"
    assert f["alt"] == "Kolczyki w zlotym odcieniu", "image_alt -> alt"
    assert f["meta_title"] == "Kolczyki | Merebilo"
    assert "link_rewrite" in f  # zachowane w wyniku, ale NIE zapisywane do sklepu
    print("  [ok] mapowanie pol: description_html->description, image_alt->alt")


def _good_fields():
    body = ("<p>" + "Kolczyki w zlotym odcieniu z ozdobnym detalem. " * 8 + "</p>"
            "<p>" + "Sprawdza sie w codziennych stylizacjach. " * 6 + "</p>"
            "<ul><li>zloty odcien</li><li>lekka konstrukcja</li></ul>"
            "<p>Warto rozszerzyc asortyment o ten model.</p>")
    return {"description": body, "description_short": "Krotki opis produktu bez HTML.",
            "meta_title": "Kolczyki zlote | Merebilo",
            "meta_description": "Kolczyki w zlotym odcieniu.",
            "alt": "Kolczyki w zlotym odcieniu"}


def test_validation():
    validate_single_output(_good_fields())
    print("  [ok] poprawny wynik przechodzi walidacje")

    # KLUCZOWE: naglowek <h2> jest zabroniony przez ten prompt
    bad = _good_fields()
    bad["description"] = "<h2>Tytul</h2>" + bad["description"]
    try:
        validate_single_output(bad); assert False, "powinno odrzucic <h2>"
    except ValidationError as e:
        assert "h2" in str(e)
    print("  [ok] naglowek <h2> odrzucony (prompt dopuszcza tylko p/ul/li)")

    for name, mutate, expect in [
        ("brak ALT", lambda f: f.update(alt=""), "Brak wymaganych pol"),
        ("za krotki opis", lambda f: f.update(description="<p>Krotko.</p>"), "za krotki"),
        ("HTML w short", lambda f: f.update(description_short="<p>x</p>"), "czystym tekstem"),
        ("Markdown", lambda f: f.update(description="<p>x</p>```json"), None),
        ("za dlugi ALT", lambda f: f.update(alt="x" * 200), "alt"),
    ]:
        f = _good_fields(); mutate(f)
        try:
            validate_single_output(f); assert False, f"powinno odrzucic: {name}"
        except ValidationError as e:
            if expect:
                assert expect in str(e), (name, str(e))
    print("  [ok] odrzuca: brak ALT, za krotki opis, HTML w krotkim opisie, Markdown, za dlugi ALT")


def test_product_fields_for_prompt():
    """Pola, ktorych wymaga prompt produkcyjny, musza dotrzec do modelu."""
    import xml.etree.ElementTree as ET
    from app.prestashop import _lang_text

    # odpowiedz PrestaShop dla istniejacego produktu (ma juz slug i krotki opis)
    xml = """<?xml version="1.0"?><prestashop><product>
      <id>57110</id><reference>MF42907</reference>
      <name><language id="1"><![CDATA[Gumka do wlosow - MF42907]]></language></name>
      <link_rewrite><language id="1"><![CDATA[gumka-do-wlosow-mf42907]]></language></link_rewrite>
      <description><language id="1"><![CDATA[]]></language></description>
      <description_short><language id="1"><![CDATA[<p>Istniejacy krotki opis.</p>]]></language></description_short>
      <meta_title><language id="1"><![CDATA[Stary tytul]]></language></meta_title>
      <meta_description><language id="1"><![CDATA[]]></language></meta_description>
      <id_default_image>58885</id_default_image>
    </product></prestashop>"""

    root = ET.fromstring(xml)
    product = {
        "id": 57110,
        "name": _lang_text(root, "name"),
        "reference": root.findtext(".//reference"),
        "link_rewrite": _lang_text(root, "link_rewrite"),
        "slug": _lang_text(root, "link_rewrite"),
        "short_description": "Istniejacy krotki opis.",
        "description_short": "Istniejacy krotki opis.",
        "description": "",
        "image_api_url": "https://test.merebilo.eu/api/images/products/57110/58885",
    }

    out = render_n8n_template(PROMPT_TAIL, product)
    # ochrona URL: istniejacy slug MUSI trafic do promptu
    assert "Obecny link_rewrite / slug: gumka-do-wlosow-mf42907" in out, out[-400:]
    # krotki opis czytany jako short_description (nie description_short)
    assert "Krotki opis: Istniejacy krotki opis." in out
    assert "Krotki opis: brak" not in out
    # adres zdjecia przekazany
    assert "products/57110/58885" in out
    print("  [ok] pola dla promptu: istniejacy slug, krotki opis, adres zdjecia")


if __name__ == "__main__":
    print("Test trybu jednego promptu:")
    test_templating()
    test_field_mapping()
    test_validation()
    test_product_fields_for_prompt()
    print("\nWszystko zielone.")


