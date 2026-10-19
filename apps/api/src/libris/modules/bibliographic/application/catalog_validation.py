"""Pré-validação autorizada: transações explicitamente somente leitura."""

from rdflib import URIRef
from rdflib.namespace import RDF, SKOS
from sqlalchemy import text

from libris.modules.bibliographic.api.schemas.validation import (
    DtoValidation,
    RdfValidation,
    RevisionValidation,
    ValidationRequest,
)
from libris.modules.bibliographic.application.catalog import CatalogService, resolve_references
from libris.modules.bibliographic.application.diagnostics import (
    CatalogValidationResult,
)
from libris.modules.bibliographic.application.editorial_postgres import entity_kind
from libris.modules.bibliographic.application.validation import validate_catalog
from libris.modules.bibliographic.application.vocabulary_validation import (
    profile_uses_membership_policy,
    vocabulary_registry_for_profile,
)
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.profile_contract import ResolvedProfile
from libris.modules.bibliographic.domain.profile_registry import ProfileError
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot
from libris.modules.bibliographic.infrastructure.profiles import (
    LEGACY_PROFILES,
    load_legacy_profile,
    load_profile_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.serialization import (
    ResourceCreate,
    parse_representation,
)
from libris.modules.bibliographic.infrastructure.serialization.adapters import prepare_create_graph
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
)
from libris.modules.identity.application.authorization import ExecutionContext
from libris.modules.identity.domain.authorization import Operation, ResourceContext, ResourceKind


def resolve_profile(release: str) -> str | ResolvedProfile:
    if release in LEGACY_PROFILES:
        return release
    profile = load_profile_registry().resolve(release)
    if profile.status not in {"candidate", "published"}:
        raise ProfileError("inapplicable_profile", "Release não selecionável.")
    return profile


def require_profile_read(service: CatalogService, context: ExecutionContext) -> None:
    service.authorization.require(
        context=context,
        operation=Operation.METADATA_REVISION_READ,
        resource=ResourceContext(ResourceKind.METADATA),
    )


async def prevalidate(
    service: CatalogService,
    payload: ValidationRequest,
    entity: EntityType,
    model: type[ResourceCreate],
    context: ExecutionContext,
    base_uri: str,
) -> CatalogValidationResult:
    default_profile = (
        AUTHORITY_PROFILE_ID
        if entity in {EntityType.AGENT, EntityType.SUBJECT}
        else CONTROLLED_VOCABULARY_PROFILE_ID
    )
    service.require_operation(context, default_profile, "create")
    if isinstance(payload, RevisionValidation):
        revision, graph = await service.read(payload.resource_id, payload.revision, context=context)
        if entity_kind(check_snapshot(graph, URIRef(revision.identity.uri))) != entity:
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Tipo incompatível com a coleção.")
        vocabularies = VocabularyRegistry()
        if profile_uses_membership_policy(revision.profile, "prevalidation"):
            async with service.sessions() as session, session.begin():
                await session.execute(text("SET TRANSACTION READ ONLY"))
                vocabularies = await vocabulary_registry_for_profile(
                    session, revision.profile, operation="prevalidation"
                )
        return validate_catalog(
            graph, revision.profile, vocabularies=vocabularies, operation="prevalidation"
        )
    profile = resolve_profile(payload.profile_release)
    descriptor = load_legacy_profile(profile) if isinstance(profile, str) else profile
    if isinstance(profile, ResolvedProfile) and entity not in profile.applies_to:
        raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Perfil inaplicável.")
    if isinstance(payload, DtoValidation):
        if type(payload.resource) is not model:
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "DTO incompatível com a coleção.")
        identity = ResourceIdentity.create(base_uri)
        # A sessão não pode gravar mesmo se um adaptador futuro regredir.
        vocabularies = VocabularyRegistry()
        async with service.sessions() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            references = await resolve_references(session, payload.resource, lock=False)
            graph = prepare_create_graph(
                payload.resource, identity, profile=profile, references=references
            )
            if profile_uses_membership_policy(profile, "prevalidation"):
                vocabularies = await vocabulary_registry_for_profile(
                    session, profile, operation="prevalidation"
                )
        kind = check_snapshot(graph, URIRef(identity.uri))
        if str(kind) not in {r.rdf_class for r in descriptor.resources}:
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Perfil inaplicável.")
        return validate_catalog(
            graph, profile, vocabularies=vocabularies, operation="prevalidation"
        )
    assert isinstance(payload, RdfValidation)
    graph = parse_representation(payload.document, payload.format)
    if payload.context == "snapshot":
        kind = check_snapshot(graph, URIRef(payload.root))
        if entity_kind(kind) != entity or str(kind) not in {
            r.rdf_class for r in descriptor.resources
        }:
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Raiz incompatível com o perfil.")
    else:
        # O modo composto não fabrica inversas nem carrega outros registros.
        if not any(
            entity_kind(kind) == entity
            for kind in graph.objects(URIRef(payload.root), RDF.type)
            if kind
            in {BF.Work, BF.Instance, BF.Item, BF.Agent, BF.Person, BF.Organization, SKOS.Concept}
            and isinstance(kind, URIRef)
            and str(kind) in {r.rdf_class for r in descriptor.resources}
        ):
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Raiz composta incompatível.")
    vocabularies = VocabularyRegistry()
    if profile_uses_membership_policy(profile, "prevalidation"):
        async with service.sessions() as session, session.begin():
            await session.execute(text("SET TRANSACTION READ ONLY"))
            vocabularies = await vocabulary_registry_for_profile(
                session, profile, operation="prevalidation"
            )
    return validate_catalog(
        graph,
        profile,
        context=payload.context,
        vocabularies=vocabularies,
        operation="prevalidation",
    )
