-- Kombajn SaaS — schemat bazy (Etap 0)
-- Zasada z Zalozen: kazdy etap ma status i daje sie wznowic po awarii.
-- Kolejka zadan zyje w Postgresie (SELECT ... FOR UPDATE SKIP LOCKED),
-- wiec nie ma osobnego brokera, ktory moglby sie "zaciac" jak lock w n8n.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Najemcy (klienci SaaS). W Etapie 0 bedzie tu jeden wiersz: Ty.
-- ---------------------------------------------------------------------------
CREATE TABLE tenants (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'suspended')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Saldo kredytow — autorytatywny biezacy stan, blokowany wierszowo przy pobraniu.
-- ---------------------------------------------------------------------------
CREATE TABLE tenant_credits (
    tenant_id   BIGINT      PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    balance     BIGINT      NOT NULL DEFAULT 0 CHECK (balance >= 0)
);

-- Ksiega kredytow — append-only, pelna historia do audytu i rozliczen.
CREATE TABLE credit_ledger (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    delta         BIGINT      NOT NULL,          -- + doladowanie, - zuzycie
    balance_after BIGINT      NOT NULL,
    reason        TEXT        NOT NULL,          -- np. 'topup', 'charge:description'
    job_id        BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_tenant ON credit_ledger(tenant_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Sklepy klienta. auth_key jest szyfrowany po stronie aplikacji (Fernet)
-- i nigdy nie trafia do bazy jako plaintext ani do logow.
-- ---------------------------------------------------------------------------
CREATE TABLE shops (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id           BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform            TEXT        NOT NULL DEFAULT 'prestashop'
                                    CHECK (platform IN ('prestashop')),
    base_url            TEXT        NOT NULL,               -- https://sklep.merebilo.eu
    auth_key_encrypted  BYTEA       NOT NULL,               -- klucz webservice, zaszyfrowany
    prompt_ref          TEXT,                               -- sciezka/id promptu klienta
    params              JSONB       NOT NULL DEFAULT '{}',  -- jezyk, kategorie, ton B2B itd.
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_shops_tenant ON shops(tenant_id);

-- ---------------------------------------------------------------------------
-- Zadania. Jedno zadanie = jeden produkt.
-- stage odwzorowuje etapy z Zalozen; job wznawia sie od zapisanego stage.
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id         BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    shop_id           BIGINT      NOT NULL REFERENCES shops(id) ON DELETE CASCADE,

    type              TEXT        NOT NULL DEFAULT 'description'
                                  CHECK (type IN ('description', 'full_product')),

    -- status = gdzie job jest w kolejce
    status            TEXT        NOT NULL DEFAULT 'pending'
                                  CHECK (status IN ('pending', 'running', 'done', 'failed', 'held')),
    -- stage = na ktorym etapie produkcji job stanal (do wznowienia)
    stage             TEXT        NOT NULL DEFAULT 'queued'
                                  CHECK (stage IN (
                                      'queued',
                                      'image_processing',
                                      'product_recognition',
                                      'product_create',
                                      'description_seo',
                                      'publish',
                                      'done'
                                  )),

    product_ref       TEXT,                                 -- reference/symbol produktu
    product_id        BIGINT,                               -- ID w PrestaShop (gdy juz jest)

    attempts          INT         NOT NULL DEFAULT 0,
    last_error        TEXT,

    credits_reserved  BIGINT      NOT NULL DEFAULT 0,       -- ile kredytow juz pobrano
    payload           JSONB       NOT NULL DEFAULT '{}',    -- dane wejsciowe etapu
    result            JSONB       NOT NULL DEFAULT '{}',    -- wynik (opis, seo, alt...)

    locked_at         TIMESTAMPTZ,                          -- kiedy worker przejal job
    locked_by         TEXT,                                 -- id workera
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Kolejka pobiera po tym indeksie: najstarsze pending najpierw.
CREATE INDEX idx_jobs_queue ON jobs(status, created_at) WHERE status IN ('pending', 'running');
CREATE INDEX idx_jobs_tenant ON jobs(tenant_id, created_at DESC);
