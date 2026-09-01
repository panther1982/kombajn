#!/usr/bin/env bash
# Generuje sekrety do .env. Uruchom raz na VPS: bash gen_secrets.sh
# Wymaga tylko python3 (bez dodatkowych bibliotek).
set -euo pipefail
PGPASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
SESSION=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
FERNET=$(python3 -c "import os,base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
echo "POSTGRES_PASSWORD=$PGPASS"
echo "DATABASE_URL=postgresql://kombajn:$PGPASS@postgres:5432/kombajn"
echo "FERNET_KEY=$FERNET"
echo "SESSION_SECRET=$SESSION"
echo ""
echo "^ Wklej te 4 linie do .env (nadpisujac puste)."
echo "  Reszte (ANTHROPIC_API_KEY, COOKIE_SECURE=1, WEB_PORT=8080) uzupelnij recznie."
