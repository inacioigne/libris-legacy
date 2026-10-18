docker compose run --rm --no-deps api alembic upgrade head

docker compose exec -T api alembic upgrade head

# Homologação M04 opt-in; somente recursos novos descartáveis, build prévio.
apps/api/.venv/bin/python scripts/verify-m04-integration.py --isolated --keycloak --browser
