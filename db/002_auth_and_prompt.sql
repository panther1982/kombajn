-- Kombajn SaaS — migracja 002: logowanie + prompt edytowalny z panelu
-- Doklada do istniejacego schematu (schema.sql). Bezpieczna do uruchomienia raz.

-- Uzytkownicy panelu. Kazdy nalezy do najemcy (multi-tenant od poczatku).
CREATE TABLE IF NOT EXISTS users (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id      BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email          TEXT        NOT NULL UNIQUE,
    password_hash  TEXT        NOT NULL,
    role           TEXT        NOT NULL DEFAULT 'owner' CHECK (role IN ('owner', 'member')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);

-- Prompt edytowalny z panelu. Trzymamy tresc w bazie, nie w pliku.
ALTER TABLE shops ADD COLUMN IF NOT EXISTS prompt TEXT NOT NULL DEFAULT '';

-- Historia zmian promptu — tania polisa: kazdy zapis robi snapshot poprzedniego.
-- (Zgodne z Twoja zasada backupu i "cautious > fast".)
CREATE TABLE IF NOT EXISTS prompt_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id     BIGINT      NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    prompt      TEXT        NOT NULL,
    changed_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_prompt_history_shop ON prompt_history(shop_id, created_at DESC);
