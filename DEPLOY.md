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
