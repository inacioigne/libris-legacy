"""Typed catalog use cases over the existing content transaction protocol."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from rdflib import Graph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libris.modules.bibliographic.api.schemas.common import (
    ExternalReference,
    LocalReference,
    ResourceReference,
)
from libris.modules.bibliographic.application.editorial_postgres import PostgresReferenceResolver
from libris.modules.bibliographic.application.relationships import ResolvedEntity
from libris.modules.bibliographic.application.service import BibliographicService, prepare_document
from libris.modules.bibliographic.application.vocabulary_validation import (
    vocabulary_registry_for_profile,
)
from libris.modules.bibliographic.domain.audit import AuditEventType
from libris.modules.bibliographic.domain.errors import (
    CatalogCode,
    CatalogError,
    ResourceNotFoundError,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
    StaleRevisionError,
)
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.editorial import write_gate
from libris.modules.bibliographic.infrastructure.persistence import SemanticResource
from libris.modules.bibliographic.infrastructure.rdf import parse_document
from libris.modules.bibliographic.infrastructure.serialization import (
    ResourceCreate,
    ResourceRead,
    ResourceUpdate,
    apply_update,
    create_to_graph,
    graph_to_read,
)
from libris.modules.bibliographic.infrastructure.validation import AUTHORITY_PROFILE_ID
from libris.modules.identity.application.authorization import ExecutionContext
from libris.modules.identity.domain.authorization import Operation, ResourceContext, ResourceKind


@dataclass(frozen=True)
class CatalogView:
    revision: Revision
    resource: ResourceRead
    # None represents uninitialized legacy data, never an invented persisted state.
    editorial_version: int | None
    graph: Graph | None = None


async def resolve_references(
    session: AsyncSession, resource: BaseModel, *, lock: bool = True
) -> dict[UUID | str, ResolvedEntity]:
    """Resolve only submitted references, under the caller's gate/transaction.

    URI-only/external references absent locally remain pending as in v1. Explicit
    local references must exist and agree with the stored UUID, URI and class.
    """
    references: list[LocalReference | ResourceReference | ExternalReference] = []

    def collect(value: object) -> None:
        if isinstance(value, (LocalReference, ResourceReference, ExternalReference)):
            references.append(value)
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                if name in value.model_fields_set:
                    collect(getattr(value, name))
        elif isinstance(value, list):
            for element in value:
                collect(element)

    collect(resource)
    resolver = PostgresReferenceResolver(session, lock=lock)
    result: dict[UUID | str, ResolvedEntity] = {}
    # Deterministic acquisition of local dependency locks.
    for reference in sorted(references, key=lambda ref: str(ref.model_dump())):
        local: LocalReference | None = None
        if isinstance(reference, LocalReference):
            local = reference
        else:
            stored = await session.scalar(
                select(SemanticResource).where(SemanticResource.uri == reference.uri)
            )
            if stored is not None:
                local = LocalReference(entity_type=EntityType.WORK, internal_id=stored.id)
        entity = await resolver.resolve(local) if local is not None else None
        if isinstance(reference, LocalReference):
            if (
                entity is None
                or (
                    reference.internal_id is not None
                    and reference.internal_id != entity.identity.internal_id
                )
                or (reference.uri is not None and reference.uri != entity.identity.uri)
                or (reference.entity_type != entity.entity_type)
            ):
                raise CatalogError(CatalogCode.REFERENCE_INVALID, "Referência local incompatível.")
        if entity is not None:
            if (
                isinstance(reference, ExternalReference)
                and reference.entity_type != entity.entity_type
            ):
                raise CatalogError(CatalogCode.REFERENCE_INVALID, "Tipo local incompatível.")
            result[entity.identity.internal_id] = entity
            result[entity.identity.uri] = entity
    return result


class CatalogService(BibliographicService):
    """DTO specialization sharing stores, guards and persistence steps with M03.

    No transaction wraps a service-owned transaction. _create/_revise are shared
    steps in the single transaction owned by each use case below.
    """

    def require_operation(
        self,
        context: ExecutionContext,
        profile: str,
        operation: Literal["read", "create", "update"],
        identifier: UUID | None = None,
    ) -> None:
        if operation == "read":
            authority = profile == AUTHORITY_PROFILE_ID
            self.authorization.require(
                context=context,
                operation=Operation.AUTHORITY_READ_EDITORIAL
                if authority
                else Operation.BIBLIOGRAPHIC_READ_EDITORIAL,
                resource=ResourceContext(
                    ResourceKind.AUTHORITY if authority else ResourceKind.BIBLIOGRAPHIC,
                    resource_id=identifier,
                ),
            )
        else:
            self._require_content(
                context, profile, create=operation == "create", resource_id=identifier
            )

    async def _current(
        self,
        session: AsyncSession,
        identifier: UUID,
        model: type[ResourceRead],
        context: ExecutionContext,
        *,
        editing: bool,
    ) -> tuple[CatalogView, Graph]:
        # Check existence before lock_resource, whose historical missing contract is stale.
        await self.store.get(session, identifier, None)
        await self.editorial.lock_resource(session, identifier)
        revision, document = await self.store.get(session, identifier, None)
        self.require_operation(
            context, revision.profile, "update" if editing else "read", identifier
        )
        graph = parse_document(json.dumps(document), "json-ld")
        resource = graph_to_read(graph, revision.identity)
        if not isinstance(resource, model):
            raise ResourceNotFoundError("Recurso não pertence à coleção.")
        state = await self.editorial.get(session, identifier)
        return CatalogView(revision, resource, state.version if state else None, graph), graph

    async def current(
        self,
        identifier: UUID,
        profile: str,
        model: type[ResourceRead],
        context: ExecutionContext,
        *,
        editing: bool = False,
    ) -> CatalogView:
        self.require_operation(context, profile, "update" if editing else "read", identifier)
        async with self.sessions() as session, session.begin():
            await write_gate(session)
            view, _ = await self._current(session, identifier, model, context, editing=editing)
        return view

    async def create_resource(
        self,
        resource: ResourceCreate,
        identity: ResourceIdentity,
        profile: str,
        context: ExecutionContext,
    ) -> CatalogView:
        self.require_operation(context, profile, "create", identity.internal_id)
        actor_id, actor_type = self._audit_context(context)
        async with self.sessions() as session, session.begin():
            await write_gate(session, editorial=True)
            references = await resolve_references(session, resource)
            graph = create_to_graph(resource, identity, profile=profile, references=references)
            projected = graph_to_read(graph, identity)
            vocabularies = await vocabulary_registry_for_profile(
                session, profile, operation="create"
            )
            document = prepare_document(
                graph,
                identity.uri,
                profile,
                vocabularies=vocabularies,
                operation="create",
            )
            revision = Revision(
                identity,
                1,
                datetime.now(UTC),
                Provenance("catalog", "http:create"),
                profile,
                actor_id,
            )
            await self._create(session, revision, document, actor_type)
        return CatalogView(revision, projected, 1)

    async def update_resource(
        self,
        identifier: UUID,
        update: ResourceUpdate,
        expected: CatalogView,
        context: ExecutionContext,
    ) -> CatalogView:
        self.require_operation(context, expected.revision.profile, "update", identifier)
        actor_id, actor_type = self._audit_context(context)
        async with self.sessions() as session, session.begin():
            await write_gate(session, editorial=True)
            current, graph = await self._current(
                session,
                identifier,
                type(expected.resource),
                context,
                editing=True,
            )
            if (
                current.revision != expected.revision
                or current.editorial_version != expected.editorial_version
            ):
                raise StaleRevisionError("Precondição obsoleta.")
            references = await resolve_references(session, update)
            graph = apply_update(
                graph,
                update,
                current.revision.identity,
                profile=current.revision.profile,
                references=references,
            )
            projected = graph_to_read(graph, current.revision.identity)
            revision = await self._revise(
                session,
                identifier,
                expected.revision.number,
                graph,
                Provenance("catalog", "http:patch"),
                actor_id,
                actor_type,
                AuditEventType.REVISED,
                expected_editorial_version=expected.editorial_version or 1,
                context=context,
            )
        return CatalogView(revision, projected, (expected.editorial_version or 1) + 1)
