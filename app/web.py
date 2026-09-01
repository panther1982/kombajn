"""Panel Kombajn — FastAPI + Jinja (renderowane po stronie serwera).

Uruchomienie:
    uvicorn app.web:app --host 0.0.0.0 --port 8080
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import auth, credits, db
from app.url_guard import validate_shop_url, UnsafeURL
from app.config import Settings
from app.crypto import encrypt

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

settings = Settings.load()
db.init_pool(settings.database_url)

app = FastAPI(title="Kombajn — panel")


@app.get("/health")
def health():
    return {"status": "ok"}


app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],
    https_only=os.environ.get("COOKIE_SECURE", "0") == "1",
    same_site="lax",
    max_age=int(os.environ.get("SESSION_MAX_AGE", str(24 * 3600))),  # wygasa po 24h
)


# --- pomocnicze --------------------------------------------------------------

def _current_user(request: Request, conn):
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = auth.get_user_by_id(conn, uid)
    # wylaczone konto = natychmiast wylogowane, nawet z otwarta sesja
    if user and not user.get("is_active", True):
        return None
    return user


def _shop_for_user(conn, shop_id: int, user: dict):
    """Zwraca sklep tylko jesli nalezy do najemcy uzytkownika (izolacja)."""
    return conn.execute(
        "SELECT * FROM shops WHERE id = %s AND tenant_id = %s",
        (shop_id, user["tenant_id"]),
    ).fetchone()


# --- logowanie ---------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None):
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("login.html",
                                      {"request": request, "csrf": csrf, "error": error})


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          csrf_token: str = Form(...)):
    if not auth.check_csrf(request.session, csrf_token):
        return RedirectResponse("/login?error=Sesja+wygasla,+sprobuj+ponownie", status_code=303)

    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")

    with db.connection() as conn:
        if auth.is_login_blocked(conn, ip, email):
            return RedirectResponse(
                "/login?error=Zbyt+wiele+prob.+Odczekaj+15+minut", status_code=303)

        user = auth.get_user_by_email(conn, email)
        ok = bool(user and auth.verify_password(password, user["password_hash"]))
        auth.record_login_attempt(conn, ip, email, ok)

        if not ok:
            conn.commit()
            return RedirectResponse("/login?error=Bledny+email+lub+haslo", status_code=303)
        if not user.get("is_active", True):
            conn.commit()
            return RedirectResponse("/login?error=Konto+wylaczone", status_code=303)

        auth.clear_login_attempts(conn, ip, email)
        conn.commit()

    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- dashboard ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        tenant = conn.execute("SELECT * FROM tenants WHERE id = %s", (user["tenant_id"],)).fetchone()
        balance = credits.get_balance(conn, user["tenant_id"])
        shops = conn.execute(
            "SELECT id, base_url, platform FROM shops WHERE tenant_id = %s ORDER BY id",
            (user["tenant_id"],),
        ).fetchall()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user, "tenant": tenant,
        "balance": balance, "shops": shops,
    })


# --- edycja sklepu (prompt + ustawienia) -------------------------------------

@app.get("/shop/{shop_id}", response_class=HTMLResponse)
def shop_form(request: Request, shop_id: int, saved: int = 0, error: str | None = None):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        shop = _shop_for_user(conn, shop_id, user)
        if not shop:
            return RedirectResponse("/", status_code=303)
        balance = credits.get_balance(conn, user["tenant_id"])
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("shop.html", {
        "request": request, "user": user, "shop": shop, "csrf": csrf,
        "balance": balance, "saved": saved, "error": error,
        "params_json": json.dumps(shop["params"], ensure_ascii=False, indent=2),
    })


@app.post("/shop/{shop_id}")
def shop_save(request: Request, shop_id: int,
              prompt: str = Form(""),
              prompt_analysis: str = Form(""),
              prompt_meta: str = Form(""),
              prompt_image: str = Form(""),
              description_mode: str = Form("single"),
              base_url: str = Form(...),
              vat_rate: str = Form("23"),
              id_tax_rules_group: str = Form("1"),
              id_size_feature: str = Form(""),
              create_inactive: str = Form(""),
              params_json: str = Form("{}"),
              new_auth_key: str = Form(""),
              csrf_token: str = Form(...)):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse(f"/shop/{shop_id}?error=Sesja+wygasla", status_code=303)
        shop = _shop_for_user(conn, shop_id, user)
        if not shop:
            return RedirectResponse("/", status_code=303)

        # SSRF: adres sklepu musi byc publiczny. Wlasciciel moze uzyc http
        # (sklep testowy); klienci SaaS - tylko https.
        allow_http = bool(user.get("is_owner_account"))
        try:
            base_url = validate_shop_url(base_url, allow_http=allow_http)
        except UnsafeURL as e:
            from urllib.parse import quote
            return RedirectResponse(f"/shop/{shop_id}?error={quote(str(e))}", status_code=303)

        try:
            params = json.loads(params_json) if params_json.strip() else {}
            if not isinstance(params, dict):
                raise ValueError("params musi byc obiektem JSON")
        except (json.JSONDecodeError, ValueError) as e:
            return RedirectResponse(f"/shop/{shop_id}?error=Bledny+JSON+parametrow", status_code=303)

        # snapshot kazdego zmienionego promptu do historii (z etykieta pola)
        for fieldname, new_val in (("prompt", prompt), ("prompt_analysis", prompt_analysis),
                                    ("prompt_meta", prompt_meta), ("prompt_image", prompt_image)):
            if new_val != (shop.get(fieldname) or ""):
                conn.execute(
                    "INSERT INTO prompt_history (shop_id, prompt, changed_by) VALUES (%s,%s,%s)",
                    (shop_id, f"[{fieldname}]\n{shop.get(fieldname) or ''}", user["id"]),
                )

        try:
            vat = float(vat_rate.replace(",", ".")) / 100.0
            if not 0 <= vat < 1:
                raise ValueError
        except ValueError:
            return RedirectResponse(f"/shop/{shop_id}?error=Bledna+stawka+VAT", status_code=303)

        conn.execute(
            "UPDATE shops SET prompt=%s, prompt_analysis=%s, prompt_meta=%s, "
            "prompt_image=%s, description_mode=%s, base_url=%s, params=%s, vat_rate=%s, "
            "id_tax_rules_group=%s, id_size_feature=%s, create_inactive=%s WHERE id=%s",
            (prompt, prompt_analysis, prompt_meta, prompt_image,
             description_mode if description_mode in ("single", "chain") else "single",
             base_url.rstrip("/"), json.dumps(params), vat,
             int(id_tax_rules_group) if id_tax_rules_group.strip().isdigit() else 1,
             int(id_size_feature) if id_size_feature.strip().isdigit() else None,
             create_inactive == "1",
             shop_id),
        )
        if new_auth_key.strip():
            conn.execute(
                "UPDATE shops SET auth_key_encrypted = %s WHERE id = %s",
                (encrypt(new_auth_key.strip(), settings.fernet_key), shop_id),
            )
        conn.commit()
    return RedirectResponse(f"/shop/{shop_id}?saved=1", status_code=303)


# ============================================================================
# ZDJECIA — upload do obrobki + pobieranie wynikow
# ============================================================================
import uuid as _uuid
from pathlib import Path as _Path

from fastapi import UploadFile, File
from fastapi.responses import FileResponse

from app import jobs as _jobs

_ALLOWED_EXT = {".jpg", ".jpeg", ".png"}


@app.get("/images", response_class=HTMLResponse)
def images_page(request: Request, msg: str | None = None, error: str | None = None):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        rows = conn.execute(
            "SELECT id, status, stage, payload, result, last_error, created_at "
            "FROM jobs WHERE tenant_id=%s AND type='image' ORDER BY id DESC LIMIT 50",
            (user["tenant_id"],),
        ).fetchall()
        balance = credits.get_balance(conn, user["tenant_id"])
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("images.html", {
        "request": request, "user": user, "jobs": rows, "csrf": csrf,
        "balance": balance, "msg": msg, "error": error,
    })


@app.post("/images/upload")
async def images_upload(request: Request, files: list[UploadFile] = File(...),
                        csrf_token: str = Form(...)):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/images?error=Sesja+wygasla", status_code=303)

        incoming = _Path(settings.data_dir) / "incoming" / str(user["tenant_id"])
        incoming.mkdir(parents=True, exist_ok=True)

        accepted, rejected = 0, 0
        for f in files:
            ext = _Path(f.filename or "").suffix.lower()
            if ext not in _ALLOWED_EXT:
                rejected += 1
                continue
            dest = incoming / f"{_uuid.uuid4().hex}{ext}"
            try:
                dest.write_bytes(await f.read())
            except OSError as e:
                return RedirectResponse(
                    f"/images?error=Brak+dostepu+do+katalogu+danych+({type(e).__name__})."
                    f"+Sprawdz+uprawnienia+wolumenu+/data", status_code=303)
            _jobs.enqueue(conn, user["tenant_id"], shop_id=None, product_ref=None,
                          job_type="image",
                          payload={"input_path": str(dest), "orig_name": f.filename})
            accepted += 1
        conn.commit()

    msg = f"Przyjeto+{accepted}+zdjec"
    if rejected:
        msg += f",+odrzucono+{rejected}+(zly+format)"
    return RedirectResponse(f"/images?msg={msg}", status_code=303)


@app.get("/images/file/{job_id}")
def images_download(request: Request, job_id: int):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        row = conn.execute(
            "SELECT result FROM jobs WHERE id=%s AND tenant_id=%s AND type='image'",
            (job_id, user["tenant_id"]),
        ).fetchone()
    if not row or not row["result"].get("output_path"):
        return RedirectResponse("/images?error=Plik+niedostepny", status_code=303)
    path = row["result"]["output_path"]
    # nazwa pobieranego pliku = dokladnie nazwa wynikowa (bez prefiksow)
    return FileResponse(path, media_type="image/jpeg",
                        filename=_Path(path).name)


# ============================================================================
# OPISY — skan z kierunkiem + podglad wynikow
# ============================================================================

@app.get("/generate", response_class=HTMLResponse)
def generate_page(request: Request, msg: str | None = None, error: str | None = None):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        shops = conn.execute("SELECT id, base_url FROM shops WHERE tenant_id=%s ORDER BY id",
                             (user["tenant_id"],)).fetchall()
        rows = conn.execute(
            "SELECT id, product_id, status, stage, result, last_error, created_at "
            "FROM jobs WHERE tenant_id=%s AND type='description' ORDER BY id DESC LIMIT 30",
            (user["tenant_id"],),
        ).fetchall()
        balance = credits.get_balance(conn, user["tenant_id"])
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("generate.html", {
        "request": request, "user": user, "shops": shops, "jobs": rows,
        "csrf": csrf, "balance": balance, "msg": msg, "error": error,
    })


@app.post("/generate/scan")
def generate_scan(request: Request, shop_id: int = Form(...), mode: str = Form(...),
                  start_id: str = Form(""), max_jobs: int = Form(20),
                  csrf_token: str = Form(...)):
    from app.prestashop import PrestaShopClient
    from app.crypto import decrypt as _decrypt

    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/generate?error=Sesja+wygasla", status_code=303)
        shop = _shop_for_user(conn, shop_id, user)
        if not shop:
            return RedirectResponse("/generate?error=Nieznany+sklep", status_code=303)

        sid = int(start_id) if start_id.strip().isdigit() else None
        if mode in ("from_id_down", "from_id_up") and sid is None:
            return RedirectResponse("/generate?error=Podaj+ID+startowe", status_code=303)

        key = _decrypt(bytes(shop["auth_key_encrypted"]), settings.fernet_key)
        ps = PrestaShopClient(shop["base_url"], key)
        try:
            ids, scanned = ps.scan_products(mode=mode, start_id=sid,
                                            max_jobs=max(1, min(max_jobs, 200)))
        finally:
            ps.close()

        # pomin produkty, ktore juz maja aktywne/zakonczone zadanie
        existing = {r["product_id"] for r in conn.execute(
            "SELECT product_id FROM jobs WHERE tenant_id=%s AND type='description' "
            "AND status IN ('pending','running','held','done')", (user["tenant_id"],)).fetchall()}
        new_ids = [pid for pid in ids if pid not in existing]

        for pid in new_ids:
            _jobs.enqueue(conn, user["tenant_id"], shop["id"], product_ref=str(pid),
                          job_type="description", payload={"product_id": pid})
        conn.commit()

    msg = (f"Przeskanowano+{scanned}+produktow,+{len(ids)}+wymaga+opisu,"
           f"+dodano+{len(new_ids)}+zadan")
    if scanned and not ids:
        msg += "+(wszystkie+maja+juz+opisy)"
    elif not scanned:
        msg += "+(brak+produktow+w+tym+zakresie+ID)"
    return RedirectResponse(f"/generate?msg={msg}", status_code=303)


# ============================================================================
# MAPA KATEGORII
# ============================================================================

@app.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, msg: str | None = None, error: str | None = None):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        shops = conn.execute("SELECT id, base_url FROM shops WHERE tenant_id=%s ORDER BY id",
                             (user["tenant_id"],)).fetchall()
        rows = conn.execute(
            "SELECT cm.id, cm.source_name, cm.ps_category_id, cm.shop_id "
            "FROM category_map cm JOIN shops s ON s.id=cm.shop_id "
            "WHERE s.tenant_id=%s ORDER BY cm.source_name", (user["tenant_id"],)).fetchall()
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("categories.html", {
        "request": request, "user": user, "shops": shops, "rows": rows,
        "csrf": csrf, "msg": msg, "error": error})


@app.post("/categories/add")
def categories_add(request: Request, shop_id: int = Form(...), bulk: str = Form(""),
                   csrf_token: str = Form(...)):
    """Wklej wiele linii naraz: 'Nazwa kategorii = 15' (jedna para na linie)."""
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/categories?error=Sesja+wygasla", status_code=303)
        if not _shop_for_user(conn, shop_id, user):
            return RedirectResponse("/categories?error=Nieznany+sklep", status_code=303)

        added, bad = 0, []
        for line in bulk.splitlines():
            line = line.strip()
            if not line:
                continue
            sep = "=" if "=" in line else (";" if ";" in line else None)
            if not sep:
                bad.append(line); continue
            name, _, cid = line.partition(sep)
            name, cid = name.strip(), cid.strip()
            if not name or not cid.isdigit():
                bad.append(line); continue
            conn.execute(
                "INSERT INTO category_map (shop_id, source_name, ps_category_id) "
                "VALUES (%s,%s,%s) ON CONFLICT (shop_id, lower(btrim(source_name))) "
                "DO UPDATE SET ps_category_id = EXCLUDED.ps_category_id",
                (shop_id, name, int(cid)))
            added += 1
        conn.commit()
    msg = f"Zapisano+{added}+kategorii"
    if bad:
        msg += f",+pominieto+{len(bad)}+blednych+linii"
    return RedirectResponse(f"/categories?msg={msg}", status_code=303)


# ============================================================================
# PRODUKTY ZE ZDJEC
# ============================================================================

@app.get("/products", response_class=HTMLResponse)
def products_page(request: Request, msg: str | None = None, error: str | None = None):
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        shops = conn.execute("SELECT id, base_url FROM shops WHERE tenant_id=%s ORDER BY id",
                             (user["tenant_id"],)).fetchall()
        rows = conn.execute(
            "SELECT id, product_ref, product_id, status, stage, payload, result, last_error "
            "FROM jobs WHERE tenant_id=%s AND type='product' ORDER BY id DESC LIMIT 50",
            (user["tenant_id"],)).fetchall()
        balance = credits.get_balance(conn, user["tenant_id"])
    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("products.html", {
        "request": request, "user": user, "shops": shops, "jobs": rows,
        "csrf": csrf, "balance": balance, "msg": msg, "error": error})


@app.post("/products/upload")
async def products_upload(request: Request, shop_id: int = Form(...),
                          files: list[UploadFile] = File(...), csrf_token: str = Form(...)):
    from app.naming import parse_photo_filename, group_by_symbol, FilenameError

    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/products?error=Sesja+wygasla", status_code=303)
        shop = _shop_for_user(conn, shop_id, user)
        if not shop:
            return RedirectResponse("/products?error=Nieznany+sklep", status_code=303)

        incoming = _Path(settings.data_dir) / "incoming" / str(user["tenant_id"])
        try:
            incoming.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return RedirectResponse(
                f"/products?error=Brak+dostepu+do+katalogu+danych+({type(e).__name__})",
                status_code=303)

        parsed, errors = [], []
        for f in files:
            name = f.filename or "(bez nazwy)"
            ext = _Path(name).suffix.lower()
            if ext not in _ALLOWED_EXT:
                dozwolone = ", ".join(sorted(_ALLOWED_EXT))
                powod = (f"rozszerzenie '{ext}' niedozwolone (dozwolone: {dozwolone})"
                         if ext else "brak rozszerzenia pliku")
                errors.append(f"{name} - {powod}")
                continue
            try:
                p = parse_photo_filename(name)
            except FilenameError as e:
                errors.append(f"{name} - {e}")
                continue
            try:
                data = await f.read()
                if len(data) < 100:
                    errors.append(f"{name} - plik pusty lub uszkodzony ({len(data)} B)")
                    continue
                dest = incoming / f"{_uuid.uuid4().hex}{ext}"
                dest.write_bytes(data)
            except Exception as e:
                # jeden zly plik nie przerywa calej partii
                errors.append(f"{name} - blad zapisu: {type(e).__name__}")
                continue
            parsed.append((p, dest))

        groups = group_by_symbol([p for p, _ in parsed])
        paths = {(p.orig_name, p.photo_index): str(d) for p, d in parsed}

        created = 0
        for symbol, items in groups.items():
            photos = [{"path": paths[(i.orig_name, i.photo_index)],
                       "orig_name": i.orig_name, "index": i.photo_index} for i in items]
            first = items[0]
            _jobs.enqueue(conn, user["tenant_id"], shop["id"], product_ref=symbol,
                          job_type="product",
                          payload={"symbol": symbol, "category": first.category,
                                   "price_gross": str(first.price_gross),
                                   "size": first.size, "photos": photos})
            created += 1
        conn.commit()

    from urllib.parse import quote
    msg = f"Utworzono {created} produktow z {len(parsed)} zdjec"
    url = f"/products?msg={quote(msg)}"
    if errors:
        # szczegoly kazdego odrzuconego pliku - nazwa + konkretny powod
        detale = " | ".join(errors[:20])
        if len(errors) > 20:
            detale += f" | ... (+{len(errors) - 20} wiecej)"
        url += f"&error={quote(f'Odrzucono {len(errors)}: {detale}')}"
    return RedirectResponse(url, status_code=303)


# ============================================================================
# WZNAWIANIE ZADAN (held / failed)
# ============================================================================

@app.post("/jobs/retry")
def jobs_retry(request: Request, job_type: str = Form("description"),
               csrf_token: str = Form(...)):
    """Wznawia zadania wstrzymane i nieudane.

    Zadania 'held' (np. brak kredytow) nie wracaja do kolejki same - worker
    bierze tylko 'pending'. Ten przycisk przywraca je do puli, zerujac licznik
    prob. Checkpointy w wyniku sprawiaja, ze wznowienie nie powtarza krokow,
    ktore juz sie udaly.
    """
    with db.connection() as conn:
        user = _current_user(request, conn)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/generate?error=Sesja+wygasla", status_code=303)

        n = conn.execute(
            "UPDATE jobs SET status='pending', attempts=0, last_error=NULL, "
            "locked_by=NULL, locked_at=NULL, updated_at=now() "
            "WHERE tenant_id=%s AND type=%s AND status IN ('held','failed')",
            (user["tenant_id"], job_type),
        ).rowcount
        conn.commit()

    target = {"description": "/generate", "image": "/images", "product": "/products"}.get(
        job_type, "/generate")
    return RedirectResponse(f"{target}?msg=Wznowiono+{n}+zadan", status_code=303)


# ============================================================================
# PANEL ADMINISTRACYJNY /admin — zarzadzanie uzytkownikami (tylko rola admin)
# ============================================================================

def _require_superadmin(request: Request, conn):
    """Zwraca uzytkownika, jesli jest superadminem (wlasciciel platformy).

    Superadmin widzi i zarzadza kontami WSZYSTKICH tenantow - inaczej niz
    zwykla izolacja per-tenant, ktora obowiazuje reszte systemu.
    """
    user = _current_user(request, conn)
    if not user or user.get("role") != "superadmin":
        return None
    return user


@app.get("/admin")
def admin_users(request: Request, msg: str | None = None, error: str | None = None):
    with db.connection() as conn:
        admin = _require_superadmin(request, conn)
        if not admin:
            # nie-admin nie ma prawa nawet wiedziec, ze panel istnieje
            return RedirectResponse("/", status_code=303)

        rows = conn.execute(
            "SELECT u.id, u.email, u.role, u.is_active, u.is_owner_account, "
            "       u.credits_enabled AS user_credits, u.tenant_id, "
            "       t.name AS tenant_name, t.credits_enabled AS tenant_credits "
            "FROM users u JOIN tenants t ON t.id = u.tenant_id "
            "ORDER BY t.id, u.is_owner_account DESC, u.id"
        ).fetchall()

        users = []
        for r in rows:
            # efektywne naliczanie: wlasciciel nigdy; inaczej flaga usera lub tenanta
            if r["is_owner_account"]:
                effective = False
            elif r["user_credits"] is not None:
                effective = r["user_credits"]
            else:
                effective = r["tenant_credits"]
            users.append({**dict(r), "credits_effective": effective})

    csrf = auth.ensure_csrf_token(request.session)
    return templates.TemplateResponse("admin_users.html", {
        "request": request, "user": admin, "users": users,
        "csrf": csrf, "msg": msg, "error": error,
    })


@app.post("/admin/users/{user_id}/toggle-active")
def admin_toggle_active(request: Request, user_id: int, csrf_token: str = Form(...)):
    with db.connection() as conn:
        admin = _require_superadmin(request, conn)
        if not admin:
            return RedirectResponse("/", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/admin?error=Sesja+wygasla", status_code=303)

        target = conn.execute("SELECT id, is_active, is_owner_account, email "
                              "FROM users WHERE id = %s", (user_id,)).fetchone()
        if not target:
            return RedirectResponse("/admin?error=Nie+ma+takiego+konta", status_code=303)
        if target["id"] == admin["id"]:
            return RedirectResponse(
                "/admin?error=Nie+mozesz+wylaczyc+wlasnego+konta", status_code=303)

        conn.execute("UPDATE users SET is_active = NOT is_active WHERE id = %s", (user_id,))
        conn.commit()
        stan = "wylaczone" if target["is_active"] else "wlaczone"
    return RedirectResponse(f"/admin?msg=Konto+{target['email']}+{stan}", status_code=303)


@app.post("/admin/users/{user_id}/toggle-credits")
def admin_toggle_credits(request: Request, user_id: int, csrf_token: str = Form(...)):
    with db.connection() as conn:
        admin = _require_superadmin(request, conn)
        if not admin:
            return RedirectResponse("/", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/admin?error=Sesja+wygasla", status_code=303)

        target = conn.execute("SELECT id, email, credits_enabled, is_owner_account "
                              "FROM users WHERE id = %s", (user_id,)).fetchone()
        if not target:
            return RedirectResponse("/admin?error=Nie+ma+takiego+konta", status_code=303)
        if target["is_owner_account"]:
            return RedirectResponse(
                "/admin?error=Konto+wlasciciela+nigdy+nie+nalicza+kredytow", status_code=303)

        # cykl: NULL (dziedzicz) -> true (nalicza) -> false (nie nalicza) -> NULL
        cur = target["credits_enabled"]
        new = True if cur is None else (False if cur else None)
        conn.execute("UPDATE users SET credits_enabled = %s WHERE id = %s", (new, user_id))
        conn.commit()
        opis = {None: "dziedziczy z tenanta", True: "nalicza", False: "nie nalicza"}[new]
    return RedirectResponse(f"/admin?msg=Kredyty+dla+{target['email']}:+{opis}",
                            status_code=303)


@app.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(request: Request, user_id: int,
                         new_password: str = Form(...), csrf_token: str = Form(...)):
    with db.connection() as conn:
        admin = _require_superadmin(request, conn)
        if not admin:
            return RedirectResponse("/", status_code=303)
        if not auth.check_csrf(request.session, csrf_token):
            return RedirectResponse("/admin?error=Sesja+wygasla", status_code=303)
        if len(new_password) < 8:
            return RedirectResponse(
                "/admin?error=Haslo+min+8+znakow", status_code=303)

        target = conn.execute("SELECT email FROM users WHERE id = %s", (user_id,)).fetchone()
        if not target:
            return RedirectResponse("/admin?error=Nie+ma+takiego+konta", status_code=303)
        conn.execute("UPDATE users SET password_hash = %s WHERE id = %s",
                     (auth.hash_password(new_password), user_id))
        conn.commit()
    return RedirectResponse(f"/admin?msg=Zmieniono+haslo+dla+{target['email']}",
                            status_code=303)
