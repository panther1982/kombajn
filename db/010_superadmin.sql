-- Kombajn — migracja 010: rola superadmin (wlasciciel platformy)
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/010_superadmin.sql
--
-- Dwa poziomy: superadmin (widzi wszystkie tenanty) i member (zwykly uzytkownik).
-- Rola 'admin' i 'owner' z wczesniejszych migracji zostaja dozwolone dla zgodnosci,
-- ale nowa hierarchia uzywa tylko superadmin/member.

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check
    CHECK (role IN ('superadmin', 'admin', 'owner', 'member'));
