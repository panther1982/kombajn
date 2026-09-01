-- Kombajn — migracja 009: fundament SaaS
--   rola admin, przelacznik naliczania kredytow PER UZYTKOWNIK
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/009_admin_and_user_credits.sql

-- 1. Rola 'admin' obok owner/member. Admin widzi panel /admin.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('admin', 'owner', 'member'));

-- 2. Naliczanie kredytow per uzytkownik.
--    Kredyty pozostaja WSPOLNE (pula per tenant), ale ta flaga decyduje,
--    czy zadania danego uzytkownika w ogole zliczaja zuzycie.
--    NULL = dziedzicz ustawienie tenanta (zgodnosc wstecz).
ALTER TABLE users ADD COLUMN IF NOT EXISTS credits_enabled BOOLEAN;

-- 3. Znacznik "konto wlasne wlasciciela" - te 3 konta nalezą do Ciebie,
--    reszta bedzie zarzadzana osobno (przyszli klienci SaaS).
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_owner_account BOOLEAN NOT NULL DEFAULT false;
