-- Kombajn — migracja 008: ochrona logowania przed brute-force
--   docker compose exec -T postgres psql -U kombajn -d kombajn < db/008_login_throttle.sql

CREATE TABLE IF NOT EXISTS login_attempts (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ip          TEXT        NOT NULL,
    email       TEXT        NOT NULL,
    success     BOOLEAN     NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time ON login_attempts(ip, created_at DESC);
