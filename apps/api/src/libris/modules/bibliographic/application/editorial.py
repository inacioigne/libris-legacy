"""Editorial orchestration through a transaction-scoped, fail-closed storage contract."""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol
from uuid import UUID

from rdflib import Graph, URIRef
from rdflib.namespace import SKOS
from rdflib.term import Node

from libris.modules.bibliographic.api.schemas.common import LocalReference, UnreconciledReference
from libris.modules.bibliographic.api.schemas.relationships import RelationshipCreate
from libris.modules.bibliographic.application.audit import audit_actor
from libris.modules.bibliographic.application.diagnostics import CatalogValidationResult
from libris.modules.bibliographic.application.relationships import (
    ReferenceResolver,
    validate_references,
)
from libris.modules.bibliographic.application.validation import validate_catalog
from libris.modules.bibliographic.domain.editorial import (
    EditorialOperation as Op,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialRecord,
    EditorialScope,
    PreparedTransition,
    PublicationReadiness,
    prepare_transition,
    transition_operation,
)
from libris.modules.bibliographic.domain.errors import CatalogCode as C
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.models import Revision
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.domain.relationships import DEFAULT_REGISTRY, EntityType
from libris.modules.bibliographic.domain.vocabularies import VocabularyRegistry
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot
from libris.modules.bibliographic.infrastructure.profiles import (
    LEGACY_PROFILES,
    load_legacy_profile,
    load_profile_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.identity.application.authorization import (
    AuthorizationGuard,
    ExecutionContext,
    HumanContext,
    execution_context,
)
from libris.modules.identity.domain.authorization import (
    POLICIES,
    Operation,
    ResourceContext,
    ResourceKind,
)
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor
from libris.modules.identity.domain.permissions import Permission as P

# Compatibility view of the central policy; no second set of grants.
OPERATION_PERMISSIONS: Mapping[Op, P] = MappingProxyType(
    {op: next(iter(POLICIES[Operation(f"editorial.{op.value}")].permissions)) for op in Op}
)


class EditorialPermissionDenied(CatalogError, PermissionDenied):
    """Typed authorization denial; preserve the existing catalog code and catch contract."""

    def __init__(self) -> None:
        super().__init__(C.EDITORIAL_PERMISSION_DENIED, "Permissão editorial insuficiente.")


class CatalogPublicationRejected(CatalogError):
    def __init__(self, result: CatalogValidationResult) -> None:
        code = C.CONTENT_NOT_PUBLISHABLE
        if result.state == "failed":
            code = result.issues[0].code
        super().__init__(code, "Validação catalográfica não permite publicação.")
        self.result = result


@dataclass(frozen=True)
class PublicationContext:
    """Trusted, complete snapshot and classified links loaded in the write transaction.

    Providers must cover every profile relationship, including required ones; an
    empty fabricated collection is not evidence of referential integrity. Legacy
    URI-only values need explicit local/external classification, never a base prefix.
    """

    revision: Revision
    graph: Graph
    relationships: RelationshipCreate
    resolver: ReferenceResolver
    vocabularies: VocabularyRegistry = field(default_factory=VocabularyRegistry)


class EditorialTransaction(Protocol):
    async def read(self, identifier: UUID) -> EditorialRecord: ...

    async def publication_context(
        self,
        identifier: UUID,
        revision: int,
    ) -> PublicationContext | None:
        """None means classification/resolution is unavailable and blocks publication."""
        ...

    async def compare_and_set(self, change: PreparedTransition) -> None:
        """Atomically compare before.version AND before.current_revision and save after.

        Raise typed conflicts on mismatch. Validate dependency versions/locks in
        this transaction, roll back all writes on failure. Never write snapshots.
        No durable audit claim is implied by receiving a prepared event.
        """
        ...


TransactionFactory = Callable[[], AbstractAsyncContextManager[EditorialTransaction]]


async def publication_readiness(
    record: EditorialRecord,
    selected_revision: int,
    context: PublicationContext | None,
) -> PublicationReadiness:
    if context is None:
        raise CatalogError(C.EDITORIAL_PRECONDITION, "Contexto de publicação indisponível.")
    revision = context.revision
    if revision.identity != record.identity or revision.number != selected_revision:
        raise CatalogError(C.APPROVAL_STALE, "Snapshot não corresponde à revisão selecionada.")
    kind = check_snapshot(context.graph, URIRef(record.identity.uri))
    entity_type = EntityType(
        "Subject"
        if kind == SKOS.Concept
        else "Agent"
        if kind in {BF.Agent, BF.Person, BF.Organization}
        else str(kind).rsplit("/", 1)[-1]
    )
    try:
        profile = (
            revision.profile
            if revision.profile in LEGACY_PROFILES
            else (load_profile_registry().resolve(revision.profile))
        )
        if isinstance(profile, str):
            applicable = {resource.rdf_class for resource in load_legacy_profile(profile).resources}
        else:
            if entity_type not in profile.applies_to:
                raise CatalogError(C.PROFILE_INCOMPATIBLE, "Perfil não se aplica à entidade.")
            applicable = {resource.rdf_class for resource in profile.resources}
        if str(kind) not in applicable:
            raise CatalogError(C.PROFILE_INCOMPATIBLE, "Perfil incompatível com a raiz.")
        result = validate_catalog(
            context.graph,
            profile,
            vocabularies=context.vocabularies,
            operation="publish",
        )
    except ProfileError as error:
        raise CatalogError(C.PROFILE_UNKNOWN, "Release catalográfica não utilizável.") from error
    except OSError as error:
        raise CatalogError(C.PROFILE_UNAVAILABLE, "Perfil catalográfico indisponível.") from error
    if result.state != "completed" or result.conforms is not True:
        raise CatalogPublicationRejected(result)
    source = context.relationships.source
    if source.uri != record.identity.uri or source.internal_id != record.identity.internal_id:
        raise CatalogError(C.REFERENCE_INVALID, "Vínculos não pertencem à identidade selecionada.")
    if source.entity_type != entity_type:
        raise CatalogError(C.REFERENCE_INVALID, "Tipo da origem difere do snapshot.")
    await validate_references(context.relationships, context.resolver)
    # Ensure a valid but incomplete collection cannot skip RDF references.
    rdf_links: set[tuple[str, str]] = set()
    for definition in DEFAULT_REGISTRY.entries.values():
        if definition.source != entity_type or definition.derived:
            continue
        nodes: set[Node] = {URIRef(record.identity.uri)}
        for predicate in definition.path or (definition.predicate,):
            nodes = {
                value for node in nodes for value in context.graph.objects(node, URIRef(predicate))
            }
        rdf_links.update((definition.predicate, str(node)) for node in nodes)
    classified_links: set[tuple[str, str]] = set()
    for link in context.relationships.links:
        target = link.target
        if isinstance(target, UnreconciledReference):
            raise CatalogError(C.REFERENCE_INVALID, "Referência não reconciliada.")
        if isinstance(target, LocalReference):
            resolved = await context.resolver.resolve(target)
            if resolved is None:
                raise CatalogError(C.REFERENCE_INVALID, "Referência local inexistente.")
            uri = resolved.identity.uri
        else:
            uri = target.uri
        classified_links.add((link.predicate, uri))
    if rdf_links != classified_links:
        raise CatalogError(
            C.REFERENCE_UNVERIFIED, "Classificação não cobre os vínculos do snapshot."
        )
    return PublicationReadiness(record.identity, selected_revision, revision.profile)


class EditorialService:
    """Authorized orchestration; transaction factory owns state and audit commit."""

    def __init__(
        self, transactions: TransactionFactory, *, authorization: AuthorizationGuard | None = None
    ) -> None:
        self.authorization = authorization or AuthorizationGuard()
        self.transactions = transactions

    async def transition(
        self,
        identifier: UUID,
        target: str,
        *,
        actor: AuthenticatedActor | None = None,
        context: ExecutionContext | None = None,
        expected_version: int,
        expected_current_revision: int,
        selected_revision: int,
        scope: EditorialScope = EditorialScope.RECORD,
        justification: str | None = None,
        expected_operation: Op | None = None,
    ) -> PreparedTransition:
        context = execution_context(context, actor)
        # Preflight before opening a transaction. Publication target has two explicit
        # alternatives; the actual operation is checked again against loaded state.
        candidates = {
            "in_review": (Operation.EDITORIAL_SUBMIT,),
            "approved": (Operation.EDITORIAL_APPROVE,),
            "draft": (Operation.EDITORIAL_RETURN,),
            "published": (Operation.EDITORIAL_PUBLISH, Operation.EDITORIAL_REPUBLISH),
            "withdrawn": (Operation.EDITORIAL_WITHDRAW,),
            "archived": (Operation.EDITORIAL_ARCHIVE,),
        }.get(target, ())
        if expected_operation is not None:
            candidates = tuple(
                candidate
                for candidate in candidates
                if candidate == Operation(f"editorial.{expected_operation.value}")
            )
        try:
            if not candidates:
                raise PermissionDenied()
            for candidate in candidates:
                try:
                    self.authorization.require(
                        context=context,
                        operation=candidate,
                        resource=ResourceContext(
                            ResourceKind.BIBLIOGRAPHIC, resource_id=identifier
                        ),
                    )
                    break
                except PermissionDenied:
                    if candidate == candidates[-1]:
                        raise
        except PermissionDenied as error:
            raise EditorialPermissionDenied() from error
        if not isinstance(context, HumanContext):
            raise PermissionDenied("Decisão editorial exige humano.")
        actor = context.actor
        audit_actor(actor)
        async with self.transactions() as transaction:
            record = await transaction.read(identifier)
            record.require_expected(expected_version, expected_current_revision)
            operation = transition_operation(record.source(scope), target)
            if expected_operation is not None and operation != expected_operation:
                raise CatalogError(
                    C.TRANSITION_FORBIDDEN, "Comando difere da transição solicitada."
                )
            try:
                self.authorization.require(
                    context=context,
                    operation=Operation(f"editorial.{operation.value}"),
                    resource=ResourceContext(
                        ResourceKind.BIBLIOGRAPHIC, record.source(scope), resource_id=identifier
                    ),
                )
            except PermissionDenied as error:
                raise EditorialPermissionDenied() from error
            now = datetime.now(UTC)
            readiness = None
            if operation in {Op.PUBLISH, Op.REPUBLISH}:
                selected = (
                    record.published_revision
                    if operation == Op.REPUBLISH
                    else record.approved_revision
                )
                if selected_revision != selected:
                    raise CatalogError(C.APPROVAL_STALE, "Revisão não corresponde à aprovação.")
                readiness = await publication_readiness(
                    record,
                    selected_revision,
                    await transaction.publication_context(identifier, selected_revision),
                )
            change = prepare_transition(
                record,
                target,
                expected_version=expected_version,
                expected_current_revision=expected_current_revision,
                selected_revision=selected_revision,
                actor_id=actor.actor_id,
                occurred_at=now,
                scope=scope,
                justification=justification,
                readiness=readiness,
            )
            await transaction.compare_and_set(change)
        return change
