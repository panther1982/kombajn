-- Kombajn — migracja 011: indywidualne warunki klienta + zaproszenia mailowe

-- Warunki per klient. NULL = wartosc domyslna aplikacji.
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cost_description INT;   -- kredyty za opis
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS cost_image       INT;   -- kredyty za zdjecie
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limit_daily      INT;   -- max operacji/dobe
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS limit_monthly    INT;   -- max operacji/miesiac
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allow_descriptions BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allow_images       BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS allow_products     BOOLEAN NOT NULL DEFAULT true;

-- Zaproszenia: klient dostaje link i sam ustawia haslo.
CREATE TABLE IF NOT EXISTS invitations (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    token       TEXT        NOT NULL UNIQUE,
    email       TEXT        NOT NULL,
    tenant_id   BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role        TEXT        NOT NULL DEFAULT 'member',
    invited_by  BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);
