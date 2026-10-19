from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from starlette.types import ASGIApp

from libris.api.health import router as health_router
from libris.api.problems import RequestCorrelationMiddleware, install_problem_handlers
from libris.api.security import EndpointCORSMiddleware, SecurityHeadersMiddleware
from libris.core.config import Settings
from libris.infrastructure.database import Database
from libris.modules.bibliographic.api.router import router as catalog_router
from libris.modules.bibliographic.api.vocabularies import router as vocabularies_router
from libris.modules.identity.api.router import router as identity_router
from libris.modules.identity.application.service import IdentityService
from libris.modules.identity.infrastructure.oidc import OIDCValidator


class LibrisAPI(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        config: Settings = self.state.settings
        cors = EndpointCORSMiddleware(
            super().build_middleware_stack(),
            allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "PATCH"],
            allow_headers=["Authorization", "X-Request-ID", "If-Match", "If-None-Match"],
            expose_headers=["X-Request-ID", "WWW-Authenticate", "ETag", "Location"],
        )
        return RequestCorrelationMiddleware(
            SecurityHeadersMiddleware(cors, production=config.app_env == "production")
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(config)
        app.state.database = database
        client = httpx.AsyncClient(trust_env=False)
        app.state.identity_service = IdentityService(
            OIDCValidator(config, client),
            config.oidc_role_source,
            config.oidc_role_client,
            config.oidc_human_client_id,
        )
        try:
            yield
        finally:
            await client.aclose()
            await database.close()

    app = LibrisAPI(
        title="Libris API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if config.app_env == "production" else "/docs",
        redoc_url=None if config.app_env == "production" else "/redoc",
    )
    app.state.settings = config
    install_problem_handlers(app, correlation=False)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(catalog_router)
    app.include_router(vocabularies_router)
    return app


app = create_app()
