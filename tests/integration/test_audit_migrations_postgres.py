"""Destructive migration verification requires a SEPARATE disposable database."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.infrastructure.audit import AuditStore
from libris.modules.bibliographic.infrastructure.persistence import RevisionStore
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.infrastructure.role_mapper import map_permissions
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def test_incremental_migration_preserves_legacy_and_downgrade() -> None:
    url = os.environ.get("TEST_MIGRATION_DATABASE_URL")
    if not url:
        pytest.skip(
            "TEST_MIGRATION_DATABASE_URL exige OUTRO banco descartável; downgrade destrutivo."
        )
    if not url.startswith("postgresql+asyncpg://") or url == os.environ.get("TEST_DATABASE_URL"):
        pytest.fail("Migração destrutiva exige PostgreSQL separado do banco de integração.")
    api_dir = Path(__file__).resolve().parents[2] / "apps/api"
    env = os.environ | {"DATABASE_URL": url}

    def alembic(*arguments: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments],
            cwd=api_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    # Fail closed if this isn't a fresh database. Never downgrade preexisting data.
    async def empty() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        text(
                            "SELECT count(*) FROM information_schema.tables "
                            "WHERE table_schema='public'"
                        )
                    )
                    == 0
                ), "Banco de migração deve estar vazio."
        finally:
            await engine.dispose()

    asyncio.run(empty())
    alembic("upgrade", "0002_revision_actor")
    identifier = uuid4()
    uri = f"https://legacy.example.invalid/resources/{identifier}"
    document = '[{"@id":"' + uri + '","@type":["http://id.loc.gov/ontologies/bibframe/Work"]}]'
    profile = "urn:libris:profile:monograph:v1"

    async def seed() -> tuple[object, ...]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO experimental_semantic_resources(id,uri,current_revision) "
                        "VALUES (:id,:uri,1)"
                    ),
                    {"id": identifier, "uri": uri},
                )
                await connection.execute(
                    text(
                        "INSERT INTO experimental_semantic_revisions "
                        "(resource_id,number,document,created_at,source,process,profile,actor_id) "
                        "VALUES (:id,1,CAST(:doc AS jsonb),now(),'legacy','legacy',:profile,NULL)"
                    ),
                    {"id": identifier, "doc": document, "profile": profile},
                )
                row = (
                    await connection.execute(
                        text(
                            "SELECT document,created_at,source,process,profile,actor_id "
                            "FROM experimental_semantic_revisions WHERE resource_id=:id"
                        ),
                        {"id": identifier},
                    )
                ).one()
                return tuple(row)
        finally:
            await engine.dispose()

    before = asyncio.run(seed())
    alembic("upgrade", "head")
    alembic("check")

    async def verify() -> None:
        engine = create_async_engine(url)
        sessions = async_sessionmaker(engine)
        human = AuthenticatedActor(
            actor_identifier("issuer", "subject"),
            "issuer",
            "subject",
            frozenset({"libris-admin"}),
            map_permissions(frozenset({"libris-admin"})),
        )
        try:
            async with sessions() as session:
                row = (
                    await session.execute(
                        text(
                            "SELECT document,created_at,source,process,profile,actor_id "
                            "FROM experimental_semantic_revisions WHERE resource_id=:id"
                        ),
                        {"id": identifier},
                    )
                ).one()
                assert tuple(row) == before
                revision, _ = await RevisionStore().get(session, identifier, 1)
                assert revision.actor_id is None and revision.provenance.reason is None
                assert revision.identity.uri == uri and revision.profile == profile
                assert await AuditStore().list(session, identifier) == ()
            entries = await HistoryService(sessions).list_revisions(identifier, actor=human)
            assert len(entries) == 1 and not entries[0].content_event_recorded
        finally:
            await engine.dispose()

    asyncio.run(verify())
    alembic("downgrade", "0002_revision_actor")
    alembic("upgrade", "head")
    asyncio.run(verify())
