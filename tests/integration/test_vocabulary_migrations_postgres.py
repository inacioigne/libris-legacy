"""Incremental M08.02 migration checks in a dedicated disposable PostgreSQL database."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration


def test_vocabulary_upgrade_constraints_protected_downgrade_and_reupgrade() -> None:
    url = os.environ.get("TEST_VOCABULARY_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip("TEST_VOCABULARY_MIGRATION_DATABASE_URL exige banco descartável exclusivo.")
    if not url.startswith("postgresql+asyncpg://") or url in {
        os.environ.get("DATABASE_URL"),
        os.environ.get("TEST_DATABASE_URL"),
        os.environ.get("TEST_MIGRATION_DATABASE_URL"),
        os.environ.get("TEST_EDITORIAL_MIGRATION_DATABASE_URL"),
        os.environ.get("TEST_WEB_MIGRATION_DATABASE_URL"),
    }:
        pytest.fail("Migração M08.02 exige PostgreSQL descartável exclusivo.")
    api = Path(__file__).resolve().parents[2] / "apps/api"
    env = os.environ | {"DATABASE_URL": url}

    def alembic(
        *arguments: str, succeeds: bool = True, failure_message: str = ""
    ) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=api,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if succeeds:
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            assert result.returncode != 0
            assert failure_message in result.stdout + result.stderr

    async def sql(
        statement: str, parameters: dict[str, object] | None = None
    ) -> list[tuple[object, ...]]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                result = await connection.execute(text(statement), parameters or {})
                return [tuple(row) for row in result] if result.returns_rows else []
        finally:
            await engine.dispose()

    def query(
        statement: str, parameters: dict[str, object] | None = None
    ) -> list[tuple[object, ...]]:
        return asyncio.run(sql(statement, parameters))

    assert query(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema NOT IN ('pg_catalog','information_schema')"
    ) == [(0,)]
    alembic("upgrade", "0005_web_auth")
    legacy = query(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND tablename LIKE 'experimental_semantic_%' ORDER BY tablename"
    )
    alembic("upgrade", "head")
    alembic("check")
    assert query(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND tablename LIKE 'experimental_semantic_%' ORDER BY tablename"
    ) == legacy
    expected = {
        "vocabulary_sources",
        "vocabularies",
        "vocabulary_releases",
        "vocabulary_concepts",
        "vocabulary_release_concepts",
        "vocabulary_labels",
        "vocabulary_identifiers",
        "vocabulary_imports",
        "vocabulary_resolution_attempts",
    }
    assert {
        row[0]
        for row in query(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND (tablename LIKE 'vocabulary_%' OR tablename = 'vocabularies')"
        )
    } == expected
    source_id, vocabulary_id = uuid4(), uuid4()
    query(
        "INSERT INTO vocabulary_sources(id,key,provider_uri,source_uri,provenance,created_at) "
        "VALUES (:id,'fixture','urn:provider','urn:source','{}'::jsonb,now())",
        {"id": source_id},
    )
    query(
        "INSERT INTO vocabularies(id,uri,source_id,kind,status,created_at) "
        "VALUES (:id,'urn:vocabulary',:source,'external','active',now())",
        {"id": vocabulary_id, "source": source_id},
    )
    release_id = uuid4()
    query(
        "INSERT INTO vocabulary_releases"
        "(id,vocabulary_id,release_key,source_uri,digest,provenance,completeness,status,"
        "retrieved_at,created_at) "
        "VALUES (:id,:vocabulary,'fixture','urn:source',:digest,'{}'::jsonb,"
        "'complete','candidate',now(),now())",
        {"id": release_id, "vocabulary": vocabulary_id, "digest": "a" * 64},
    )
    assert query("SELECT uri,status FROM vocabularies") == [("urn:vocabulary", "active")]
    assert query("SELECT provenance FROM vocabulary_releases") == [({},)]
    alembic(
        "downgrade",
        "0005_web_auth",
        succeeds=False,
        failure_message="Downgrade M08.02 exige",
    )
    assert query("SELECT uri,status FROM vocabularies") == [("urn:vocabulary", "active")]
    query(
        "UPDATE vocabulary_releases SET provenance='{\"format\":\"SKOS/RDF Turtle\"}'::jsonb"
    )
    alembic(
        "downgrade",
        "0005_web_auth",
        succeeds=False,
        failure_message="Downgrade M08 release provenance exige",
    )
    assert query("SELECT count(*) FROM vocabulary_releases") == [(1,)]
    query("DELETE FROM vocabulary_releases")
    query("DELETE FROM vocabularies")
    query("DELETE FROM vocabulary_sources")
    alembic("downgrade", "0005_web_auth")
    assert query("SELECT to_regclass('public.vocabularies')") == [(None,)]
    alembic("upgrade", "head")
    alembic("check")
    assert query("SELECT count(*) FROM vocabularies") == [(0,)]
