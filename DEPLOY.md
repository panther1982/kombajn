# Wdrożenie na VPS — Kombajn (test.merebilo.eu)

Startujemy na sklepie testowym. Produkcji nie dotykamy, dopóki nie sprawdzimy
całości na `test.merebilo.eu`.

## Bezpieczeństwo tego wdrożenia

- Postgres nie ma wystawionego portu — jest dostępny tylko w sieci compose, nie z internetu.
- Panel słucha na `127.0.0.1:8080` VPS-a. TLS dokładasz swoim reverse proxy (masz już jedno dla n8n).
- Klucz webservice sklepu jest szyfrowany (Fernet), nie ma go w bazie plaintextem ani w logach.
- Osobna baza Postgres, osobny stack — nie koliduje z n8n (n8n ma swój port 5678).

## Kroki (na VPS)

1. Skopiuj katalog projektu na serwer (np. `scp -r kombajn user@vps:/opt/kombajn`).

2. Wejdź do katalogu i przygotuj `.env`:

       cd /opt/kombajn
       cp .env.example .env
       bash gen_secrets.sh        # wypisze 4 gotowe linie z sekretami

   Wklej te 4 linie do `.env` (nadpisując puste). Uzupełnij ręcznie:
   - `ANTHROPIC_API_KEY=` — na razie zostaw puste (tryb testowy, bez wywołań AI)
   - `COOKIE_SECURE=1`
   - `WEB_PORT=8080`

3. Zbuduj i uruchom (baza inicjalizuje schemat sama przy pierwszym starcie):

       docker compose up -d --build
       docker compose ps           # postgres powinien być "healthy"

4. Załóż konto i podłącz sklep testowy (klucz podaj interaktywnie, nie w komendzie):

       docker compose run --rm web python -m scripts.create_user \
           --new-tenant "Merebilo" --email ty@merebilo.pl
       # zapamiętaj id najemcy (pierwszy = 1)

       docker compose run --rm web python -m scripts.add_shop \
           --tenant-id 1 --base-url https://test.merebilo.eu
       # wpisz klucz webservice, gdy poprosi

5. Wejdź do panelu przez swój reverse proxy (patrz niżej), zaloguj się,
   otwórz sklep i wklej prompt oraz parametry.

6. Test bezpieczny — TYLKO ODCZYT ze sklepu testowego (nic nie zapisuje):

       docker compose run --rm web python -m scripts.enqueue_missing_descriptions \
           --shop-id 1 --limit 5

   Zobaczysz, czy silnik poprawnie widzi produkty bez opisu na `test.merebilo.eu`.

## Reverse proxy (przykład: Caddy)

    panel.twojadomena.pl {
        reverse_proxy 127.0.0.1:8080
    }

Nginx analogicznie: `proxy_pass http://127.0.0.1:8080;` w bloku `server` z certyfikatem.
Endpoint `/health` zwraca `{"status":"ok"}` — dobre do health-checku proxy.

## Aktualizacje kodu później

Pliki `.sql` z `db/` uruchamiają się automatycznie tylko przy PIERWSZEJ inicjalizacji bazy.
Kolejne zmiany schematu wgrywasz ręcznie:

    docker compose exec -T postgres psql -U kombajn -d kombajn < db/003_cos.sql

## Zanim włączymy zapis i realne AI

To osobny krok, świadomie odłożony:
1. Wpięcie `prompt_opisy_produktow_v2.md` i modelu w `app/ai_gateway.py`.
2. Potwierdzenie pól przy PUT w `app/prestashop.py` na sklepie testowym.
3. Ustawienie przelicznika kredytów.

## Wielozadaniowość (równoległe przetwarzanie)

Dwie osobne pule workerów, żeby partia dwustu zdjęć nie blokowała opisów:

| Pula           | Zadania              | Zmienna w `.env`  | Domyślnie |
|----------------|----------------------|-------------------|-----------|
| `worker`       | `description`        | `WORKER_OPISY`    | 3         |
| `worker-media` | `image`, `product`   | `WORKER_ZDJECIA`  | 2         |

Zmiana liczby bez przebudowy obrazu:

    echo "WORKER_OPISY=5" >> .env
    docker compose up -d

Ile ma sens:
- **Opisy** — przyspieszenie prawie liniowe. Limit to tempo Anthropic i moc VPS-a;
  przy 2 vCPU rozsądny sufit to 4–6.
- **Zdjęcia** — sufit narzuca OpenAI (~20 obrazów/min na cały klucz), a tempa pilnuje
  wspólna rezerwacja w bazie (`app/tempo.py`, tabela `rate_slots`). Powyżej 2–3
  kontenerów nic nie przyspieszy — workery będą tylko czekać na swój slot.
  Po podniesieniu tieru w OpenAI zmniejsz `IMAGE_MIN_INTERVAL_SECONDS`.

Kto co przetwarza:

    docker compose ps
    docker compose exec -T postgres psql -U kombajn -d kombajn \
      -c "SELECT locked_by, type, count(*) FROM jobs WHERE status='running' GROUP BY 1,2"
