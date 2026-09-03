"""Przebiegi zadan. Kredyty pobierane w tej samej transakcji co zakonczenie joba.

Bezpiecznik: WRITE_ENABLED=0 (domyslnie) => opisy sa generowane i zapisywane
do zadania (podglad w panelu), ale NIC nie idzie do sklepu. Wlaczenie zapisu
to swiadoma decyzja w .env, po podpieciu kluczy i akceptacji wynikow.
"""
import json
from pathlib import Path

from app import ai_gateway, credits, jobs
from app import terms


def output_filename(orig_name: str) -> str:
    """Nazwa pliku wynikowego = nazwa wejsciowa BEZ ZMIAN.

    Jedyny wyjatek (jak w oryginalnym batchu): wynik zawsze jest JPEG,
    wiec dla wejscia .png/.webp podmieniamy samo rozszerzenie na .jpg.
    Nazwa niesie dane produktu, wiec nie wolno doklejac prefiksow.
    """
    path = Path(orig_name)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return path.name
    return f"{path.stem}.jpg"
from app.validation import (validate_generated_content, validate_single_output,
                            ValidationError)


def run_description_job(conn, job: dict, shop: dict, auth_key: str,
                        api_key: str, write_enabled: bool) -> None:
    """queued -> description_seo (generacja+walidacja) -> publish (PUT) -> done"""
    from app.prestashop import PrestaShopClient

    tenant_id = job["tenant_id"]
    ps = PrestaShopClient(shop["base_url"], auth_key)
    try:
        if not job.get("product_id"):
            pid = job["payload"].get("product_id")
            if pid:
                job["product_id"] = int(pid)
                conn.execute("UPDATE jobs SET product_id=%s WHERE id=%s", (int(pid), job["id"]))
                conn.commit()

        cost = 0
        if job["stage"] in ("queued", "description_seo"):
            product = ps.get_product_for_generation(job["product_id"])
            # adres zdjecia wymagany przez prompt produkcyjny (pole image_api_url)
            if product.get("image_id"):
                product["image_api_url"] = (f"{shop['base_url'].rstrip('/')}/api/images/"
                                            f"products/{job['product_id']}/{product['image_id']}")
            else:
                product["image_api_url"] = ""
            image_bytes = None
            image_note = "brak zdjecia glownego w produkcie"
            if product.get("image_id"):
                try:
                    image_bytes = ps.download_product_image(job["product_id"], product["image_id"])
                    if image_bytes and len(image_bytes) > 1000:
                        image_note = f"ok ({len(image_bytes) // 1024} KB)"
                    else:
                        image_note = f"pobrano tylko {len(image_bytes or b'')} B - podejrzanie malo"
                        image_bytes = None
                except Exception as e:
                    # brak zdjecia nie blokuje generowania, ale MUSI byc widoczny
                    image_note = f"blad pobierania: {type(e).__name__}: {e}"[:300]
                    image_bytes = None

            mode = shop.get("description_mode") or "single"
            if mode == "single":
                gen = ai_gateway.generate_description_single(
                    product, shop.get("prompt") or "", image_bytes, api_key)
            else:
                prompts = {
                    "analysis": shop.get("prompt_analysis") or "",
                    "description": shop.get("prompt") or "",
                    "meta": shop.get("prompt_meta") or "",
                }
                gen = ai_gateway.generate_description_chain(product, image_bytes,
                                                            prompts, api_key)
            # stawka klienta (moze byc indywidualna); 0 = tryb podgladu
            cost = terms.load(conn, tenant_id).cost_description if gen.cost_credits else 0

            if not gen.preview_mode:
                def _sprawdz(g):
                    if mode == "single":
                        validate_single_output(g.fields)
                    else:
                        validate_generated_content(product["name"],
                                                   g.fields.get("description", ""), g.fields)
                try:
                    _sprawdz(gen)
                except ValidationError as ve:
                    # DRUGA PROBA: mowimy modelowi, co bylo nie tak, i prosimy o poprawke.
                    # Jedno ponowienie - wiecej nie oplaca sie (kazde to koszt wywolania).
                    if mode == "single":
                        try:
                            gen = ai_gateway.generate_description_single(
                                product, shop.get("prompt") or "", image_bytes, api_key,
                                poprawka=str(ve))
                            _sprawdz(gen)
                            cost = terms.load(conn, tenant_id).cost_description if gen.cost_credits else 0
                        except ValidationError as ve2:
                            jobs.fail(conn, job["id"], f"walidacja (po poprawce): {ve2}")
                            conn.commit()
                            return
                        except Exception as e2:
                            jobs.fail(conn, job["id"], f"ponowienie nieudane: {type(e2).__name__}")
                            conn.commit()
                            return
                    else:
                        jobs.fail(conn, job["id"], f"walidacja: {ve}")
                        conn.commit()
                        return

            if cost and credits.credits_enabled(conn, tenant_id) and credits.get_balance(conn, tenant_id) < cost:
                jobs.hold(conn, job["id"], "brak kredytow")
                conn.commit()
                return

            jobs.set_stage(conn, job["id"], "publish",
                           result_patch={**gen.fields, "_analysis": gen.analysis[:4000],
                                         "_preview": gen.preview_mode, "_cost": cost,
                                         "_image": image_note})
            conn.commit()
            job["result"] = {**job.get("result", {}), **gen.fields,
                             "_preview": gen.preview_mode, "_cost": cost}

        if job["stage"] == "publish" or cost is not None:
            result = job["result"]
            preview = bool(result.get("_preview"))
            cost = int(result.get("_cost", 0))

            if not write_enabled or preview:
                jobs.hold(conn, job["id"],
                          "podglad gotowy - zapis do sklepu wylaczony"
                          if not write_enabled else "podglad (brak klucza AI)")
                conn.commit()
                return

            drift = ps.update_product_texts(job["product_id"], result)
            if drift:
                # zapis opisu zmienil cene/typ/kombinacje - to NIGDY nie powinno sie zdarzyc
                opis = "; ".join(f"{k}: {a!r} -> {b!r}" for k, (a, b) in drift.items())
                jobs.fail(conn, job["id"], f"UWAGA - zapis zmienil pola krytyczne: {opis}")
                conn.commit()
                return
            if cost:
                credits.charge(conn, tenant_id, cost, "charge:description", job["id"])
            jobs.complete(conn, job["id"])
            conn.commit()
    finally:
        ps.close()


def run_image_job(conn, job: dict, shop_prompt_image: str, openai_key: str,
                  data_dir: str) -> None:
    """queued -> image_processing -> done. Wynik na dysku (DATA_DIR), sciezka w result."""
    tenant_id = job["tenant_id"]
    payload = job["payload"]
    input_path = Path(payload["input_path"])
    if not input_path.exists():
        jobs.fail(conn, job["id"], f"brak pliku wejsciowego: {input_path.name}")
        conn.commit()
        return

    jobs.set_stage(conn, job["id"], "image_processing")
    conn.commit()

    data = input_path.read_bytes()
    result = ai_gateway.process_image(data, shop_prompt_image, openai_key)

    koszt_zdjecia = terms.load(conn, tenant_id).cost_image if result.cost_credits else 0
    if koszt_zdjecia and credits.credits_enabled(conn, tenant_id) and credits.get_balance(conn, tenant_id) < koszt_zdjecia:
        jobs.hold(conn, job["id"], "brak kredytow")
        conn.commit()
        return

    # Nazwa pliku niesie dane produktu (kategoria, symbol, cena, rozmiar),
    # wiec MUSI zostac zachowana co do znaku. Kolizje rozwiazuje osobny
    # katalog na zadanie, a nie doklejanie prefiksow do nazwy.
    out_dir = Path(data_dir) / "processed" / str(tenant_id) / str(job["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    orig = payload.get("orig_name", f"job{job['id']}.jpg")
    out_path = out_dir / output_filename(orig)
    out_path.write_bytes(result.output_bytes)

    jobs.set_stage(conn, job["id"], "done",
                   result_patch={"output_path": str(out_path), "orig_name": orig,
                                 "size_kb": round(len(result.output_bytes) / 1024),
                                 "_preview": result.preview_mode})
    if koszt_zdjecia:
        credits.charge(conn, tenant_id, koszt_zdjecia, "charge:image", job["id"])
    jobs.complete(conn, job["id"])
    conn.commit()


# ---------------------------------------------------------------------------
# Tworzenie produktu ze zdjec (Etap 2)
# ---------------------------------------------------------------------------

def _bez_nawiasow(nazwa: str) -> str:
    """Usuwa dopiski w nawiasach kwadratowych z nazwy kategorii.

    Konwencja z workflow n8n: 'Bransoletka CM miedz pozlacana 14k [ 17+3 cm ]'
    -> 'Bransoletka CM miedz pozlacana 14k'. Nawias to notatka o rozmiarze
    czy wariancie, nie czesc nazwy kategorii.
    """
    import re as _re
    return " ".join(_re.sub(r"\[[^\]]*\]", " ", nazwa or "").split())


def resolve_category(conn, shop_id: int, source_name: str) -> int | None:
    """Kategoria z mapy: dokladna nazwa, a gdy brak - najdluzszy pasujacy poczatek.

    Najpierw wycinamy dopiski w nawiasach. Najdluzszy poczatek wygrywa, wiec
    'Kolczyki Xuping Stal 316L' ma pierwszenstwo przed ogolnym 'Kolczyki'.
    Gdy nic nie pasuje - zwracamy None i zadanie zostaje wstrzymane, zamiast
    wrzucac produkt do przypadkowej kategorii.
    """
    czysta = _bez_nawiasow(source_name)

    row = conn.execute(
        "SELECT ps_category_id FROM category_map "
        "WHERE shop_id=%s AND lower(btrim(source_name))=lower(btrim(%s))",
        (shop_id, czysta)).fetchone()
    if row:
        return row["ps_category_id"]

    row = conn.execute(
        "SELECT ps_category_id FROM category_map "
        "WHERE shop_id=%s "
        "  AND lower(btrim(%s)) LIKE lower(btrim(source_name)) || '%%' "
        "ORDER BY length(btrim(source_name)) DESC LIMIT 1",
        (shop_id, czysta)).fetchone()
    return row["ps_category_id"] if row else None


def run_product_job(conn, job: dict, shop: dict, auth_key: str, openai_key: str,
                    data_dir: str, write_enabled: bool) -> None:
    """queued -> image_processing -> product_create -> publish -> done.

    Jeden job = jeden produkt (symbol), z 1..n zdjeciami.
    Kolejnosc jest wazna: zdjecia obrabiamy PRZED utworzeniem produktu,
    zeby nie zostawic w sklepie produktu bez zdjec, gdy obrobka padnie.
    """
    from decimal import Decimal
    from app.prestashop import PrestaShopClient

    tenant_id, payload = job["tenant_id"], job["payload"]
    result = dict(job.get("result") or {})
    symbol = payload["symbol"]

    category_name = payload.get("category")
    if not category_name:
        jobs.hold(conn, job["id"], "Brak kategorii w nazwie pliku")
        conn.commit()
        return
    category_id = resolve_category(conn, shop["id"], category_name)
    if category_id is None:
        jobs.hold(conn, job["id"],
                  f"Brak kategorii w mapie: '{category_name}' - uzupelnij mape kategorii")
        conn.commit()
        return

    # --- etap 1: obrobka zdjec --------------------------------------------
    if job["stage"] in ("queued", "image_processing"):
        jobs.set_stage(conn, job["id"], "image_processing")
        conn.commit()

        out_dir = Path(data_dir) / "processed" / str(tenant_id) / str(job["id"])
        out_dir.mkdir(parents=True, exist_ok=True)

        processed, total_cost, preview = [], 0, False
        for photo in payload["photos"]:
            src = Path(photo["path"])
            if not src.exists():
                jobs.fail(conn, job["id"], f"brak pliku: {photo['orig_name']}")
                conn.commit()
                return
            res = ai_gateway.process_image(src.read_bytes(), shop.get("prompt_image") or "",
                                           openai_key)
            dst = out_dir / output_filename(photo["orig_name"])
            dst.write_bytes(res.output_bytes)
            processed.append({"path": str(dst), "index": photo["index"]})
            total_cost += (terms.load(conn, tenant_id).cost_image if res.cost_credits else 0)
            preview = preview or res.preview_mode

        if total_cost and credits.credits_enabled(conn, tenant_id) and credits.get_balance(conn, tenant_id) < total_cost:
            jobs.hold(conn, job["id"], "brak kredytow na obrobke zdjec")
            conn.commit()
            return

        result.update({"processed": processed, "_cost": total_cost, "_preview": preview})
        jobs.set_stage(conn, job["id"], "product_create", result_patch=result)
        conn.commit()
        job["stage"] = "product_create"

    # --- etap 2: utworzenie produktu + zdjecia + cecha ---------------------
    # Kazdy pod-krok zapisuje checkpoint w result, wiec po awarii w polowie
    # job kontynuuje od miejsca przerwania: nie duplikuje zdjec, nie gubi
    # kategorii ani marki. (Zasada z Zalozen: kazdy etap wznawialny.)
    if not write_enabled:
        jobs.hold(conn, job["id"],
                  "zdjecia gotowe - tworzenie produktu wylaczone (WRITE_ENABLED=0)")
        conn.commit()
        return

    from app.naming import detect_manufacturer

    def _checkpoint():
        jobs.set_stage(conn, job["id"], "publish", result_patch=result)
        conn.commit()

    ps = PrestaShopClient(shop["base_url"], auth_key)
    try:
        vat = Decimal(str(shop.get("vat_rate", "0.23")))
        price_net = (Decimal(str(payload["price_gross"])) / (Decimal(1) + vat)
                     ).quantize(Decimal("0.000001"))

        # -- 2a: produkt (utworz albo podepnij istniejacy) --
        product_id = job.get("product_id") or result.get("product_id")
        if not product_id:
            existing = ps.find_by_reference(symbol)
            if existing:
                product_id = existing
                result["duplicate"] = True   # istnial: dokladamy tylko zdjecia
            else:
                # producent po ID (Merebilo=2, Xuping=3, Chuangmei=107) - pewne,
                # nie wymaga odczytu nazw przez API i nie zaklada duplikatow
                brand_name, manufacturer_id = detect_manufacturer(category_name, symbol)
                result["brand"] = brand_name
                result["manufacturer_id"] = manufacturer_id

                category_ids = ps.category_path(category_id) or [category_id]
                product_id = ps.create_product(
                    name=f"{category_name} - {symbol}", reference=symbol,
                    price_net=str(price_net), category_id=category_id,
                    id_tax_rules_group=int(shop.get("id_tax_rules_group", 1)),
                    active=not shop.get("create_inactive", True),
                    id_manufacturer=manufacturer_id,
                    category_ids=category_ids)
                result["created_inactive"] = bool(shop.get("create_inactive", True))

            result["product_id"] = product_id
            conn.execute("UPDATE jobs SET product_id=%s WHERE id=%s",
                         (product_id, job["id"]))
            _checkpoint()

        # -- 2b: kategoria domyslna (pomijana dla duplikatow) --
        if not result.get("duplicate") and not result.get("category_fixed"):
            ps.fix_default_category(product_id, category_id)   # kwirk: root ID 2 -> 404
            result["category_fixed"] = True
            _checkpoint()

        # -- 2c: zdjecia (kazde z osobna odhaczane) --
        uploaded = set(result.get("photos_uploaded") or [])
        for photo in sorted(result["processed"], key=lambda x: x["index"]):
            if photo["index"] in uploaded:
                continue
            ps.upload_product_image(product_id, Path(photo["path"]).read_bytes(),
                                    Path(photo["path"]).name)
            uploaded.add(photo["index"])
            result["photos_uploaded"] = sorted(uploaded)
            _checkpoint()

        # -- 2d: rozmiar jako cecha --
        size = payload.get("size")
        if size and shop.get("id_size_feature") and not result.get("feature_done"):
            ps.set_product_feature(product_id, int(shop["id_size_feature"]), f"{size} cm")
            result["feature_done"] = True
            _checkpoint()

        # -- 2e: rozliczenie i koniec --
        cost = int(result.get("_cost", 0))
        if cost and not result.get("charged"):
            credits.charge(conn, tenant_id, cost, "charge:product", job["id"])
            result["charged"] = True
        jobs.set_stage(conn, job["id"], "done", result_patch=result)
        jobs.complete(conn, job["id"])
        conn.commit()
    finally:
        ps.close()
