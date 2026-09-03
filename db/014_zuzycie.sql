-- Kombajn — migracja 014: zapis zuzycia tokenow AI (do raportow i rozliczen)

CREATE TABLE IF NOT EXISTS ai_usage (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id     BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id       BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    job_id        BIGINT,
    kind          TEXT        NOT NULL,          -- 'description' | 'image'
    input_tokens  INT         NOT NULL DEFAULT 0,
    output_tokens INT         NOT NULL DEFAULT 0,
    credits       INT         NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_tenant ON ai_usage(tenant_id, created_at DESC);

-- kto zlecil zadanie - potrzebne, by rozliczyc zuzycie na konkretne konto
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES users(id) ON DELETE SET NULL;
