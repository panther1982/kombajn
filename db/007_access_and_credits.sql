-- Kombajn — migracja 007: blokada kont + wylaczenie kredytow per najemca
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/007_access_and_credits.sql

-- Konto mozna wylaczyc bez kasowania (zachowuje historie zadan).
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

-- Najemca z wylaczonymi kredytami nie ma pobieranych oplat (charge przechodzi bez potracen).
ALTER TABLE tenants ADD COLUMN IF NOT EXISTS credits_enabled BOOLEAN NOT NULL DEFAULT true;
