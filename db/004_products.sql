-- Kombajn — migracja 004: tworzenie produktow ze zdjec
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/004_products.sql

-- Ustawienia sklepu potrzebne przy tworzeniu produktu
ALTER TABLE shops ADD COLUMN IF NOT EXISTS vat_rate NUMERIC(5,4) NOT NULL DEFAULT 0.2300;
ALTER TABLE shops ADD COLUMN IF NOT EXISTS id_tax_rules_group INT NOT NULL DEFAULT 1;
ALTER TABLE shops ADD COLUMN IF NOT EXISTS id_size_feature INT;  -- cecha "Rozmiar" w PrestaShop

-- Mapa kategorii: nazwa z pliku -> ID kategorii w PrestaShop
CREATE TABLE IF NOT EXISTS category_map (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shop_id     BIGINT      NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    source_name TEXT        NOT NULL,          -- dokladnie jak w nazwie pliku
    ps_category_id INT      NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- dopasowanie bez wzgledu na wielkosc liter i biale znaki
CREATE UNIQUE INDEX IF NOT EXISTS idx_catmap_shop_name
    ON category_map (shop_id, lower(btrim(source_name)));

-- Nowy typ zadania: product (tworzenie produktu ze zdjec)
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_type_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_type_check
    CHECK (type IN ('description', 'full_product', 'image', 'product'));

-- Symbol produktu (reference) — szybkie wyszukiwanie i kontrola duplikatow
CREATE INDEX IF NOT EXISTS idx_jobs_product_ref ON jobs(tenant_id, product_ref);
