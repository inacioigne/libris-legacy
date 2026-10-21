"""Executable policy and transaction-contract tests; doubles do not prove PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from itertools import product
from uuid import UUID

import pytest
from libris.modules.bibliographic.api.schemas.common import LocalReference
from libris.modules.bibliographic.api.schemas.relationships import (
    DirectLink,
    RelationshipCreate,
)
from libris.modules.bibliographic.application.editorial import (
    CatalogPublicationRejected,
    EditorialService,
    EditorialTransaction,
    PublicationContext,
)
from libris.modules.bibliographic.application.relationships import ResolvedEntity
from libris.modules.bibliographic.domain.editorial import (
    TRANSITIONS,
    EditorialRecord,
    EditorialScope,
    PreparedTransition,
    PublicationReadiness,
    prepare_content_revision,
    prepare_transition,
    prepare_workspace_publish,
)
from libris.modules.bibliographic.domain.editorial import (
    EditorialState as S,
)
from libris.modules.bibliographic.domain.errors import CatalogCode as C
from libris.modules.bibliographic.domain.errors import CatalogError
from libris.modules.bibliographic.domain.identity_lifecycle import (
    IdentityDecision,
    IdentityLifecycle,
    IdentityStatus,
)
from libris.modules.bibliographic.domain.models import (
    Provenance,
    ResourceIdentity,
    Revision,
)
from libris.modules.bibliographic.domain.relationships import EntityType
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
    VocabularyConceptEvidence,
    VocabularyDefinition,
    VocabularyRegistry,
)
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.infrastructure.role_mapper import map_permissions
from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import RDF, RDFS, SKOS

NOW = datetime.now(UTC)
IDENTITY = ResourceIdentity.create("https://example.org/resources")


def record(state: S = S.DRAFT, *, number: int = 1) -> EditorialRecord:
    return EditorialRecord(
        IDENTITY,
        current_revision=number,
        editorial_state=state,
        approved_revision=number if state in {S.APPROVED, S.PUBLISHED, S.WITHDRAWN} else None,
        published_revision=number if state in {S.PUBLISHED, S.WITHDRAWN, S.ARCHIVED} else None,
    )


def transition(before: EditorialRecord, target: S, **kwargs: object) -> PreparedTransition:
    selected = (
        before.published_revision
        if (before.source(kwargs.get("scope", EditorialScope.RECORD)) in {S.PUBLISHED, S.WITHDRAWN})
        else before.current_revision
    )
    params = dict(
        expected_version=before.version,
        expected_current_revision=before.current_revision,
        selected_revision=selected,
        actor_id="actor",
        occurred_at=NOW,
        justification="Decisão",
        readiness=PublicationReadiness(before.identity, selected, INSTITUTIONAL_PROFILE_ID),
    )
    params.update(kwargs)
    return prepare_transition(before, target, **params)


def revision(number: int = 1, profile: str = INSTITUTIONAL_PROFILE_ID) -> Revision:
    return Revision(IDENTITY, number, NOW, Provenance("test", "test"), profile)


def actor(role: str) -> AuthenticatedActor:
    roles = frozenset({role})
    return AuthenticatedActor(
        actor_identifier("issuer", "subject"), "issuer", "subject", roles, map_permissions(roles)
    )


@pytest.mark.parametrize("source,target", list(product(S, S)))
def test_entire_matrix(source: S, target: S) -> None:
    before = record(source)
    if (source, target) not in TRANSITIONS:
        with pytest.raises(CatalogError) as error:
            transition(before, target)
        assert error.value.code == C.TRANSITION_FORBIDDEN
        assert before == record(source)
    else:
        result = transition(before, target)
        assert result.after.editorial_state == target
        assert result.after.version == before.version + 1
        assert result.after.identity == before.identity
        assert result.after.current_revision == before.current_revision
        assert result.event.previous_state == source
        assert result.event.next_state == target
        assert result.event.revision == 1
        assert result.event.actor_id == "actor"
        assert result.event.occurred_at == NOW


def test_initial_draft_and_unknown_states() -> None:
    initial = EditorialRecord(IDENTITY)
    assert initial.editorial_state == S.DRAFT
    assert initial.public_revision is None
    for source, target in [("unknown", "draft"), ("draft", "unknown")]:
        with pytest.raises(CatalogError) as error:
            if source == "unknown":
                replace(initial, editorial_state=source)
            else:
                transition(initial, target)
        assert error.value.code == C.EDITORIAL_STATE_INVALID


def test_workspace_publishes_validated_draft_without_intermediate_states() -> None:
    before = record()
    change = prepare_workspace_publish(
        before,
        expected_version=1,
        expected_current_revision=1,
        actor_id="actor",
        occurred_at=NOW,
        readiness=PublicationReadiness(before.identity, 1, INSTITUTIONAL_PROFILE_ID),
    )
    assert change.after.editorial_state == S.PUBLISHED
    assert change.after.approved_revision == 1
    assert change.after.published_revision == 1
    assert change.after.public_revision == 1
    assert change.after.version == 2
    assert change.event.previous_state == S.DRAFT
    assert change.event.next_state == S.PUBLISHED


def test_workspace_refuses_unvalidated_revision_and_legacy_intermediate_state() -> None:
    draft = record()
    with pytest.raises(CatalogError) as mismatch:
        prepare_workspace_publish(
            draft,
            expected_version=1,
            expected_current_revision=1,
            actor_id="actor",
            occurred_at=NOW,
            readiness=PublicationReadiness(draft.identity, 2, INSTITUTIONAL_PROFILE_ID),
        )
    assert mismatch.value.code == C.APPROVAL_STALE
    with pytest.raises(CatalogError) as intermediate:
        prepare_workspace_publish(
            record(S.IN_REVIEW),
            expected_version=1,
            expected_current_revision=1,
            actor_id="actor",
            occurred_at=NOW,
            readiness=PublicationReadiness(draft.identity, 1, INSTITUTIONAL_PROFILE_ID),
        )
    assert intermediate.value.code == C.TRANSITION_FORBIDDEN


@pytest.mark.parametrize(
    "source,target",
    [
        (S.IN_REVIEW, S.DRAFT),
        (S.APPROVED, S.DRAFT),
        (S.PUBLISHED, S.WITHDRAWN),
        (S.WITHDRAWN, S.ARCHIVED),
    ],
)
def test_justification_required(source: S, target: S) -> None:
    with pytest.raises(CatalogError) as error:
        transition(record(source), target, justification=" ")
    assert error.value.code == C.EDITORIAL_PRECONDITION


def test_approval_and_validation_are_required() -> None:
    with pytest.raises(CatalogError) as error:
        transition(record(S.DRAFT), S.PUBLISHED)
    assert error.value.code == C.TRANSITION_FORBIDDEN
    with pytest.raises(CatalogError) as error:
        transition(record(S.APPROVED), S.PUBLISHED, readiness=None)
    assert error.value.code == C.EDITORIAL_PRECONDITION
    for readiness in [
        PublicationReadiness(IDENTITY, 2, "profile"),
        PublicationReadiness(ResourceIdentity.create("https://other.test"), 1, "profile"),
    ]:
        with pytest.raises(CatalogError) as error:
            transition(record(S.APPROVED), S.PUBLISHED, readiness=readiness)
        assert error.value.code == C.APPROVAL_STALE
    with pytest.raises(CatalogError):
        replace(record(S.APPROVED, number=2), approved_revision=1)


def test_content_and_editorial_revisions_are_independent() -> None:
    published = transition(record(S.APPROVED), S.PUBLISHED).after
    old_revision = revision()
    candidate_revision = revision(2)
    edited = prepare_content_revision(
        published,
        candidate_revision,
        expected_version=published.version,
        expected_current_revision=1,
    )
    assert published.current_revision == 1
    assert old_revision.number == 1 and old_revision.identity == candidate_revision.identity
    assert edited.current_revision == 2 and edited.published_revision == edited.public_revision == 1
    assert edited.editorial_state == S.PUBLISHED and edited.candidate_state == S.DRAFT
    assert edited.approved_revision is None
    reviewing = transition(edited, S.IN_REVIEW, scope=EditorialScope.CANDIDATE).after
    approved = transition(reviewing, S.APPROVED, scope=EditorialScope.CANDIDATE).after
    with pytest.raises(CatalogError) as error:
        transition(approved, S.PUBLISHED, scope=EditorialScope.CANDIDATE, selected_revision=1)
    assert error.value.code == C.APPROVAL_STALE
    updated = transition(approved, S.PUBLISHED, scope=EditorialScope.CANDIDATE).after
    assert updated.public_revision == 2 and updated.current_revision == 2
    assert updated.candidate_state is None and updated.identity == published.identity
    assert published.public_revision == 1 and old_revision == revision()


def test_withdraw_republish_pending_and_archive() -> None:
    edited = prepare_content_revision(
        record(S.PUBLISHED),
        revision(2),
        expected_version=1,
        expected_current_revision=1,
    )
    withdrawn = transition(edited, S.WITHDRAWN).after
    assert withdrawn.public_revision is None and withdrawn.published_revision == 1
    assert withdrawn.candidate_state == S.DRAFT
    republished = transition(withdrawn, S.PUBLISHED).after
    assert republished.public_revision == 1 and republished.current_revision == 2
    archived = transition(withdrawn, S.ARCHIVED).after
    assert archived.editorial_state == S.ARCHIVED and archived.public_revision is None
    assert archived.published_revision == 1 and archived.current_revision == 2
    assert archived.candidate_state is None
    with pytest.raises(CatalogError):
        transition(archived, S.DRAFT)


@pytest.mark.parametrize("state", [S.IN_REVIEW, S.APPROVED, S.WITHDRAWN, S.ARCHIVED])
def test_content_edits_require_editable_cycle(state: S) -> None:
    with pytest.raises(CatalogError) as error:
        prepare_content_revision(
            record(state), revision(2), expected_version=1, expected_current_revision=1
        )
    assert error.value.code == C.TRANSITION_FORBIDDEN


def test_stale_editorial_and_content_expectations() -> None:
    before = record(S.IN_REVIEW)
    approved = transition(before, S.APPROVED).after
    with pytest.raises(CatalogError) as error:
        transition(approved, S.PUBLISHED, expected_version=before.version)
    assert error.value.code == C.EDITORIAL_VERSION_STALE
    with pytest.raises(CatalogError) as error:
        transition(before, S.APPROVED, expected_current_revision=2)
    assert error.value.code == C.REVISION_STALE


class Resolver:
    def __init__(self, entities: dict[UUID, ResolvedEntity]) -> None:
        self.entities = entities

    async def resolve(self, reference: LocalReference) -> ResolvedEntity | None:
        return self.entities.get(reference.internal_id)


def context(
    entity_type: EntityType = EntityType.WORK,
    profile: str | None = None,
    *,
    valid: bool = True,
    number: int = 1,
) -> PublicationContext:
    root = URIRef(IDENTITY.uri)
    graph = Graph()
    if entity_type == EntityType.WORK:
        graph.add((root, RDF.type, BF.Work))
        if valid:
            title = BNode()
            graph.add((root, BF.title, title))
            graph.add((title, RDF.type, BF.Title))
            graph.add((title, BF.mainTitle, Literal("Título")))
    elif entity_type == EntityType.INSTANCE:
        graph.add((root, RDF.type, BF.Instance))
        graph.add((root, BF.instanceOf, URIRef("https://example.org/work")))
    elif entity_type == EntityType.AGENT:
        graph.add((root, RDF.type, BF.Person))
        if valid:
            graph.add((root, RDFS.label, Literal("Nome")))
    else:
        graph.add((root, RDF.type, SKOS.Concept))
        if valid:
            graph.add((root, SKOS.prefLabel, Literal("Assunto")))
            graph.add((root, SKOS.inScheme, URIRef("https://example.org/scheme")))
    local = LocalReference(
        entity_type=entity_type, internal_id=IDENTITY.internal_id, uri=IDENTITY.uri
    )
    entities = {IDENTITY.internal_id: ResolvedEntity(IDENTITY, entity_type)}
    links = []
    if entity_type == EntityType.INSTANCE:
        target = ResourceIdentity.create("https://example.org/resources")
        stored_target = ResourceIdentity(target.internal_id, "https://example.org/work")
        entities[target.internal_id] = ResolvedEntity(stored_target, EntityType.WORK)
        links.append(
            DirectLink(
                source=local,
                predicate=str(BF.instanceOf),
                target=LocalReference(
                    entity_type=EntityType.WORK,
                    internal_id=target.internal_id,
                    uri=stored_target.uri,
                ),
            )
        )
    relationships = RelationshipCreate(source=local, links=links)
    chosen = profile or (
        AUTHORITY_PROFILE_ID
        if entity_type in {EntityType.AGENT, EntityType.SUBJECT}
        else INSTITUTIONAL_PROFILE_ID
    )
    return PublicationContext(revision(number, chosen), graph, relationships, Resolver(entities))


class MemoryTransactions:
    """Test-only atomic CAS double; intentionally absent from production infrastructure."""

    def __init__(
        self,
        initial: EditorialRecord,
        publication: PublicationContext | None = None,
        *,
        concurrent: bool = False,
    ) -> None:
        self.state = initial
        self.publication = publication
        self.changes: list[PreparedTransition] = []
        self.concurrent = concurrent
        self.read_count = 0
        self.both_read = asyncio.Event()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[EditorialTransaction]:
        yield self

    async def read(self, identifier: UUID) -> EditorialRecord:
        assert identifier == self.state.identity.internal_id
        snapshot = self.state
        if self.concurrent:
            self.read_count += 1
            if self.read_count == 2:
                self.both_read.set()
            await self.both_read.wait()
        return snapshot

    async def publication_context(self, identifier: UUID, number: int) -> PublicationContext | None:
        return self.publication

    async def compare_and_set(self, change: PreparedTransition) -> None:
        self.state.require_expected(change.before.version, change.before.current_revision)
        self.state = change.after
        self.changes.append(change)


async def execute(
    store: MemoryTransactions,
    target: S,
    role: str = "libris-admin",
    **kwargs: object,
) -> PreparedTransition:
    before = store.state
    params = dict(
        actor=actor(role),
        expected_version=before.version,
        expected_current_revision=before.current_revision,
        selected_revision=before.published_revision
        if before.editorial_state == S.WITHDRAWN
        else before.current_revision,
    )
    params.update(kwargs)
    return await EditorialService(store.transaction).transition(
        IDENTITY.internal_id, target, **params
    )


@pytest.mark.parametrize(
    "source,target,role",
    [
        (S.DRAFT, S.IN_REVIEW, "libris-reader"),
        (S.IN_REVIEW, S.APPROVED, "libris-cataloger"),
        (S.IN_REVIEW, S.DRAFT, "libris-cataloger"),
        (S.APPROVED, S.PUBLISHED, "libris-reviewer"),
        (S.PUBLISHED, S.WITHDRAWN, "libris-cataloger"),
        (S.WITHDRAWN, S.PUBLISHED, "libris-cataloger"),
        (S.WITHDRAWN, S.ARCHIVED, "libris-cataloger"),
    ],
)
def test_permissions_reject_without_mutation(source: S, target: S, role: str) -> None:
    before = record(source)
    store = MemoryTransactions(before, context())
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, target, role, justification="Decisão"))
    assert error.value.code == C.EDITORIAL_PERMISSION_DENIED
    assert store.state == before and not store.changes


def test_authorized_submit_approve_publish_and_republish() -> None:
    store = MemoryTransactions(record(), context())

    async def scenario() -> None:
        await execute(store, S.IN_REVIEW, "libris-cataloger")
        await execute(store, S.APPROVED, "libris-reviewer")
        assert store.state.approved_revision == 1
        await execute(store, S.PUBLISHED, "libris-publisher")
        await execute(store, S.WITHDRAWN, "libris-publisher", justification="Correção")
        await execute(store, S.PUBLISHED, "libris-publisher")

    asyncio.run(scenario())
    assert store.state.public_revision == 1 and store.state.current_revision == 1
    assert len(store.changes) == 5


@pytest.mark.parametrize(
    "entity,profile",
    [
        (EntityType.WORK, INSTITUTIONAL_PROFILE_ID),
        (EntityType.AGENT, AUTHORITY_PROFILE_ID),
        (EntityType.SUBJECT, AUTHORITY_PROFILE_ID),
        (EntityType.WORK, "urn:libris:profile:work:2.0.0"),
        (EntityType.AGENT, "urn:libris:profile:agent:2.0.0"),
    ],
)
def test_real_validation_bibliographic_and_authority(entity: EntityType, profile: str) -> None:
    publication = context(entity, profile)
    store = MemoryTransactions(record(S.APPROVED), publication)
    triples = frozenset(publication.graph)
    asyncio.run(execute(store, S.PUBLISHED, "libris-publisher"))
    assert store.state.public_revision == 1
    assert frozenset(publication.graph) == triples


@pytest.mark.parametrize("entity", [EntityType.WORK, EntityType.AGENT, EntityType.SUBJECT])
def test_invalid_catalog_blocks_publication(entity: EntityType) -> None:
    before = record(S.APPROVED)
    store = MemoryTransactions(before, context(entity, valid=False))
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.PUBLISHED))
    assert error.value.code == C.CONTENT_NOT_PUBLISHABLE
    assert store.state == before and not store.changes


@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing", C.EDITORIAL_PRECONDITION),
        ("unknown_profile", C.PROFILE_UNKNOWN),
        ("wrong_profile", C.PROFILE_INCOMPATIBLE),
        ("wrong_revision", C.APPROVAL_STALE),
        ("incomplete_links", C.REFERENCE_UNVERIFIED),
        ("invalid_local", C.REFERENCE_INVALID),
    ],
)
def test_publication_dependencies_fail_closed(failure: str, code: C) -> None:
    before = record(S.APPROVED)
    publication = context()
    if failure == "missing":
        publication = None
    elif failure == "unknown_profile":
        publication = context(profile="urn:unknown")
    elif failure == "wrong_profile":
        publication = context(EntityType.AGENT, "urn:libris:profile:work:2.0.0")
    elif failure == "wrong_revision":
        publication = context(number=2)
    else:
        publication = context(EntityType.INSTANCE)
        if failure == "incomplete_links":
            publication = context()
            publication.graph.add(
                (URIRef(IDENTITY.uri), BF.relatedTo, URIRef("https://other.test/work"))
            )
        if failure == "invalid_local":
            target = ResourceIdentity.create("https://example.org/resources")
            link = DirectLink(
                source=publication.relationships.source,
                predicate=str(BF.instanceOf),
                target=LocalReference(
                    entity_type=EntityType.WORK,
                    internal_id=target.internal_id,
                    uri="https://example.org/work",
                ),
            )
            publication = replace(
                publication,
                relationships=RelationshipCreate(
                    source=publication.relationships.source, links=[link]
                ),
            )
    store = MemoryTransactions(before, publication)
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.PUBLISHED))
    assert error.value.code == code
    assert store.state == before and not store.changes


@pytest.mark.parametrize(
    "source,target",
    [(S.IN_REVIEW, S.APPROVED), (S.APPROVED, S.PUBLISHED), (S.PUBLISHED, S.WITHDRAWN)],
)
def test_two_concurrent_operations_have_one_winner(source: S, target: S) -> None:
    store = MemoryTransactions(record(source), context(), concurrent=True)

    async def scenario() -> list[PreparedTransition | BaseException]:
        return await asyncio.gather(
            execute(store, target, justification="Decisão"),
            execute(store, target, justification="Decisão"),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    conflicts = [result for result in results if isinstance(result, CatalogError)]
    assert len(conflicts) == 1 and conflicts[0].code == C.EDITORIAL_VERSION_STALE
    assert len(store.changes) == 1


def test_content_race_cannot_publish_obsolete_candidate() -> None:
    pending = prepare_content_revision(
        record(S.PUBLISHED),
        revision(2),
        expected_version=1,
        expected_current_revision=1,
    )
    reviewing = transition(pending, S.IN_REVIEW, scope=EditorialScope.CANDIDATE).after
    approved = transition(reviewing, S.APPROVED, scope=EditorialScope.CANDIDATE).after
    store = MemoryTransactions(approved, context(number=2))
    # Transaction dependency changes while catalog validation is awaiting references.
    original = store.publication_context

    async def race(identifier: UUID, number: int) -> PublicationContext | None:
        publication = await original(identifier, number)
        returned = transition(store.state, S.DRAFT, scope=EditorialScope.CANDIDATE).after
        store.state = prepare_content_revision(
            returned,
            revision(3),
            expected_version=returned.version,
            expected_current_revision=2,
        )
        return publication

    store.publication_context = race
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.PUBLISHED, scope=EditorialScope.CANDIDATE))
    assert error.value.code == C.EDITORIAL_VERSION_STALE
    assert store.state.public_revision == 1 and store.state.current_revision == 3
    assert not store.changes


@pytest.mark.parametrize(
    "status",
    [IdentityStatus.DISCONTINUED, IdentityStatus.REPLACED, IdentityStatus.MERGED],
)
def test_retirement_preserves_identity_and_history(status: IdentityStatus) -> None:
    active = IdentityLifecycle(IDENTITY)
    successor = ResourceIdentity.create("https://example.org/resources")
    decision = IdentityDecision(
        IDENTITY,
        status,
        None if status == IdentityStatus.DISCONTINUED else successor,
        "actor",
        NOW,
        "Decisão",
        "Evidência",
    )
    retired = active.retire(decision)
    assert active.status == IdentityStatus.ACTIVE and not active.history
    assert retired.reserved_identity == IDENTITY and retired.history == (decision,)
    assert (
        retired.identity.internal_id == IDENTITY.internal_id
        and retired.identity.uri == IDENTITY.uri
    )
    with pytest.raises(CatalogError) as error:
        retired.retire(decision)
    assert error.value.code == C.IDENTITY_OPERATION_INVALID


@pytest.mark.parametrize(
    "status,successor,evidence",
    [
        (IdentityStatus.REPLACED, None, None),
        (IdentityStatus.MERGED, None, "evidence"),
        (IdentityStatus.MERGED, IDENTITY, "evidence"),
        (
            IdentityStatus.MERGED,
            ResourceIdentity.create("https://example.org/resources"),
            None,
        ),
        (IdentityStatus.DISCONTINUED, IDENTITY, None),
    ],
)
def test_invalid_identity_decisions(
    status: IdentityStatus, successor: ResourceIdentity | None, evidence: str | None
) -> None:
    with pytest.raises(CatalogError) as error:
        IdentityDecision(IDENTITY, status, successor, "actor", NOW, "Decisão", evidence)
    assert error.value.code == C.IDENTITY_OPERATION_INVALID


@pytest.mark.parametrize(
    "failure,code",
    [
        ("engine_failure", C.VALIDATOR_FAILURE),
        ("shapes_unavailable", C.INFRASTRUCTURE_UNAVAILABLE),
    ],
)
def test_shacl_unavailable_never_produces_publication(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    code: C,
) -> None:
    from libris.modules.bibliographic.application.editorial import (
        CatalogPublicationRejected,
    )
    from libris.modules.bibliographic.infrastructure import validation

    def fail(*args: object, **kwargs: object) -> None:
        raise validation.ShaclValidationError(failure, "Falha controlada")

    monkeypatch.setattr(validation, "_validate_shapes", fail)
    before = record(S.APPROVED)
    store = MemoryTransactions(before, context())
    with pytest.raises(CatalogPublicationRejected) as error:
        asyncio.run(execute(store, S.PUBLISHED))
    assert error.value.code == code
    assert error.value.result.state == "failed" and error.value.result.conforms is None
    assert store.state == before and not store.changes


@pytest.mark.parametrize("failure", ["missing", "type", "uri"])
def test_required_local_reference_integrity(failure: str) -> None:
    publication = context(EntityType.INSTANCE)
    target = publication.relationships.links[0].target
    assert isinstance(target, LocalReference) and target.internal_id is not None
    if failure == "missing":
        del publication.resolver.entities[target.internal_id]
    elif failure == "type":
        original = publication.resolver.entities[target.internal_id]
        publication.resolver.entities[target.internal_id] = replace(
            original, entity_type=EntityType.AGENT
        )
    else:
        original = publication.resolver.entities[target.internal_id]
        publication.resolver.entities[target.internal_id] = replace(
            original, identity=ResourceIdentity(target.internal_id, "https://example.org/different")
        )
    before = record(S.APPROVED)
    store = MemoryTransactions(before, publication)
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.PUBLISHED))
    assert error.value.code == C.REFERENCE_INVALID
    assert store.state == before and not store.changes


def test_required_local_reference_can_be_published() -> None:
    store = MemoryTransactions(record(S.APPROVED), context(EntityType.INSTANCE))
    asyncio.run(execute(store, S.PUBLISHED))
    assert store.state.public_revision == 1


def test_publication_blocks_only_proven_absence_and_keeps_timeout_as_warning() -> None:
    profile = CONTROLLED_VOCABULARY_PROFILE_ID
    language = "http://id.loc.gov/vocabulary/languages/por"
    vocabulary = "http://id.loc.gov/vocabulary/languages/"
    source = "urn:test:trusted-vocabulary-source"
    publication = context(profile=profile)
    publication.graph.add((URIRef(IDENTITY.uri), BF.language, URIRef(language)))
    absent = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=vocabulary,
                release="complete-1",
                source=source,
                complete=True,
            ),
        )
    )
    rejected = MemoryTransactions(
        record(S.APPROVED), replace(publication, vocabularies=absent)
    )
    with pytest.raises(CatalogPublicationRejected) as error:
        asyncio.run(execute(rejected, S.PUBLISHED))
    assert error.value.result.conforms is False
    assert any(
        issue.code == C.VOCABULARY_MISMATCH and issue.field_ids == ("/language",)
        for issue in error.value.result.issues
    )
    assert rejected.state.editorial_state == S.APPROVED and not rejected.changes

    unavailable = VocabularyRegistry(
        vocabularies=(
            VocabularyDefinition(
                uri=vocabulary,
                release="complete-1",
                source=source,
                concepts=(
                    VocabularyConceptEvidence(uri=language, status="unavailable"),
                ),
                complete=True,
            ),
        )
    )
    accepted = MemoryTransactions(
        record(S.APPROVED), replace(publication, vocabularies=unavailable)
    )
    asyncio.run(execute(accepted, S.PUBLISHED))
    assert accepted.state.public_revision == 1
    assert len(accepted.changes) == 1


def test_content_cas_is_checked_even_when_editorial_version_matches() -> None:
    class ContentRace(MemoryTransactions):
        async def read(self, identifier: UUID) -> EditorialRecord:
            before = await super().read(identifier)
            # Models a content writer that does not advance the editorial token.
            self.state = replace(before, current_revision=2)
            return before

    store = ContentRace(record())
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.IN_REVIEW, "libris-cataloger"))
    assert error.value.code == C.REVISION_STALE
    assert store.state.version == 1 and store.state.current_revision == 2
    assert not store.changes


def test_authority_manager_needs_explicit_editorial_capability() -> None:
    store = MemoryTransactions(record())
    with pytest.raises(CatalogError) as error:
        asyncio.run(execute(store, S.IN_REVIEW, "libris-authority-manager"))
    assert error.value.code == C.EDITORIAL_PERMISSION_DENIED
    assert not store.changes


@pytest.mark.parametrize(
    "target", [S.IN_REVIEW, S.APPROVED, S.DRAFT, S.PUBLISHED, S.WITHDRAWN, S.ARCHIVED]
)
def test_guard_denies_before_editorial_transaction(target: S) -> None:
    from unittest.mock import MagicMock

    from libris.modules.identity.application.authorization import TechnicalContext
    from libris.modules.identity.domain.permissions import Permission

    transactions = MagicMock()
    service = EditorialService(transactions)
    technical = TechnicalContext(
        "service:editorial-probe",
        frozenset({Permission.EDITORIAL_APPROVE, Permission.EDITORIAL_PUBLISH}),
        frozenset(),
        "pytest:composition-root",
    )
    for kwargs in ({}, {"actor": None}, {"actor": actor("libris-reader")}, {"context": technical}):
        with pytest.raises(CatalogError) as error:
            asyncio.run(
                service.transition(
                    IDENTITY.internal_id,
                    target,
                    expected_version=1,
                    expected_current_revision=1,
                    selected_revision=1,
                    **kwargs,
                )
            )
        assert error.value.code == C.EDITORIAL_PERMISSION_DENIED
    transactions.assert_not_called()


def test_typed_editorial_denial_maps_to_403_without_changing_legacy_catalog_fallback() -> None:
    from libris.api.problems import classify
    from libris.modules.bibliographic.application.editorial import (
        EditorialPermissionDenied,
    )
    from libris.modules.identity.domain.exceptions import PermissionDenied

    denial = EditorialPermissionDenied()
    assert isinstance(denial, CatalogError) and isinstance(denial, PermissionDenied)
    assert denial.code == C.EDITORIAL_PERMISSION_DENIED
    assert classify(denial) == (403, "authorization.denied")
    assert classify(CatalogError(C.EDITORIAL_PERMISSION_DENIED, "Legacy")) == (
        422,
        C.EDITORIAL_PERMISSION_DENIED.value,
    )
