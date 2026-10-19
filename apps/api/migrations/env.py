import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from libris.core.config import Settings
from libris.infrastructure.database import Base
from libris.modules.bibliographic.infrastructure import (  # noqa: F401
    audit,
    editorial,
    persistence,
    vocabulary_catalog,
)

settings = Settings()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=str(settings.database_url),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(str(settings.database_url), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
