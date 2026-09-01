-- Kombajn — migracja 003: obrobka zdjec + lancuch promptow opisow
-- Wgranie na dzialajacej bazie:
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/003_images_and_prompts.sql

-- Lancuch promptow jak w dzialajacych skryptach: analiza -> opis -> meta.
-- Kolumna `prompt` (istniejaca) pelni role promptu OPISU.
ALTER TABLE shops ADD COLUMN IF NOT EXISTS prompt_analysis TEXT NOT NULL DEFAULT '';
ALTER TABLE shops ADD COLUMN IF NOT EXISTS prompt_meta     TEXT NOT NULL DEFAULT '';
ALTER TABLE shops ADD COLUMN IF NOT EXISTS prompt_image    TEXT NOT NULL DEFAULT '';

-- Zadania obrobki zdjec nie sa przypisane do sklepu.
ALTER TABLE jobs ALTER COLUMN shop_id DROP NOT NULL;

-- Nowy typ zadania: image.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
    CHECK (type IN ('description', 'full_product', 'image'));

-- Oryginalna nazwa pliku dla zadan zdjec (wyszukiwanie/porzadek w panelu).
CREATE INDEX IF NOT EXISTS idx_jobs_tenant_type ON jobs(tenant_id, type, created_at DESC);
