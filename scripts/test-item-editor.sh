#!/usr/bin/env bash
# M07.03: browser + real BFF/API, exclusively on a fresh disposable cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
container_id=""
cleanup() {
  if [[ -n "$container_id" ]]; then docker rm -f "$container_id" >/dev/null; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
unset TEST_DATABASE_URL TEST_MIGRATION_DATABASE_URL TEST_EDITORIAL_MIGRATION_DATABASE_URL
unset TEST_WEB_MIGRATION_DATABASE_URL TEST_ADMIN_API_DATABASE_URL DATABASE_URL
unset TEST_KEYCLOAK_ENABLED TEST_KEYCLOAK_GOVERNANCE
container_id=$(docker run --detach --pull=never --label libris.task=issue63-integration \
  --tmpfs /var/lib/postgresql/data --publish 127.0.0.1::5432 \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_USER=libris_test \
  --env POSTGRES_DB=libris_test postgres:17-bookworm)
port=$(docker port "$container_id" 5432/tcp)
port=${port##*:}
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  if docker exec "$container_id" pg_isready -h 127.0.0.1 -U libris_test >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[[ "$ready" == 1 ]] || { echo 'PostgreSQL descartável indisponível.' >&2; exit 1; }
export TEST_ADMIN_API_DATABASE_URL="postgresql://libris_test@127.0.0.1:$port/libris_test"
export TEST_WEB_ISOLATED=1 TEST_WEB_BROWSER_ENABLED=1
cd apps/web
# Use the production build produced by npm run build / scripts/check.sh.
node --conditions=react-server --import tsx --test tests/item-editor.e2e.ts tests/item-editor-http.e2e.ts
