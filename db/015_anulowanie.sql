-- Kombajn — migracja 015: zatrzymanie partii w trakcie

ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check
    CHECK (status IN ('pending', 'running', 'done', 'failed', 'held', 'cancelled'));

-- Znacznik na partii: worker sprawdza go przed kazdym zadaniem.
ALTER TABLE batches ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ;
