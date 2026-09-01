"""Klient PrestaShop webservice (v1.7.x).

Zawiera kwirki wypracowane na sklep.merebilo.eu:
- pojedynczy GET zwraca ZAWSZE {products: [...]} (liczba mnoga) -> bierzemy [0]
- limit=offset,count wywala HTTP 500 przez n8n; tu HTTP idzie bezposrednio,
  ale i tak trzymamy bezpieczne male limity 30-40
- display musi jawnie wymieniac kazde pole, inaczej cicho nic nie wraca
- link_rewrite wymaga scislego ASCII (slugify) -> unikamy bledu walidacji 84
- pole to description_short (nie short_description)
- PUT wymaga pelnego XML; modul grprestashop musi byc wylaczony (przechwytuje PUT)
- tworzenie potrafi zwrocic HTTP 500 mimo sukcesu -> fallback: szukaj po reference
"""
import re
import unicodedata
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx


class PrestaShopError(Exception):
    pass


# Znaki, ktorych normalizacja Unicode NIE rozklada (nie maja formy "litera + znak
# diakrytyczny"), wiec bez tej mapy wypadalyby ze sluga: 'wlosow' -> 'wosow'.
_TRANSLIT = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
})


def slugify(text: str) -> str:
    """Scisly ASCII slug. Usuwa m.in. cyrylice wtracana czasem przez model."""
    text = (text or "").translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"-{2,}", "-", text) or "produkt"


def _lang_text(parent, tag: str) -> str:
    """Tekst pola wielojezycznego: pierwszy wezel <language> danego pola.

    Zgodne z dzialajacym kodem (.//name/language, .//description/language).
    ElementTree scala CDATA do .text, wiec dziala tez dla pol w CDATA.
    """
    node = parent.find(f".//{tag}/language")
    if node is not None and node.text:
        return node.text.strip()
    return ""


def parse_products_needing_description(xml_text: str, min_length: int = 500) -> list[int]:
    """Zwraca ID produktow, ktorych opis (wezel jezykowy) ma < min_length znakow."""
    root = ET.fromstring(xml_text)
    ids: list[int] = []
    for product in root.iter("product"):
        pid = product.findtext("id")
        if not pid:
            continue
        desc = _lang_text(product, "description")
        if len(desc) < min_length:
            ids.append(int(pid))
    return ids


class PrestaShopClient:
    def __init__(self, base_url: str, auth_key: str, timeout: float = 30.0):
        self.base = base_url.rstrip("/") + "/api"
        # webservice PrestaShop: klucz jako login, haslo puste (Basic Auth)
        # verify=True: przy polaczeniu leci klucz webservice, wiec certyfikat
        # sklepu MUSI byc weryfikowany (ochrona przed man-in-the-middle).
        self._client = httpx.Client(auth=(auth_key, ""), timeout=timeout,
                                    verify=True,
                                    headers={"Accept": "application/xml"})

    # -- odczyt ---------------------------------------------------------------

    def products_without_description(self, limit: int = 500, min_length: int = 500) -> list[int]:
        """ID produktow, ktore wymagaja opisu.

        Logika przeniesiona z dzialajacego generate_missing.py:
        opis czytamy z wezla JEZYKOWEGO (.//description/language), a produkt
        uznajemy za wymagajacy opisu, gdy tekst ma < min_length znakow.
        Prog 500 lapie tez produkty z opisem szczatkowym, nie tylko pustym.
        """
        r = self._client.get(
            f"{self.base}/products",
            params={"display": "[id,name,description]", "sort": "[id_DESC]", "limit": str(limit)},
        )
        self._raise_for_status(r)
        return parse_products_needing_description(r.text, min_length)

    def get_product(self, product_id: int) -> dict:
        """Zwraca slownik pol produktu. Pamieta o liczbie mnogiej w odpowiedzi."""
        r = self._client.get(
            f"{self.base}/products/{product_id}",
            params={"display": "[id,reference,name,description,description_short,"
                               "link_rewrite,meta_title,meta_description]"},
        )
        self._raise_for_status(r)
        root = ET.fromstring(r.text)
        product = root.find(".//product")
        if product is None:
            raise PrestaShopError(f"produkt {product_id} nie znaleziony")
        return {child.tag: (child.text or "") for child in product}

    def find_newest_by_reference(self, reference: str) -> int | None:
        """Fallback po tworzeniu: gdy POST zwroci 500, znajdz najnowszy produkt."""
        r = self._client.get(
            f"{self.base}/products",
            params={"display": "[id]", "filter[reference]": reference,
                    "sort": "[id_DESC]", "limit": "1"},
        )
        self._raise_for_status(r)
        root = ET.fromstring(r.text)
        pid = root.findtext(".//product/id")
        return int(pid) if pid else None

    # -- zapis (SZKIC — potwierdzic zestaw pol przed pierwszym live PUT) -------

    @staticmethod
    def _raise_for_status(resp) -> None:
        if resp.status_code >= 400:
            raise PrestaShopError(f"HTTP {resp.status_code}: {resp.text[:300]}")

    def close(self) -> None:
        self._client.close()


# ---------------------------------------------------------------------------
# Skan produktow, odczyt do generowania, zapis opisow
# ---------------------------------------------------------------------------

def filter_scan_ids(items: list[tuple[int, int]], mode: str,
                    start_id: int | None, min_length: int, max_jobs: int) -> list[int]:
    """items: lista (id, dlugosc_opisu). Czysta funkcja - latwa do testow."""
    needing = [(pid, ln) for pid, ln in items if ln < min_length]
    if mode == "newest":
        needing.sort(key=lambda x: -x[0])
    elif mode == "oldest":
        needing.sort(key=lambda x: x[0])
    elif mode == "from_id_down":
        needing = sorted([x for x in needing if start_id and x[0] <= start_id], key=lambda x: -x[0])
    elif mode == "from_id_up":
        needing = sorted([x for x in needing if start_id and x[0] >= start_id], key=lambda x: x[0])
    else:
        raise ValueError(f"nieznany tryb skanu: {mode}")
    return [pid for pid, _ in needing[:max_jobs]]


def build_scan_params(mode: str, start_id: int | None, fetch_limit: int) -> dict:
    """Parametry zapytania. Filtr ID MUSI trafic do zapytania - inaczej
    pobralibysmy 500 produktow z innego zakresu i nic nie znalezli."""
    params = {"display": "[id,description]", "limit": str(fetch_limit)}
    params["sort"] = "[id_ASC]" if mode in ("oldest", "from_id_up") else "[id_DESC]"
    if mode == "from_id_up" and start_id:
        params["filter[id]"] = f"[{start_id},99999999]"
    elif mode == "from_id_down" and start_id:
        params["filter[id]"] = f"[1,{start_id}]"
    return params


# Pola FAKTYCZNIE tylko-do-odczytu. UWAGA: `type` i `id_default_combination`
# NIE moga tu trafic - ich wyciecie kasowalo typ produktu i domyslna kombinacje
# (reset ceny produktow zlozonych).
_READONLY_FIELDS = ["manufacturer_name", "quantity", "position_in_category",
                    "id_default_image"]


def _remove_readonly_fields(xml: str) -> str:
    for f in _READONLY_FIELDS:
        xml = re.sub(rf"\s*<{f}[^>]*>.*?</{f}>", "", xml, flags=re.DOTALL)
    return xml


def _replace_cdata(xml: str, fieldname: str, value: str) -> str:
    safe = str(value).replace("]]>", "]]]]><![CDATA[>")
    pattern = rf"<{fieldname}><language([^>]*)><!\[CDATA\[.*?\]\]></language></{fieldname}>"
    replacement = rf"<{fieldname}><language\1><![CDATA[{safe}]]></language></{fieldname}>"
    return re.sub(pattern, replacement, xml, flags=re.DOTALL)


CORE_FIELDS = ["price", "type", "id_default_combination", "id_tax_rules_group",
               "active", "reference"]


def _core_snapshot(xml: str) -> dict:
    """Wartosci, ktorych zapis opisow NIE MOZE zmienic."""
    root = ET.fromstring(xml)
    product = root.find(".//product")
    snap = {}
    for field in CORE_FIELDS:
        node = product.find(field) if product is not None else None
        snap[field] = (node.text or "").strip() if node is not None and node.text else ""
    snap["combinations"] = len(list(root.iter("combination")))
    return snap


def _snapshot_core(self, product_id: int) -> dict:
    r = self._client.get(f"{self.base}/products/{product_id}")
    self._raise_for_status(r)
    return _core_snapshot(r.text)


def _scan_products(self, mode: str = "newest", start_id: int | None = None,
                   min_length: int = 500, max_jobs: int = 50,
                   fetch_limit: int = 500) -> tuple[list[int], int]:
    """Zwraca (lista ID do opisania, liczba przeskanowanych produktow)."""
    r = self._client.get(f"{self.base}/products",
                         params=build_scan_params(mode, start_id, fetch_limit))
    self._raise_for_status(r)
    root = ET.fromstring(r.text)
    items: list[tuple[int, int]] = []
    for product in root.iter("product"):
        pid = product.findtext("id")
        if pid:
            items.append((int(pid), len(_lang_text(product, "description"))))
    return filter_scan_ids(items, mode, start_id, min_length, max_jobs), len(items)


def _get_product_for_generation(self, product_id: int) -> dict:
    """Pelne dane do promptu + surowy XML."""
    r = self._client.get(f"{self.base}/products/{product_id}")
    self._raise_for_status(r)
    root = ET.fromstring(r.content)

    def _txt(path):
        node = root.find(path)
        return node.text.strip() if node is not None and node.text else ""

    def _plain(html):
        return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())

    image_id = _txt(".//id_default_image") or _txt(".//images/image/id")
    short = _plain(_txt(".//description_short/language"))
    return {
        "id": product_id,
        "name": _txt(".//name/language"),
        "reference": _txt(".//reference") or f"ID-{product_id}",
        "description": _plain(_txt(".//description/language")),
        # prompt produkcyjny czyta `short_description`, reszta `description_short`
        "description_short": short,
        "short_description": short,
        # ochrona URL: istniejacy slug musi trafic do promptu
        "link_rewrite": _txt(".//link_rewrite/language"),
        "slug": _txt(".//link_rewrite/language"),
        "meta_title": _txt(".//meta_title/language"),
        "meta_description": _txt(".//meta_description/language"),
        "manufacturer_name": _txt(".//manufacturer_name"),
        "image_id": image_id,
        "raw_xml": r.text,
    }


def _download_product_image(self, product_id: int, image_id: str) -> bytes:
    r = self._client.get(f"{self.base}/images/products/{product_id}/{image_id}")
    self._raise_for_status(r)
    return r.content


def _update_product_texts(self, product_id: int, fields: dict, raw_xml: str | None = None) -> dict:
    """PUT opisow/SEO na pelnym XML (PrestaShop tego wymaga).

    Zwraca slownik roznic w polach krytycznych - pusty oznacza, ze cena,
    typ, kombinacje i link_rewrite pozostaly nietkniete.
    """
    r = self._client.get(f"{self.base}/products/{product_id}")
    self._raise_for_status(r)
    original = r.text
    before = _core_snapshot(original)

    xml = _remove_readonly_fields(original)
    for name in ("description", "description_short", "meta_title",
                 "meta_description", "meta_keywords"):
        if fields.get(name):
            xml = _replace_cdata(xml, name, fields[name])

    put = self._client.put(f"{self.base}/products/{product_id}",
                           content=xml.encode("utf-8"),
                           headers={"Content-Type": "application/xml"})
    self._raise_for_status(put)

    after = self.snapshot_core(product_id)
    return {k: (before[k], after[k]) for k in before if before[k] != after[k]}


def _find_by_reference(self, reference: str) -> int | None:
    """ID produktu o danym symbolu. Tolerancyjny na HTTP 500 tego sklepu."""
    try:
        r = self._client.get(f"{self.base}/products",
                             params={"display": "[id]", "filter[reference]": reference,
                                     "sort": "[id_DESC]", "limit": "1"})
        pid = ET.fromstring(r.text).findtext(".//product/id")
        return int(pid) if pid else None
    except Exception:
        return None


LOCAL_PRODUCT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<prestashop xmlns:xlink="http://www.w3.org/1999/xlink">
<product>
  <id_manufacturer></id_manufacturer>
  <id_category_default></id_category_default>
  <id_tax_rules_group></id_tax_rules_group>
  <reference></reference>
  <price></price>
  <active></active>
  <state></state>
  <available_for_order></available_for_order>
  <show_price></show_price>
  <minimal_quantity></minimal_quantity>
  <name><language id="1"></language></name>
  <link_rewrite><language id="1"></language></link_rewrite>
  <associations><categories></categories></associations>
</product>
</prestashop>"""


def _blank_schema(self) -> "ET.Element":
    """Szkielet nowego produktu.

    Endpoint `?schema=blank` bywa niesprawny na tym sklepie (HTTP 500 z
    WebserviceOutputBuilder), wiec przy bledzie uzywamy szablonu lokalnego -
    tworzenie produktow nie moze zalezec od tego jednego endpointu.
    """
    try:
        r = self._client.get(f"{self.base}/products", params={"schema": "blank"})
        if r.status_code < 400:
            root = ET.fromstring(r.content)
            if root.find(".//product") is not None:
                return root
    except Exception:
        pass
    return ET.fromstring(LOCAL_PRODUCT_TEMPLATE)


def _set_lang(product, tag: str, value: str) -> None:
    node = product.find(tag)
    if node is None:
        return
    langs = node.findall("language")
    if langs:
        for lang in langs:
            lang.text = value
    else:
        node.text = value


def _create_product(self, *, name: str, reference: str, price_net: str,
                    category_id: int, id_tax_rules_group: int = 1,
                    active: bool = False, id_manufacturer: int | None = None,
                    category_ids: list[int] | None = None) -> int:
    """Tworzy produkt i zwraca ID.

    Kwirk tego sklepu: POST potrafi zwrocic HTTP 500 MIMO poprawnego
    utworzenia produktu, wiec zamiast ufac kodowi odpowiedzi sprawdzamy
    stan faktyczny (wyszukanie po `reference`, z kilkoma probami).
    """
    root = self._blank_schema()
    product = root.find(".//product")

    _set_lang(product, "name", name)
    _set_lang(product, "link_rewrite", slugify(name))   # scisly ASCII -> brak bledu 84

    def _set(tag, value):
        node = product.find(tag)
        if node is not None:
            node.text = str(value)

    _set("reference", reference)
    if id_manufacturer:
        _set("id_manufacturer", id_manufacturer)
    _set("price", price_net)
    _set("id_category_default", category_id)
    _set("id_tax_rules_group", id_tax_rules_group)
    _set("active", 1 if active else 0)
    _set("state", 1)
    _set("available_for_order", 1)
    _set("show_price", 1)
    _set("minimal_quantity", 1)

    cats = product.find(".//associations/categories")
    if cats is not None:
        for child in list(cats):
            cats.remove(child)
        # pelna sciezka kategorii + katalog glowny (2); bez duplikatow, z zachowaniem kolejnosci
        all_cats = [2] + list(category_ids or [category_id])
        for cid in dict.fromkeys(all_cats):
            node = ET.SubElement(cats, "category")
            ET.SubElement(node, "id").text = str(cid)

    body = ET.tostring(root, encoding="unicode")
    try:
        r = self._client.post(f"{self.base}/products", content=body.encode("utf-8"),
                              headers={"Content-Type": "application/xml"})
        if r.status_code < 400:
            pid = ET.fromstring(r.content).findtext(".//product/id")
            if pid:
                return int(pid)
    except Exception:
        pass

    for delay in (0, 1.0, 2.0):
        if delay:
            time.sleep(delay)
        pid = self.find_by_reference(reference)
        if pid:
            return pid
    raise PrestaShopError(
        f"Nie udalo sie utworzyc produktu {reference} (nie ma go tez po sprawdzeniu)")


def _category_path(self, category_id: int) -> list[int]:
    """Zwraca kategorie od korzenia do podanej (lisc + wszyscy przodkowie).

    PrestaShop pokazuje produkt w kategorii tylko, gdy jest jawnie przypisany -
    dlatego produkt musi trafic do calej sciezki, nie tylko do liscia.
    Idziemy w gore po id_parent, ze strażnikiem przeciw petli.
    """
    path, current, seen = [], category_id, set()
    for _ in range(20):   # bezpiecznik glebokosci
        if current in seen or current in (0, None) or current <= 2:
            break
        seen.add(current)
        try:
            r = self._client.get(f"{self.base}/categories/{current}",
                                 params={"display": "[id,id_parent,is_root_category]"})
            node = ET.fromstring(r.text).find(".//category")
            parent = int(node.findtext("id_parent") or 0)
            is_root = (node.findtext("is_root_category") or "0") == "1"
        except Exception:
            path.append(current)   # przy bledzie zostaw przynajmniej biezaca
            break
        if is_root:            # sam korzen sklepu - nie dodajemy
            break
        path.append(current)   # realna kategoria (lisc lub posrednia)
        if parent <= 2:        # rodzic to Root/Home - koniec wspinaczki
            break
        current = parent
    return list(reversed(path))


def _get_or_create_manufacturer(self, name: str) -> int | None:
    """ID producenta o danej nazwie; zaklada go, jesli nie istnieje.

    Odporny na 500 tego sklepu: kazdy krok weryfikowany ponownym odczytem.
    Zwraca None tylko, gdy nie da sie ani znalezc, ani utworzyc - wtedy
    produkt powstaje bez marki (marka nie moze blokowac calego produktu).
    """
    def _find() -> int | None:
        try:
            r = self._client.get(f"{self.base}/manufacturers",
                                 params={"display": "[id,name]",
                                         "filter[name]": name, "limit": "5"})
            for node in ET.fromstring(r.text).iter("manufacturer"):
                got = (node.findtext("name") or "").strip()
                if got.lower() == name.lower():
                    return int(node.findtext("id"))
        except Exception:
            pass
        return None

    existing = _find()
    if existing:
        return existing

    body = ('<?xml version="1.0" encoding="UTF-8"?><prestashop><manufacturer>'
            f'<name><![CDATA[{name}]]></name><active><![CDATA[1]]></active>'
            '</manufacturer></prestashop>')
    try:
        r = self._client.post(f"{self.base}/manufacturers",
                              content=body.encode("utf-8"),
                              headers={"Content-Type": "application/xml"})
        if r.status_code < 400:
            mid = ET.fromstring(r.content).findtext(".//id")
            if mid:
                return int(mid)
    except Exception:
        pass
    return _find()   # moglo powstac mimo 500


def _fix_default_category(self, product_id: int, category_id: int) -> None:
    """Poprawia kategorie domyslna (PrestaShop czasem ustawia root ID 2 -> 404).

    Sklep bywa zwraca 500 mimo wykonanej zmiany, wiec sprawdzamy efekt.
    """
    try:
        r = self._client.get(f"{self.base}/products/{product_id}")
        self._raise_for_status(r)
        xml = _remove_readonly_fields(r.text)
        xml = re.sub(r"<id_category_default>.*?</id_category_default>",
                     f"<id_category_default><![CDATA[{category_id}]]></id_category_default>",
                     xml, flags=re.DOTALL)
        self._client.put(f"{self.base}/products/{product_id}",
                         content=xml.encode("utf-8"),
                         headers={"Content-Type": "application/xml"})
    except Exception:
        pass

    current = self.get_default_category(product_id)
    if current != category_id:
        raise PrestaShopError(
            f"Nie udalo sie ustawic kategorii domyslnej {category_id} "
            f"dla produktu {product_id} (jest: {current})")


def _get_default_category(self, product_id: int) -> int | None:
    try:
        r = self._client.get(f"{self.base}/products/{product_id}",
                             params={"display": "[id,id_category_default]"})
        value = ET.fromstring(r.text).findtext(".//id_category_default")
        return int(value) if value else None
    except Exception:
        return None


def _list_product_images(self, product_id: int) -> list[int]:
    try:
        r = self._client.get(f"{self.base}/images/products/{product_id}")
        return [int(x) for x in re.findall(r"/images/products/\d+/(\d+)", r.text)]
    except Exception:
        return []


def safe_upload_filename(name: str) -> str:
    """Nazwa dla multipartu do PrestaShop: scisly ASCII.

    Sklep waliduje nazwy przesylanych plikow i odrzuca spacje, polskie znaki,
    '!' czy ','. Dla sklepu nazwa nie ma znaczenia (obrazy zapisuje pod
    wlasnymi ID) - oryginalna nazwa zostaje na dysku i przy pobieraniu.
    """
    stem = slugify(Path(name).stem)[:60] or "photo"
    return f"{stem}.jpg"


def _upload_product_image(self, product_id: int, image_bytes: bytes,
                          filename: str = "photo.jpg") -> None:
    """Wgrywa zdjecie. Weryfikuje efekt, bo sklep bywa zwraca 500 mimo sukcesu."""
    before = len(self.list_product_images(product_id))
    safe_name = safe_upload_filename(filename)
    detail = ""
    try:
        r = self._client.post(f"{self.base}/images/products/{product_id}",
                              files={"image": (safe_name, image_bytes, "image/jpeg")})
        if r.status_code < 400:
            return
        detail = f"HTTP {r.status_code}: {' '.join(r.text.split())[:250]}"
    except Exception as e:
        detail = f"{type(e).__name__}: {e}"[:250]

    if len(self.list_product_images(product_id)) > before:
        return   # zdjecie jednak sie wgralo

    raise PrestaShopError(
        f"Nie udalo sie wgrac zdjecia do produktu {product_id} "
        f"(plik '{safe_name}', {len(image_bytes) // 1024} KB). {detail}")


def _get_or_create_feature_value(self, id_feature: int, value: str) -> int:
    """Zwraca ID wartosci cechy; tworzy ja, jesli jeszcze nie istnieje."""
    r = self._client.get(f"{self.base}/product_feature_values",
                         params={"display": "[id,value]",
                                 "filter[id_feature]": str(id_feature), "limit": "100"})
    self._raise_for_status(r)
    for node in ET.fromstring(r.text).iter("product_feature_value"):
        current = _lang_text(node, "value") or (node.findtext("value") or "")
        if current.strip().lower() == value.strip().lower():
            return int(node.findtext("id"))

    schema = self._client.get(f"{self.base}/product_feature_values",
                              params={"schema": "blank"})
    self._raise_for_status(schema)
    root = ET.fromstring(schema.content)
    fv = root.find(".//product_feature_value")
    node = fv.find("id_feature")
    if node is not None:
        node.text = str(id_feature)
    _set_lang(fv, "value", value)

    r = self._client.post(f"{self.base}/product_feature_values",
                          content=ET.tostring(root, encoding="unicode").encode("utf-8"),
                          headers={"Content-Type": "application/xml"})
    self._raise_for_status(r)
    return int(ET.fromstring(r.content).findtext(".//id"))


def _set_product_feature(self, product_id: int, id_feature: int, value: str) -> None:
    """Dopisuje ceche (np. Rozmiar) do produktu, zachowujac istniejace cechy."""
    id_value = self.get_or_create_feature_value(id_feature, value)

    r = self._client.get(f"{self.base}/products/{product_id}")
    self._raise_for_status(r)
    root = ET.fromstring(r.content)
    product = root.find(".//product")

    assoc = product.find("associations")
    if assoc is None:
        assoc = ET.SubElement(product, "associations")
    features = assoc.find("product_features")
    if features is None:
        features = ET.SubElement(assoc, "product_features")
    else:
        for node in list(features):
            if (node.findtext("id") or "") == str(id_feature):
                features.remove(node)   # podmieniamy wartosc tej samej cechy

    entry = ET.SubElement(features, "product_feature")
    ET.SubElement(entry, "id").text = str(id_feature)
    ET.SubElement(entry, "id_feature_value").text = str(id_value)

    xml = _remove_readonly_fields(ET.tostring(root, encoding="unicode"))
    try:
        self._client.put(f"{self.base}/products/{product_id}",
                         content=xml.encode("utf-8"),
                         headers={"Content-Type": "application/xml"})
    except Exception:
        pass

    # weryfikacja: czy cecha faktycznie jest przypisana
    try:
        r = self._client.get(f"{self.base}/products/{product_id}")
        for node in ET.fromstring(r.content).iter("product_feature"):
            if (node.findtext("id") or "") == str(id_feature):
                return
    except Exception:
        return   # nie blokujemy produktu z powodu samej cechy
    raise PrestaShopError(f"Nie udalo sie zapisac cechy {id_feature} (produkt {product_id})")


PrestaShopClient.scan_products = _scan_products
PrestaShopClient.get_product_for_generation = _get_product_for_generation
PrestaShopClient.download_product_image = _download_product_image
PrestaShopClient.update_product_texts = _update_product_texts
PrestaShopClient.snapshot_core = _snapshot_core
PrestaShopClient._blank_schema = _blank_schema
PrestaShopClient.find_by_reference = _find_by_reference
PrestaShopClient.create_product = _create_product
PrestaShopClient.get_or_create_manufacturer = _get_or_create_manufacturer
PrestaShopClient.category_path = _category_path
PrestaShopClient.fix_default_category = _fix_default_category
PrestaShopClient.get_default_category = _get_default_category
PrestaShopClient.upload_product_image = _upload_product_image
PrestaShopClient.list_product_images = _list_product_images
PrestaShopClient.get_or_create_feature_value = _get_or_create_feature_value
PrestaShopClient.set_product_feature = _set_product_feature
