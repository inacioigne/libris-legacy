#!/usr/bin/env bash
# Integração opt-in: somente containers/bancos novos, sem volumes ou URLs de trabalho.
set -euo pipefail
cd "$(dirname "$0")/../apps/api"

container_id=""
cleanup() {
  if [[ -n "$container_id" ]]; then
    docker rm -f "$container_id" >/dev/null
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Não herdar configuração de integração nem permitir selecionar banco de trabalho.
unset TEST_DATABASE_URL TEST_MIGRATION_DATABASE_URL TEST_EDITORIAL_MIGRATION_DATABASE_URL
unset TEST_WEB_MIGRATION_DATABASE_URL DATABASE_URL
unset TEST_KEYCLOAK_ENABLED TEST_KEYCLOAK_GOVERNANCE

container_id=$(docker run --detach --pull=never \
  --label libris.task=issue51-integration \
  --tmpfs /var/lib/postgresql/data \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_HOST_AUTH_METHOD=trust \
  --env POSTGRES_USER=libris_test --env POSTGRES_DB=libris_test \
  postgres:17-bookworm)
port=$(docker port "$container_id" 5432/tcp)
port=${port##*:}
ready=0
for ((attempt=0; attempt<60; attempt++)); do
  # The image's temporary initialization server accepts Unix sockets before
  # restarting. TCP is available only once the final server is ready.
  if docker exec "$container_id" pg_isready -h 127.0.0.1 -U libris_test >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  echo 'PostgreSQL descartável não ficou disponível.' >&2
  exit 1
fi
for database in libris_audit_migration libris_editorial_migration libris_web_migration \
  libris_vocabulary_migration; do
  docker exec "$container_id" createdb -U libris_test "$database"
done

base_url="postgresql+asyncpg://libris_test@127.0.0.1:$port"
export TEST_DATABASE_URL="$base_url/libris_test"
export TEST_MIGRATION_DATABASE_URL="$base_url/libris_audit_migration"
export TEST_EDITORIAL_MIGRATION_DATABASE_URL="$base_url/libris_editorial_migration"
export TEST_WEB_MIGRATION_DATABASE_URL="$base_url/libris_web_migration"
export TEST_VOCABULARY_MIGRATION_DATABASE_URL="$base_url/libris_vocabulary_migration"
export TEST_WEB_ISOLATED=1
DATABASE_URL="$TEST_DATABASE_URL" uv run alembic upgrade head

# O JUnit permite recusar qualquer skip na seleção PostgreSQL. Keycloak é separado.
report=$(mktemp /tmp/libris-postgres-junit.XXXXXX.xml)
printf 'JUnit da integração: %s\n' "$report"
uv run pytest -c pyproject.toml ../../tests/integration \
  -m 'not keycloak' -q -rs --junitxml="$report" "$@"
uv run python - "$report" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
suites = list(root.iter("testsuite"))
assert suites and sum(int(s.get("tests", "0")) for s in suites) > 0, "Nenhum teste executado"
assert not any(int(s.get("skipped", "0")) for s in suites), "Skip não comprova integração"
PY
