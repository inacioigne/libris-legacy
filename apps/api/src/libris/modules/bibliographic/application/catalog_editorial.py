"""Collection-aware adapters to the existing editorial and history use cases."""

import json
from dataclasses import dataclass
from uuid import UUID

from rdflib import URIRef

from libris.modules.bibliographic.application.catalog import CatalogService, CatalogView
from libris.modules.bibliographic.application.editorial import EditorialService
from libris.modules.bibliographic.application.editorial_postgres import (
    PostgresEditorialTransactions,
    entity_kind,
)
from libris.modules.bibliographic.application.history import HistoryService
from libris.modules.bibliographic.domain.editorial import (
    EditorialOperation,
    EditorialRecord,
    EditorialScope,
)
from libris.modules.bibliographic.domain.errors import ResourceNotFoundError
from libris.modules.bibliographic.domain.models import Provenance, StaleRevisionError
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.infrastructure.editorial import write_gate
from libris.modules.bibliographic.infrastructure.ownership import entity_kind as snapshot_kind
from libris.modules.bibliographic.infrastructure.rdf import parse_document
from libris.modules.bibliographic.infrastructure.serialization import ResourceRead, graph_to_read
from libris.modules.bibliographic.infrastructure.validation import AUTHORITY_PROFILE_ID
from libris.modules.identity.application.authorization import HumanContext
from libris.modules.identity.domain.authorization import Operation, ResourceContext, ResourceKind


@dataclass(frozen=True)
class EditorialView:
    catalog: CatalogView
    state: EditorialRecord | None


class CatalogEditorialService(CatalogService):
    @property
    def history(self) -> HistoryService:
        return HistoryService(self.sessions, authorization=self.authorization)

    def require(
        self, context: HumanContext, operation: Operation, identifier: UUID, profile: str
    ) -> None:
        kind = (
            ResourceKind.METADATA
            if operation.value.startswith("metadata")
            else ResourceKind.AUTHORITY
            if profile == AUTHORITY_PROFILE_ID
            else ResourceKind.BIBLIOGRAPHIC
        )
        self.authorization.require(
            context=context,
            operation=operation,
            resource=ResourceContext(kind, resource_id=identifier),
        )

    async def observe(
        self,
        identifier: UUID,
        profile: str,
        model: type[ResourceRead],
        context: HumanContext,
        operation: Operation,
    ) -> EditorialView:
        # Authorize the requested operation, without implicitly granting audit/read access.
        self.require(context, operation, identifier, profile)
        async with self.sessions() as session, session.begin():
            await write_gate(session)
            await self.store.get(session, identifier, None)
            await self.editorial.lock_resource(session, identifier)
            revision, document = await self.store.get(session, identifier, None)
            graph = parse_document(json.dumps(document), "json-ld")
            resource = graph_to_read(graph, revision.identity)
            if not isinstance(resource, model):
                raise ResourceNotFoundError("Recurso não pertence à coleção.")
            self.require(context, operation, identifier, revision.profile)
            state = await self.editorial.get(session, identifier)
        return EditorialView(
            CatalogView(revision, resource, state.version if state else None), state
        )

    async def authorize_history(
        self,
        identifier: UUID,
        profile: str,
        entity: EntityType,
        context: HumanContext,
        operation: Operation,
    ) -> None:
        # Collection membership needs the owned root, not a valid current Read DTO.
        # Historical queries must remain usable for incomplete legacy snapshots.
        self.require(context, operation, identifier, profile)
        async with self.sessions() as session:
            revision, document = await self.store.get(session, identifier, None)
        graph = parse_document(json.dumps(document), "json-ld")
        if entity_kind(snapshot_kind(graph, URIRef(revision.identity.uri))) != entity:
            raise ResourceNotFoundError("Recurso não pertence à coleção.")
        self.require(context, operation, identifier, revision.profile)

    async def execute(
        self,
        expected: EditorialView,
        operation: EditorialOperation,
        target: str,
        selected_revision: int,
        scope: EditorialScope,
        justification: str | None,
        context: HumanContext,
    ) -> EditorialView:
        identifier = expected.catalog.revision.identity.internal_id
        transactions = PostgresEditorialTransactions(
            self.sessions,
            actor=context.actor,
            provenance=Provenance("catalog", f"http:editorial:{operation.value}"),
        )
        change = await EditorialService(transactions, authorization=self.authorization).transition(
            identifier,
            target,
            context=context,
            expected_version=expected.catalog.editorial_version or 1,
            expected_current_revision=expected.catalog.revision.number,
            selected_revision=selected_revision,
            scope=scope,
            justification=justification,
            expected_operation=operation,
        )
        return EditorialView(
            CatalogView(expected.catalog.revision, expected.catalog.resource, change.after.version),
            change.after,
        )

    def workspace_transactions(
        self, context: HumanContext, process: str
    ) -> PostgresEditorialTransactions:
        return PostgresEditorialTransactions(
            self.sessions,
            actor=context.actor,
            provenance=Provenance("catalog", process),
        )

    async def validate_workspace(
        self, expected: EditorialView, context: HumanContext
    ) -> int:
        identifier = expected.catalog.revision.identity.internal_id
        readiness = await EditorialService(
            self.workspace_transactions(context, "http:workspace:validate"),
            authorization=self.authorization,
        ).validate_workspace(
            identifier,
            context=context,
            expected_version=expected.catalog.editorial_version or 1,
            expected_current_revision=expected.catalog.revision.number,
        )
        return readiness.revision

    async def publish_workspace(
        self, expected: EditorialView, context: HumanContext
    ) -> EditorialView:
        identifier = expected.catalog.revision.identity.internal_id
        change = await EditorialService(
            self.workspace_transactions(context, "http:workspace:publish"),
            authorization=self.authorization,
        ).publish_workspace(
            identifier,
            context=context,
            expected_version=expected.catalog.editorial_version or 1,
            expected_current_revision=expected.catalog.revision.number,
        )
        return EditorialView(
            CatalogView(expected.catalog.revision, expected.catalog.resource, change.after.version),
            change.after,
        )

    async def public_view(self, identifier: UUID, model: type[ResourceRead]) -> CatalogView:
        try:
            selected = await self.read_public(identifier)
        except StaleRevisionError as error:
            raise ResourceNotFoundError("Recurso público inexistente.") from error
        if selected is None:
            raise ResourceNotFoundError("Recurso público inexistente.")
        revision, graph = selected
        resource = graph_to_read(graph, revision.identity)
        if not isinstance(resource, model):
            raise ResourceNotFoundError("Recurso público inexistente.")
        return CatalogView(revision, resource, None, graph)

    async def public_resource(self, identifier: UUID, model: type[ResourceRead]) -> ResourceRead:
        return (await self.public_view(identifier, model)).resource
