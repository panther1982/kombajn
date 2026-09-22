-- Wielozadaniowosc: wspolne tempo wywolan dla WIELU workerow.
--
-- Do tej pory tempo zdjec pilnowal threading.Lock, ktory dziala tylko
-- w jednym procesie. Przy kilku kontenerach worker kazdy mial wlasny licznik
-- i razem przekraczalyby limit OpenAI (bledy 429).
--
-- Tutaj kazdy worker REZERWUJE sobie moment wywolania. Blokada wiersza
-- w Postgresie ustawia wszystkich w jednej kolejce, niezaleznie od procesu.

CREATE TABLE IF NOT EXISTS rate_slots (
    klucz      text PRIMARY KEY,
    next_at    timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Indeks pomocniczy do podgladu, kto teraz pracuje (panel/diagnostyka).
CREATE INDEX IF NOT EXISTS jobs_running_idx ON jobs (status, locked_at)
    WHERE status = 'running';
