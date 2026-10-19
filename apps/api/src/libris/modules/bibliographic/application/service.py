import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from rdflib import Graph, URIRef
from rdflib.namespace import RDF
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libris.modules.bibliographic.application.audit import audit_actor, content_event
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationRejected,
    ordered,
)
from libris.modules.bibliographic.application.vocabulary_validation import (
    profile_uses_membership_policy,
    validate_profile_vocabulary_membership,
    vocabulary_registry_for_profile,
)
from libris.modules.bibliographic.domain.audit import ActorType, AuditEventType, ExecutionActor
from libris.modules.bibliographic.domain.editorial import prepare_content_revision
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
    StaleRevisionError,
)
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.audit import AuditStore
from libris.modules.bibliographic.infrastructure.diagnostics import adapt_report
from libris.modules.bibliographic.infrastructure.editorial import EditorialStore, write_gate
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot
from libris.modules.bibliographic.infrastructure.persistence import RevisionStore
from libris.modules.bibliographic.infrastructure.profiles import load_legacy_profile
from libris.modules.bibliographic.infrastructure.rdf import BF, parse_document, serialize_document
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
    PROFILE_ID,
    validate_profile,
)
from libris.modules.identity.application.authorization import (
    AuthorizationGuard,
    ExecutionContext,
    HumanContext,
    TechnicalContext,
    execution_context,
)
from libris.modules.identity.domain.authorization import Operation as O
from libris.modules.identity.domain.authorization import ResourceContext
from libris.modules.identity.domain.authorization import ResourceKind as K
from libris.modules.identity.domain.exceptions import PermissionDenied
from libris.modules.identity.domain.models import AuthenticatedActor


def prepare_document(
    graph: Graph,
    uri: str,
    profile: str = PROFILE_ID,
    *,
    vocabularies: VocabularyRegistry | None = None,
    operation: Literal["prevalidation", "create", "update", "restore", "publish"] = "create",
    previous_graph: Graph | None = None,
) -> list[dict[str, object]]:
    if profile not in (
        PROFILE_ID,
        INSTITUTIONAL_PROFILE_ID,
        CONTROLLED_VOCABULARY_PROFILE_ID,
        AUTHORITY_PROFILE_ID,
    ):
        raise ProfileError("unknown_profile", "Perfil local desconhecido.")
    if profile == PROFILE_ID:
        if not any(
            (URIRef(uri), RDF.type, kind) in graph for kind in (BF.Work, BF.Instance, BF.Item)
        ):
            raise CatalogError(
                CatalogCode.IDENTITY_INVALID,
                "A URI raiz deve identificar Work, Instance ou Item no grafo.",
            )
    else:
        kind = check_snapshot(graph, URIRef(uri))
        bibliographic = kind in (BF.Work, BF.Instance, BF.Item)
        if (
            profile in {INSTITUTIONAL_PROFILE_ID, CONTROLLED_VOCABULARY_PROFILE_ID}
            and not bibliographic
        ) or (
            profile == AUTHORITY_PROFILE_ID and bibliographic
        ):
            raise CatalogError(
                CatalogCode.PROFILE_INCOMPATIBLE, "Tipo da raiz incompatível com o perfil."
            )
    descriptor = load_legacy_profile(profile)
    report = adapt_report(validate_profile(graph, profile), descriptor, graph)
    if vocabularies is not None:
        membership_issues = validate_profile_vocabulary_membership(
            graph,
            descriptor,
            vocabularies,
            operation=operation,
            previous_graph=previous_graph,
        )
        if membership_issues:
            report = report.model_copy(
                update={
                    "conforms": report.conforms is True
                    and not any(issue.severity == "Violation" for issue in membership_issues),
                    "issues": ordered((*report.issues, *membership_issues)),
                }
            )
    if report.conforms is not True:
        raise CatalogValidationRejected(report)
    document = serialize_document(graph, "json-ld")
    if any("\x00" in str(term) for triple in graph for term in triple):
        raise CatalogError(
            CatalogCode.IDENTITY_INVALID,
            "PostgreSQL JSONB não suporta o caractere NUL em termos RDF.",
        )
    value: object = json.loads(document)
    if not isinstance(value, list) or not all(isinstance(node, dict) for node in value):
        raise ValueError("A representação JSON-LD expandida deve ser uma lista de nós.")
    return value


class BibliographicService:
    """Internal operations; owns transactions and never mutates earlier revisions."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        authorization: AuthorizationGuard | None = None,
    ) -> None:
        self.authorization = authorization or AuthorizationGuard()
        self.sessions = sessions
        self.store = RevisionStore()
        self.audit = AuditStore()
        self.editorial = EditorialStore()

    def _context(
        self,
        context: ExecutionContext | None,
        actor: AuthenticatedActor | None,
        execution_actor: ExecutionActor | None = None,
    ) -> ExecutionContext | None:
        # ExecutionActor is audit metadata, never a credential or a permission grant.
        if execution_actor is not None:
            if (
                not isinstance(context, TechnicalContext)
                or context.actor_id != execution_actor.actor_id
                or actor is not None
            ):
                raise PermissionDenied("Ator técnico exige contexto autorizado correspondente.")
        return execution_context(context, actor)

    def _audit_context(
        self, context: ExecutionContext | None
    ) -> tuple[str | None, ActorType | None]:
        if isinstance(context, HumanContext):
            return audit_actor(context.actor)
        if isinstance(context, TechnicalContext):
            kind = ActorType(context.actor_id.split(":", 1)[0])
            return audit_actor(None, ExecutionActor(context.actor_id, kind))
        raise PermissionDenied("Contexto obrigatório para escrita.")

    def _require_content(
        self,
        context: ExecutionContext | None,
        profile: str,
        *,
        create: bool,
        resource_id: UUID | None = None,
    ) -> None:
        kind = K.AUTHORITY if profile == AUTHORITY_PROFILE_ID else K.BIBLIOGRAPHIC
        operation = (
            O.AUTHORITY_MANAGE
            if kind == K.AUTHORITY
            else O.BIBLIOGRAPHIC_CREATE
            if create
            else O.BIBLIOGRAPHIC_UPDATE
        )
        self.authorization.require(
            context=context,
            operation=operation,
            resource=ResourceContext(kind, resource_id=resource_id),
        )

    def _require_existing(
        self, context: ExecutionContext | None, *, create: bool, resource_id: UUID | None = None
    ) -> None:
        for profile in (PROFILE_ID, AUTHORITY_PROFILE_ID):
            try:
                self._require_content(context, profile, create=create, resource_id=resource_id)
                return
            except PermissionDenied:
                if profile == AUTHORITY_PROFILE_ID:
                    raise

    async def create(
        self,
        identity: ResourceIdentity,
        graph: Graph,
        provenance: Provenance,
        *,
        profile: str = PROFILE_ID,
        actor: AuthenticatedActor | None = None,
        execution_actor: ExecutionActor | None = None,
        context: ExecutionContext | None = None,
    ) -> Revision:
        context = self._context(context, actor, execution_actor)
        self._require_content(context, profile, create=True, resource_id=identity.internal_id)
        actor_id, actor_type = self._audit_context(context)
        revision = Revision(
            identity,
            1,
            datetime.now(UTC),
            provenance,
            profile,
            actor_id,
        )
        if profile_uses_membership_policy(profile, "create"):
            async with self.sessions() as session, session.begin():
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
                await self._create(session, revision, document, actor_type)
        else:
            document = prepare_document(
                graph,
                identity.uri,
                profile,
                operation="create",
            )
            async with self.sessions() as session, session.begin():
                await self._create(session, revision, document, actor_type)
        return revision

    async def _create(
        self,
        session: AsyncSession,
        revision: Revision,
        document: list[dict[str, object]],
        actor_type: ActorType | None,
    ) -> None:
        """Shared persistence steps; the calling operation owns the transaction."""
        await write_gate(session)
        await self.store.create(session, revision.identity)
        await self.store.save(session, revision, document)
        resource = await self.editorial.lock_resource(session, revision.identity.internal_id)
        await self.editorial.initialize(session, resource)
        await self.audit.append(
            session, content_event(revision, AuditEventType.CREATED, actor_type)
        )

    async def revise(
        self,
        identifier: UUID,
        expected_revision: int,
        graph: Graph,
        provenance: Provenance,
        *,
        actor: AuthenticatedActor | None = None,
        execution_actor: ExecutionActor | None = None,
        expected_editorial_version: int | None = None,
        context: ExecutionContext | None = None,
    ) -> Revision:
        context = self._context(context, actor, execution_actor)
        # Kind is owned by storage: preflight the two explicit alternatives, then
        # check the stored profile before any mutation (never trust the proposed graph).
        self._require_existing(context, create=False, resource_id=identifier)
        actor_id, actor_type = self._audit_context(context)
        async with self.sessions() as session, session.begin():
            revision = await self._revise(
                session,
                identifier,
                expected_revision,
                graph,
                provenance,
                actor_id,
                actor_type,
                AuditEventType.REVISED,
                expected_editorial_version=expected_editorial_version,
                context=context,
            )
        return revision

    async def _revise(
        self,
        session: AsyncSession,
        identifier: UUID,
        expected_revision: int,
        graph: Graph,
        provenance: Provenance,
        actor_id: str | None,
        actor_type: ActorType | None,
        event_type: AuditEventType,
        source_revision: int | None = None,
        expected_editorial_version: int | None = None,
        context: ExecutionContext | None = None,
    ) -> Revision:
        if event_type == AuditEventType.RESTORED:
            self.authorization.require(
                context=context,
                operation=O.METADATA_REVISION_RESTORE,
                resource=ResourceContext(K.METADATA, resource_id=identifier),
            )
        else:
            self._require_existing(context, create=False, resource_id=identifier)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("A revisão esperada deve ser positiva.")
        await write_gate(session)
        resource = await self.editorial.lock_resource(session, identifier)
        try:
            previous, prior_document = await self.store.get(session, identifier, None)
        except LookupError as exc:
            raise StaleRevisionError("Recurso ausente; recarregue o registro.") from exc
        if event_type != AuditEventType.RESTORED:
            self._require_content(context, previous.profile, create=False, resource_id=identifier)
        state = await self.editorial.initialize(session, resource)
        if expected_editorial_version is not None:
            state.require_expected(expected_editorial_version, expected_revision)
        uri = await self.store.advance(session, identifier, expected_revision)
        prior_graph = parse_document(json.dumps(prior_document), "json-ld")
        operation: Literal["restore", "update"] = (
            "restore" if event_type == AuditEventType.RESTORED else "update"
        )
        vocabularies = await vocabulary_registry_for_profile(
            session, previous.profile, operation=operation
        )
        document = prepare_document(
            graph,
            uri,
            previous.profile,
            vocabularies=vocabularies,
            operation=operation,
            previous_graph=None if event_type == AuditEventType.RESTORED else prior_graph,
        )
        if previous.profile != PROFILE_ID:
            if check_snapshot(prior_graph, URIRef(uri)) != check_snapshot(graph, URIRef(uri)):
                raise CatalogError(
                    CatalogCode.IDENTITY_INVALID,
                    "O tipo da identidade não muda entre revisões.",
                )
        revision = Revision(
            ResourceIdentity(identifier, uri),
            expected_revision + 1,
            datetime.now(UTC),
            provenance,
            previous.profile,
            actor_id,
        )
        next_state = prepare_content_revision(
            state,
            revision,
            expected_version=state.version,
            expected_current_revision=expected_revision,
        )
        await self.store.save(session, revision, document)
        await self.editorial.save(session, state, next_state)
        await self.audit.append(
            session,
            content_event(
                revision,
                event_type,
                actor_type,
                source_revision=source_revision,
            ),
        )
        return revision

    async def restore_content(
        self,
        identifier: UUID,
        source_revision: int,
        expected_revision: int,
        provenance: Provenance,
        *,
        actor: AuthenticatedActor | None = None,
        expected_editorial_version: int | None = None,
        context: ExecutionContext | None = None,
    ) -> Revision:
        """Copy an owned historical snapshot as new content, never an editorial restore."""
        context = self._context(context, actor)
        self.authorization.require(
            context=context,
            operation=O.METADATA_REVISION_RESTORE,
            resource=ResourceContext(K.METADATA, resource_id=identifier),
        )
        actor_id, actor_type = self._audit_context(context)
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("A revisão esperada deve ser positiva.")
        if type(source_revision) is not int or not 1 <= source_revision <= expected_revision:
            raise ValueError("Revisão de origem deve pertencer ao histórico esperado.")
        async with self.sessions() as session, session.begin():
            await write_gate(session)
            await self.editorial.lock_resource(session, identifier)
            historical, document = await self.store.get(session, identifier, source_revision)
            current, _ = await self.store.get(session, identifier, None)
            if historical.identity != current.identity or historical.profile != current.profile:
                raise ValueError("Restauração não migra perfil ou identidade.")
            graph = parse_document(json.dumps(document), "json-ld")
            revision = await self._revise(
                session,
                identifier,
                expected_revision,
                graph,
                provenance,
                actor_id,
                actor_type,
                AuditEventType.RESTORED,
                source_revision,
                expected_editorial_version,
                context,
            )
        return revision

    async def read(
        self,
        identifier: UUID,
        number: int | None = None,
        *,
        actor: AuthenticatedActor | None = None,
        context: ExecutionContext | None = None,
    ) -> tuple[Revision, Graph]:
        context = self._context(context, actor)
        for kind, operation in (
            (K.BIBLIOGRAPHIC, O.BIBLIOGRAPHIC_READ_EDITORIAL),
            (K.AUTHORITY, O.AUTHORITY_READ_EDITORIAL),
        ):
            try:
                self.authorization.require(
                    context=context,
                    operation=operation,
                    resource=ResourceContext(kind, resource_id=identifier),
                )
                break
            except PermissionDenied:
                if kind == K.AUTHORITY:
                    raise
        async with self.sessions() as session:
            revision, document = await self.store.get(session, identifier, number)
            kind = K.AUTHORITY if revision.profile == AUTHORITY_PROFILE_ID else K.BIBLIOGRAPHIC
            operation = (
                O.AUTHORITY_READ_EDITORIAL
                if kind == K.AUTHORITY
                else O.BIBLIOGRAPHIC_READ_EDITORIAL
            )
            self.authorization.require(
                context=context,
                operation=operation,
                resource=ResourceContext(kind, resource_id=identifier),
            )
        return revision, parse_document(json.dumps(document), "json-ld")

    async def read_public(self, identifier: UUID) -> tuple[Revision, Graph] | None:
        """Trusted internal public projection; legacy has no inferred publication."""
        async with self.sessions() as session, session.begin():
            await write_gate(session)
            await self.editorial.lock_resource(session, identifier)
            state = await self.editorial.get(session, identifier)
            if state is None or state.public_revision is None:
                return None
            revision, document = await self.store.get(session, identifier, state.public_revision)
            authority = revision.profile == AUTHORITY_PROFILE_ID
            self.authorization.require(
                context=None,
                operation=O.AUTHORITY_READ_PUBLIC if authority else O.BIBLIOGRAPHIC_READ_PUBLIC,
                resource=ResourceContext(
                    K.AUTHORITY if authority else K.BIBLIOGRAPHIC,
                    state.editorial_state,
                    state.public_revision,
                    state.public_revision,
                    resource_id=identifier,
                ),
            )
        return revision, parse_document(json.dumps(document), "json-ld")
