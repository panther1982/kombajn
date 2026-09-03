-- Kombajn — migracja 013: darmowe kredyty na test, limit per domena sklepu

CREATE TABLE IF NOT EXISTS trial_grants (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    domain      TEXT        NOT NULL UNIQUE,   -- znormalizowana domena sklepu
    tenant_id   BIGINT      REFERENCES tenants(id) ON DELETE SET NULL,
    credits     INT         NOT NULL,
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ile kredytow dostaje nowy klient na test (0 = wylaczone).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS trial_granted BOOLEAN NOT NULL DEFAULT false;
