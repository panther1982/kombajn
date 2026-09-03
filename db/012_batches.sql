-- Kombajn — migracja 012: partie zadan + powiadomienie mailem po zakonczeniu

CREATE TABLE IF NOT EXISTS batches (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   BIGINT      NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id     BIGINT      REFERENCES users(id) ON DELETE SET NULL,
    kind        TEXT        NOT NULL,          -- 'product' | 'image' | 'description'
    total       INT         NOT NULL DEFAULT 0,
    notify_email TEXT,                          -- komu wyslac podsumowanie
    notified_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS batch_id BIGINT
    REFERENCES batches(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id) WHERE batch_id IS NOT NULL;

-- Powiadomienia mozna wylaczyc per uzytkownik.
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_batches BOOLEAN NOT NULL DEFAULT true;
