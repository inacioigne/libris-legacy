"""Actual existing uniqueness constraints, only on a dedicated migrated PostgreSQL."""

import asyncio
import os

import pytest
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.infrastructure.persistence import RevisionStore
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("duplicate", ["uuid", "uri"])
def test_identity_uniqueness_postgres(duplicate: str) -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL dedicado e migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração exige PostgreSQL real via asyncpg.")
    asyncio.run(exercise_uniqueness(url, duplicate))


async def exercise_uniqueness(url: str, duplicate: str) -> None:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = RevisionStore()
    first = ResourceIdentity.create("https://example.org/resources")
    second = ResourceIdentity.create("https://example.org/resources")
    conflicting = (
        ResourceIdentity(first.internal_id, second.uri)
        if duplicate == "uuid"
        else ResourceIdentity(second.internal_id, first.uri)
    )
    try:
        # Roll back the outer transaction: no committed resource lacking a revision.
        async with sessions() as session:
            transaction = await session.begin()
            try:
                await store.create(session, first)
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        await store.create(session, conflicting)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
