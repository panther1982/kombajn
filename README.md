# Kombajn SaaS — Etap 0 (szkielet)

Natywna aplikacja w Pythonie zastępująca n8n jako silnik produktu sprzedawanego
klientom. n8n zostaje po Twojej stronie do Merebilo i prototypowania.

## Co już działa (przetestowane na żywym Postgresie)

- Kolejka zadań w Postgresie (`SELECT ... FOR UPDATE SKIP LOCKED`) — bez brokera,
  bez ryzyka zacinającego się locka jak w n8n.
- Wznawianie po awarii: osierocone zadanie „running" wraca do puli po `LOCK_TIMEOUT`.
  (Realizuje zasadę z Twoich Założeń: każdy etap ma status i daje się wznowić.)
- Księga kredytów z atomowym pobieraniem (blokada wierszowa, brak zejścia < 0).
- Klient PrestaShop z Twoimi kwirkami (odczyt gotowy; zapis w szkicu).
- Bramka AI (Twoje klucze) z pomiarem zużycia i przelicznikiem na kredyty.

## Struktura

    app/config.py        konfiguracja z env
    app/db.py            pula połączeń Postgres
    app/crypto.py        szyfrowanie kluczy webservice (Fernet)
    app/credits.py       księga kredytów (atomowo)
    app/jobs.py          silnik kolejki (SKIP LOCKED + przejmowanie osieroconych)
    app/prestashop.py    klient PrestaShop (kwirki merebilo)
    app/ai_gateway.py    bramka AI — TWOJE klucze
    app/pipeline.py      przebieg produktu, etap po etapie
    app/worker.py        pętla workera
    db/001_schema.sql        schemat bazy
    scripts/enqueue_missing_descriptions.py   test Etapu 0 (tylko odczyt sklepu)
    tests/test_engine.py  test silnika bez sieci

## Uruchomienie lokalne

    cp .env.example .env            # uzupełnij DATABASE_URL i FERNET_KEY
    python -c "from app.crypto import generate_key; print(generate_key())"  # -> FERNET_KEY
    psql "$DATABASE_URL" -f db/001_schema.sql
    python -m tests.test_engine     # powinno być "Wszystko zielone"

## Pierwszy test na Twoim sklepie (bezpieczny — tylko odczyt)

    python -m scripts.enqueue_missing_descriptions --shop-id 1 --limit 5

Zobaczysz, czy silnik poprawnie widzi produkty bez opisu. Dopiero potem
włączamy zapis i realne wywołanie AI.

## Do potwierdzenia zanim pójdzie pierwszy live zapis

1. Treść promptu `prompt_opisy_produktow_v2.md` + model (wpięcie w `ai_gateway.py`).
2. Dokładny zestaw pól przy PUT do PrestaShop (`prestashop.update_product_seo`).
3. Przelicznik kredytów: ile kredytów za opis, ile za obróbkę zdjęcia.
