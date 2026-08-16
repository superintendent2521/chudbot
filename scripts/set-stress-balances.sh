#!/usr/bin/env bash
set -Eeuo pipefail

readonly DATABASE_NAME="economy_test"
readonly POSTGRES_SERVICE="postgres"
readonly POSTGRES_USER="postgres"
readonly TARGET_BALANCE=50000
readonly REPO_DIR="/home/rhys/chudite"

cd "$REPO_DIR"

docker compose exec -T "$POSTGRES_SERVICE" \
  psql \
    --username="$POSTGRES_USER" \
    --dbname="$DATABASE_NAME" \
    --set=ON_ERROR_STOP=1 \
    --set=target_balance="$TARGET_BALANCE" <<'SQL'
BEGIN;

UPDATE economy_accounts
SET balance = :'target_balance';

SELECT COUNT(*) AS accounts_at_target_balance
FROM economy_accounts
WHERE balance = :'target_balance';

COMMIT;
SQL
