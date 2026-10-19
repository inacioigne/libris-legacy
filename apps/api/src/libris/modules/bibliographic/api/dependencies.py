"""HTTP access to the lifespan-owned factory; application services own transactions.

Identity/context/guard dependencies are reused directly from identity.api.dependencies.
Correlation is supplied by the existing middleware and request_id_context.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libris.core.config import Settings
from libris.infrastructure.database import Database
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.application.vocabulary_queries import VocabularyQueryService
from libris.modules.identity.api.dependencies import get_authorization_guard
from libris.modules.identity.application.authorization import AuthorizationGuard


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the existing factory without opening a session or transaction."""
    database: Database = request.app.state.database
    return database.sessions


def get_catalog_service(
    request: Request,
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
    authorization: Annotated[AuthorizationGuard, Depends(get_authorization_guard)],
) -> CatalogService:
    return CatalogService(sessions, authorization=authorization)


def get_vocabulary_query_service(
    sessions: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> VocabularyQueryService:
    return VocabularyQueryService(sessions)


def get_resource_base_uri(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return str(settings.resource_base_uri)
