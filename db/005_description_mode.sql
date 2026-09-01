-- Kombajn — migracja 005: tryb generowania opisow
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/005_description_mode.sql

-- 'single' = jeden prompt zwracajacy JSON (prompt produkcyjny z n8n)
-- 'chain'  = lancuch trzech promptow (analiza -> opis -> meta)
ALTER TABLE shops ADD COLUMN IF NOT EXISTS description_mode TEXT NOT NULL DEFAULT 'single'
    CHECK (description_mode IN ('single', 'chain'));
