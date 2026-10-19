"""Concrete unit of work composing existing revision, reference and audit contracts."""

import json
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from types import MappingProxyType
from uuid import UUID

from rdflib import URIRef
from rdflib.namespace import SKOS
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libris.modules.bibliographic.api.schemas.common import ExternalReference, LocalReference
from libris.modules.bibliographic.api.schemas.relationships import DirectLink, RelationshipCreate
from libris.modules.bibliographic.application.audit import audit_actor, editorial_event
from libris.modules.bibliographic.application.editorial import PublicationContext
from libris.modules.bibliographic.application.relationships import ResolvedEntity, validate_cycles
from libris.modules.bibliographic.application.vocabulary_validation import (
    vocabulary_registry_for_profile,
)
from libris.modules.bibliographic.domain.editorial import EditorialRecord, PreparedTransition
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError
from libris.modules.bibliographic.domain.models import Provenance
from libris.modules.bibliographic.domain.relationships import DEFAULT_REGISTRY, EntityType
from libris.modules.bibliographic.infrastructure.audit import AuditStore
from libris.modules.bibliographic.infrastructure.editorial import EditorialStore, write_gate
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot
from libris.modules.bibliographic.infrastructure.persistence import RevisionStore, SemanticResource
from libris.modules.bibliographic.infrastructure.rdf import BF, parse_document
from libris.modules.identity.domain.models import AuthenticatedActor


def entity_kind(kind: URIRef) -> EntityType:
    if kind == SKOS.Concept:
        return EntityType.SUBJECT
    if kind in {BF.Agent, BF.Person, BF.Organization}:
        return EntityType.AGENT
    return EntityType(str(kind).rsplit("/", 1)[-1])


class PostgresReferenceResolver:
    def __init__(self, session: AsyncSession, *, lock: bool = True) -> None:
        self.session = session
        self.lock = lock
        self.revisions = RevisionStore()

    async def resolve(self, reference: LocalReference) -> ResolvedEntity | None:
        statement = select(SemanticResource)
        if self.lock:
            statement = statement.with_for_update(read=True, key_share=True)
        statement = statement.where(
            SemanticResource.id == reference.internal_id
            if reference.internal_id is not None
            else SemanticResource.uri == reference.uri
        )
        resource = await self.session.scalar(statement)
        if resource is None:
            return None
        revision, document = await self.revisions.get(self.session, resource.id, None)
        graph = parse_document(json.dumps(document), "json-ld")
        kind = check_snapshot(graph, URIRef(resource.uri))
        return ResolvedEntity(
            revision.identity,
            entity_kind(kind),
            "Organization" if kind == BF.Organization else None,
        )


class PostgresEditorialTransaction:
    def __init__(
        self,
        session: AsyncSession,
        actor: AuthenticatedActor,
        provenance: Provenance,
        external_references: Mapping[str, EntityType],
        audit: AuditStore,
    ) -> None:
        self.session = session
        self.actor = actor
        self.provenance = provenance
        self.external_references = external_references
        self.states = EditorialStore()
        self.revisions = RevisionStore()
        self.audit = audit

    async def read(self, identifier: UUID) -> EditorialRecord:
        resource = await self.states.lock_resource(self.session, identifier)
        return await self.states.initialize(self.session, resource)

    async def publication_context(self, identifier: UUID, revision: int) -> PublicationContext:
        selected, document = await self.revisions.get(self.session, identifier, revision)
        graph = parse_document(json.dumps(document), "json-ld")
        root = URIRef(selected.identity.uri)
        kind = entity_kind(check_snapshot(graph, root))
        source = LocalReference(entity_type=kind, internal_id=identifier, uri=str(root))
        resolver = PostgresReferenceResolver(self.session)
        links: list[dict[str, object]] = []
        for definition in DEFAULT_REGISTRY.entries.values():
            if definition.source != kind or definition.derived:
                continue
            intermediates = (
                graph.objects(root, URIRef(definition.path[0])) if definition.path else (root,)
            )
            for intermediate in intermediates:
                predicate = definition.path[-1] if definition.path else definition.predicate
                for node in graph.objects(intermediate, URIRef(predicate)):
                    if not isinstance(node, URIRef):
                        raise CatalogError(
                            CatalogCode.REFERENCE_INVALID, "Destino de vínculo deve ser URI."
                        )
                    resource = await self.session.scalar(
                        select(SemanticResource).where(SemanticResource.uri == str(node))
                    )
                    if resource is not None:
                        target = LocalReference(
                            entity_type=next(iter(definition.targets)),
                            internal_id=resource.id,
                            uri=resource.uri,
                        )
                        resolved = await resolver.resolve(target)
                        if resolved is None:
                            raise CatalogError(
                                CatalogCode.REFERENCE_INVALID, "Referência local ausente."
                            )
                        target = target.model_copy(update={"entity_type": resolved.entity_type})
                        target_value = target.model_dump()
                    elif str(node) in self.external_references:
                        target_value = ExternalReference(
                            entity_type=self.external_references[str(node)], uri=str(node)
                        ).model_dump()
                    else:
                        raise CatalogError(
                            CatalogCode.REFERENCE_UNVERIFIED,
                            "URI sem classificação local/externa explícita.",
                        )
                    link: dict[str, object] = {
                        "source": source.model_dump(),
                        "target": target_value,
                        "predicate": definition.predicate,
                        "kind": "direct",
                    }
                    if definition.role_required:
                        link.update(
                            kind="contribution",
                            role=[str(role) for role in graph.objects(intermediate, BF.role)],
                        )
                    elif definition.auxiliary_class == str(BF.Publication):
                        link.update(kind="publication", activity_key=str(intermediate))
                    links.append(link)
        relationships = RelationshipCreate.model_validate({"source": source, "links": links})
        # Follow only locally resolved acyclic edges, using current owned snapshots.
        # The exclusive gate keeps all these content heads stable until commit.
        closure = list(relationships.links)
        for definition in DEFAULT_REGISTRY.entries.values():
            if definition.source != kind or not definition.acyclic:
                continue
            pending = [
                link.target
                for link in relationships.links
                if link.predicate == definition.predicate
                and isinstance(link.target, LocalReference)
            ]
            visited: set[UUID] = {identifier}
            while pending:
                reference = pending.pop()
                resolved = await resolver.resolve(reference)
                if resolved is None:
                    raise CatalogError(CatalogCode.REFERENCE_INVALID, "Dependência local ausente.")
                dependency_id = resolved.identity.internal_id
                if dependency_id in visited:
                    continue
                visited.add(dependency_id)
                _, dependency_document = await self.revisions.get(self.session, dependency_id, None)
                dependency_graph = parse_document(json.dumps(dependency_document), "json-ld")
                origin = LocalReference(
                    entity_type=resolved.entity_type,
                    internal_id=dependency_id,
                    uri=resolved.identity.uri,
                )
                for node in dependency_graph.objects(
                    URIRef(resolved.identity.uri), URIRef(definition.predicate)
                ):
                    if not isinstance(node, URIRef):
                        raise CatalogError(
                            CatalogCode.REFERENCE_INVALID, "Destino inválido no fecho."
                        )
                    resource = await self.session.scalar(
                        select(SemanticResource).where(SemanticResource.uri == str(node))
                    )
                    if resource is None:
                        if str(node) not in self.external_references:
                            raise CatalogError(
                                CatalogCode.REFERENCE_UNVERIFIED,
                                "Fecho contém referência sem classificação.",
                            )
                        continue  # No claim about remote transitive closure.
                    target = LocalReference(
                        entity_type=kind, internal_id=resource.id, uri=resource.uri
                    )
                    closure.append(
                        DirectLink(source=origin, target=target, predicate=definition.predicate)
                    )
                    pending.append(target)
        validate_cycles(closure)
        vocabularies = await vocabulary_registry_for_profile(
            self.session, selected.profile, operation="publish"
        )
        return PublicationContext(selected, graph, relationships, resolver, vocabularies)

    async def compare_and_set(self, change: PreparedTransition) -> None:
        # Recheck persisted content CAS before the conditional state UPDATE.
        resource = await self.states.lock_resource(self.session, change.before.identity.internal_id)
        if resource.current_revision != change.before.current_revision:
            raise CatalogError(CatalogCode.REVISION_STALE, "Conteúdo mudou durante a transição.")
        revision, _ = await self.revisions.get(self.session, resource.id, change.event.revision)
        event = editorial_event(change.event, revision, self.provenance, actor=self.actor)
        await self.states.save(self.session, change.before, change.after)
        await self.audit.append(self.session, event)


class PostgresEditorialTransactions:
    """Trusted actor/source configuration, one begin/commit per effective transition."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        actor: AuthenticatedActor,
        provenance: Provenance,
        external_references: Mapping[str, EntityType] | None = None,
    ) -> None:
        audit_actor(actor)
        self.sessions = sessions
        self.actor = actor
        self.provenance = provenance
        self.external_references = MappingProxyType(dict(external_references or {}))
        self.audit = AuditStore()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[PostgresEditorialTransaction]:
        async with self.sessions() as session, session.begin():
            await write_gate(session, editorial=True)
            yield PostgresEditorialTransaction(
                session, self.actor, self.provenance, self.external_references, self.audit
            )
