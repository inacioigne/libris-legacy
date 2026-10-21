"""Denied application operations leave content, state and audit unchanged in PostgreSQL."""

import asyncio

import pytest
from libris.modules.bibliographic.application.editorial import EditorialService
from libris.modules.bibliographic.application.editorial_postgres import (
    PostgresEditorialTransactions,
)
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.domain.audit import ActorType, ExecutionActor
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError
from libris.modules.bibliographic.domain.models import Provenance, ResourceIdentity
from libris.modules.bibliographic.infrastructure.validation import (
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.application.authorization import TechnicalContext
from libris.modules.identity.domain.authorization import Operation
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.permissions import Permission
from test_audit_postgres import actor, context, graph_for

pytestmark = pytest.mark.integration


def test_denial_has_no_durable_mutation_and_technical_scope_is_enforced() -> None:
    async def scenario() -> None:
        async with context() as (sessions, service):
            identity = ResourceIdentity.create("https://rbac.example.invalid/resources")
            provenance = Provenance("fixture:rbac", "test:authorization")
            human = actor()
            graph = graph_for(identity)
            await service.create(
                identity, graph, provenance, profile=INSTITUTIONAL_PROFILE_ID, actor=human
            )
            history = HistoryService(sessions)
            before = await history.read_editorial(identity.internal_id, actor=human)
            importer = TechnicalContext(
                "service:rbac-import",
                frozenset({Permission.BIBLIOGRAPHIC_CREATE}),
                frozenset({Operation.BIBLIOGRAPHIC_CREATE}),
                "pytest:composition-root",
            )
            for kwargs in (
                {},
                {"actor": None},
                {"actor": actor("libris-reader")},
                {"context": importer},
                {"execution_actor": ExecutionActor("service:forged", ActorType.SERVICE)},
            ):
                with pytest.raises(PermissionDenied):
                    await service.revise(identity.internal_id, 1, graph, provenance, **kwargs)
            for kwargs in ({}, {"actor": actor("libris-reader")}, {"context": importer}):
                with pytest.raises(PermissionDenied):
                    await service.read(identity.internal_id, **kwargs)
            factory = PostgresEditorialTransactions(sessions, actor=human, provenance=provenance)
            with pytest.raises(CatalogError) as error:
                await EditorialService(factory).transition(
                    identity.internal_id,
                    "in_review",
                    context=importer,
                    expected_version=1,
                    expected_current_revision=1,
                    selected_revision=1,
                )
            assert error.value.code == CatalogCode.EDITORIAL_PERMISSION_DENIED
            after = await history.read_editorial(identity.internal_id, actor=human)
            assert after == before
            assert len(await history.list_revisions(identity.internal_id, actor=human)) == 1
            assert len(await history.list_events(identity.internal_id, actor=human)) == 1
            assert await service.read_public(identity.internal_id) is None
            missing = ResourceIdentity.create("https://rbac.example.invalid/new")
            with pytest.raises(PermissionDenied):
                await service.create(missing, graph_for(missing), provenance)
            with pytest.raises(LookupError):
                await service.read(missing.internal_id, actor=human)
            technical = await service.create(
                missing, graph_for(missing), provenance, context=importer
            )
            assert technical.actor_id == importer.actor_id

    asyncio.run(scenario())
