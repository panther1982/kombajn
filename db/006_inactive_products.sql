-- Kombajn — migracja 006: produkty tworzone jako nieaktywne
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/006_inactive_products.sql

-- Nowe produkty powstaja wylaczone, do recznej akceptacji w PrestaShop.
ALTER TABLE shops ADD COLUMN IF NOT EXISTS create_inactive BOOLEAN NOT NULL DEFAULT true;
