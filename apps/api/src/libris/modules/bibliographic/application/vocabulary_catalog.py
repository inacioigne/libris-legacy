"""Transactional use cases for the persistent operational vocabulary catalog."""

from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libris.modules.bibliographic.api.schemas.common import IRI, Contract
from libris.modules.bibliographic.application.controlled_values import (
    ControlledConcept,
    LocalConcept,
)
from libris.modules.bibliographic.application.vocabulary_resolution import (
    ResolutionCachePolicy,
    ResolutionFailure,
    ResolutionResult,
    ResolutionSuccess,
)
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyCatalogRepository,
    VocabularyConceptRecord,
    VocabularyIdentifierRecord,
    VocabularyImportRecord,
    VocabularyLabelRecord,
    VocabularyRecord,
    VocabularyReleaseConceptRecord,
    VocabularyReleaseRecord,
    VocabularyResolutionAttemptRecord,
    VocabularySourceRecord,
)

Completeness = Literal["complete", "partial"]
LabelOrigin = Literal[
    "provider_official",
    "authorized_secondary",
    "institutional_translation",
    "bibliographic_snapshot",
]
IdentifierScheme = Literal["marc", "iso639-1", "iso639-2-b", "iso639-2-t", "iso639-3"]


class VocabularyImportConflictError(RuntimeError):
    """The same immutable identity was reused with divergent content."""


class VocabularySourceInput(Contract):
    key: str = Field(min_length=1)
    provider_uri: IRI
    source_uri: IRI
    provenance: dict[str, object] = Field(default_factory=dict)


class VocabularyInput(Contract):
    uri: IRI
    kind: Literal["external", "local"] = "external"
    status: Literal["draft", "active", "suspended", "retired"] = "draft"


class VocabularyReleaseInput(Contract):
    release_key: str = Field(min_length=1)
    source_version: str | None = None
    source_uri: IRI
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    completeness: Completeness
    scope: str | None = None
    retrieved_at: AwareDatetime

    @model_validator(mode="after")
    def partial_scope(self) -> "VocabularyReleaseInput":
        if self.completeness == "partial" and not (self.scope and self.scope.strip()):
            raise ValueError("Release parcial exige escopo explícito.")
        return self


class ImportedConcept(Contract):
    concept: ControlledConcept | LocalConcept
    label_origin: LabelOrigin = "provider_official"
    labels: tuple["VocabularyLabelInput", ...] = ()
    identifiers: tuple["VocabularyIdentifierInput", ...] = ()

    @model_validator(mode="after")
    def unique_metadata(self) -> "ImportedConcept":
        identifiers = [(item.scheme, item.value) for item in self.identifiers]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Identificador duplicado no conceito importado.")
        labels = [(item.kind, item.language, item.value) for item in self.labels]
        if len(labels) != len(set(labels)):
            raise ValueError("Rótulo duplicado no conceito importado.")
        return self


class VocabularyLabelInput(Contract):
    kind: Literal["preferred", "alternative"]
    value: str = Field(min_length=1)
    language: str = ""
    origin: LabelOrigin
    source_uri: IRI

    @model_validator(mode="after")
    def normalize_language(self) -> "VocabularyLabelInput":
        normalized = self.language.lower()
        if normalized != self.language:
            return self.model_copy(update={"language": normalized})
        return self


class VocabularyIdentifierInput(Contract):
    scheme: IdentifierScheme
    value: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    source_uri: IRI


class VocabularyImport(Contract):
    idempotency_key: str = Field(min_length=1)
    release: VocabularyReleaseInput
    concepts: tuple[ImportedConcept, ...]

    @model_validator(mode="after")
    def exact_unique_uris(self) -> "VocabularyImport":
        uris = [entry.concept.uri for entry in self.concepts]
        if len(uris) != len(set(uris)):
            raise ValueError("Uma release não admite URI de conceito duplicada.")
        return self

    def payload_digest(self) -> str:
        canonical = self.model_dump_json(exclude={"idempotency_key"})
        return sha256(canonical.encode()).hexdigest()


class VocabularyChangeReport(Contract):
    baseline_release_id: UUID | None
    candidate_release_id: UUID
    added: tuple[str, ...]
    changed_labels: tuple[str, ...]
    changed_identifiers: tuple[str, ...]
    absent: tuple[str, ...]
    explicitly_obsolete: tuple[str, ...]
    absence_proves_removal: bool


class VocabularyCatalogService:
    """Own atomic registration/import/evidence transactions; never edits RDF snapshots."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: VocabularyCatalogRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.repository = repository or VocabularyCatalogRepository()

    async def register(
        self,
        source: VocabularySourceInput,
        vocabulary: VocabularyInput,
        *,
        now: datetime,
    ) -> VocabularyRecord:
        async with self.sessions() as session, session.begin():
            existing = await self.repository.vocabulary_by_uri(session, vocabulary.uri)
            if existing is not None:
                stored_source = await session.get(VocabularySourceRecord, existing.source_id)
                if (
                    stored_source is None
                    or stored_source.key != source.key
                    or stored_source.provider_uri != source.provider_uri
                    or stored_source.source_uri != source.source_uri
                    or existing.kind != vocabulary.kind
                ):
                    raise VocabularyImportConflictError(
                        "Identidade do vocabulário já existe com definição divergente."
                    )
                return existing
            stored_source = await session.scalar(
                select(VocabularySourceRecord).where(VocabularySourceRecord.key == source.key)
            )
            if stored_source is None:
                stored_source = VocabularySourceRecord(
                    id=uuid4(),
                    key=source.key,
                    provider_uri=source.provider_uri,
                    source_uri=source.source_uri,
                    provenance=source.provenance,
                    created_at=now,
                )
                session.add(stored_source)
                await session.flush()
            elif (
                stored_source.provider_uri != source.provider_uri
                or stored_source.source_uri != source.source_uri
                or stored_source.provenance != source.provenance
            ):
                raise VocabularyImportConflictError(
                    "Chave da fonte já existe com proveniência divergente."
                )
            result = VocabularyRecord(
                id=uuid4(),
                uri=vocabulary.uri,
                source_id=stored_source.id,
                kind=vocabulary.kind,
                status=vocabulary.status,
                created_at=now,
            )
            session.add(result)
            await session.flush()
            return result

    async def import_release(
        self, vocabulary_id: UUID, request: VocabularyImport, *, now: datetime
    ) -> VocabularyReleaseRecord:
        payload_digest = request.payload_digest()
        try:
            return await self._import_release(
                vocabulary_id, request, now=now, payload_digest=payload_digest
            )
        except IntegrityError as error:
            # A concurrent identical run may win either unique constraint. Re-read only
            # after rollback; divergent or incomplete runs remain explicit conflicts.
            async with self.sessions() as session:
                prior = await session.scalar(
                    select(VocabularyImportRecord).where(
                        VocabularyImportRecord.vocabulary_id == vocabulary_id,
                        VocabularyImportRecord.idempotency_key == request.idempotency_key,
                    )
                )
                if (
                    prior is not None
                    and prior.payload_digest == payload_digest
                    and prior.status == "completed"
                    and prior.release_id is not None
                ):
                    release = await session.get(VocabularyReleaseRecord, prior.release_id)
                    if release is not None:
                        return release
            raise VocabularyImportConflictError(
                "Importação concorrente divergiu ou não foi concluída."
            ) from error

    async def _import_release(
        self,
        vocabulary_id: UUID,
        request: VocabularyImport,
        *,
        now: datetime,
        payload_digest: str,
    ) -> VocabularyReleaseRecord:
        async with self.sessions() as session, session.begin():
            vocabulary = await session.get(VocabularyRecord, vocabulary_id)
            if vocabulary is None:
                raise LookupError("Vocabulário não encontrado.")
            prior = await session.scalar(
                select(VocabularyImportRecord).where(
                    VocabularyImportRecord.vocabulary_id == vocabulary_id,
                    VocabularyImportRecord.idempotency_key == request.idempotency_key,
                )
            )
            if prior is not None:
                if (
                    prior.payload_digest != payload_digest
                    or prior.status != "completed"
                    or prior.release_id is None
                ):
                    raise VocabularyImportConflictError(
                        "Chave de idempotência reutilizada com importação divergente/incompleta."
                    )
                release = await session.get(VocabularyReleaseRecord, prior.release_id)
                if release is None:
                    raise RuntimeError("Importação concluída sem release persistida.")
                return release
            existing_release = await self.repository.release_by_key(
                session, vocabulary_id, request.release.release_key
            )
            if existing_release is not None:
                if existing_release.digest != request.release.digest:
                    raise VocabularyImportConflictError(
                        "Release histórica não pode receber conteúdo divergente."
                    )
                raise VocabularyImportConflictError(
                    "Release existente exige a chave idempotente original."
                )
            run = VocabularyImportRecord(
                id=uuid4(),
                vocabulary_id=vocabulary_id,
                release_id=None,
                idempotency_key=request.idempotency_key,
                payload_digest=payload_digest,
                status="running",
                started_at=now,
                finished_at=None,
            )
            release = VocabularyReleaseRecord(
                id=uuid4(),
                vocabulary_id=vocabulary_id,
                release_key=request.release.release_key,
                source_version=request.release.source_version,
                source_uri=request.release.source_uri,
                digest=request.release.digest,
                completeness=request.release.completeness,
                scope=request.release.scope,
                status="candidate",
                retrieved_at=request.release.retrieved_at,
                created_at=now,
            )
            session.add_all((run, release))
            await session.flush()
            for entry in request.concepts:
                await self._store_concept(session, vocabulary, release, entry)
            run.release_id = release.id
            run.status = "completed"
            run.finished_at = now
            await session.flush()
            return release

    async def _store_concept(
        self,
        session: AsyncSession,
        vocabulary: VocabularyRecord,
        release: VocabularyReleaseRecord,
        imported: ImportedConcept,
    ) -> None:
        value = imported.concept
        if value.vocabulary != vocabulary.uri:
            raise VocabularyImportConflictError("Conceito pertence a outro vocabulário.")
        stored = await session.scalar(
            select(VocabularyConceptRecord).where(VocabularyConceptRecord.uri == value.uri)
        )
        internal_id = value.internal_id if isinstance(value, LocalConcept) else None
        kind = "local" if isinstance(value, LocalConcept) else "external"
        if stored is None:
            stored = VocabularyConceptRecord(
                id=uuid4(),
                vocabulary_id=vocabulary.id,
                uri=value.uri,
                kind=kind,
                internal_id=internal_id,
                created_at=release.created_at,
            )
            session.add(stored)
            await session.flush()
        elif (
            stored.vocabulary_id != vocabulary.id
            or stored.kind != kind
            or stored.internal_id != internal_id
        ):
            raise VocabularyImportConflictError("URI global do conceito possui outra identidade.")
        occurrence = VocabularyReleaseConceptRecord(
            id=uuid4(),
            release_id=release.id,
            concept_id=stored.id,
            vocabulary_id=vocabulary.id,
            concept_uri=value.uri,
            notation=value.notation,
            status=value.status,
            source_uri=value.source,
            replacement_uri=value.replacement,
        )
        session.add(occurrence)
        await session.flush()
        label_source = value.source or release.source_uri
        labels = list(imported.labels) or [
            VocabularyLabelInput(
                kind=kind_name,
                value=label.value,
                language=(label.language or "").lower(),
                origin=imported.label_origin,
                source_uri=label_source,
            )
            for kind_name, values in (
                ("preferred", value.label),
                ("alternative", value.alternative_labels),
            )
            for label in values
        ]
        session.add_all(
            [
                VocabularyLabelRecord(
                    id=uuid4(),
                    release_concept_id=occurrence.id,
                    kind=label.kind,
                    value=label.value,
                    language=label.language,
                    origin=label.origin,
                    source_uri=label.source_uri,
                )
                for label in labels
            ]
        )
        session.add_all(
            [
                VocabularyIdentifierRecord(
                    id=uuid4(),
                    release_concept_id=occurrence.id,
                    scheme=identifier.scheme,
                    value=identifier.value,
                    source_uri=identifier.source_uri,
                )
                for identifier in imported.identifiers
            ]
        )

    async def activate_release(
        self,
        vocabulary_id: UUID,
        release_id: UUID,
        *,
        expected_active_release_id: UUID | None,
    ) -> VocabularyReleaseRecord:
        """Atomically move the active pointer using the release state as the CAS value."""
        async with self.sessions() as session, session.begin():
            candidate = await session.get(VocabularyReleaseRecord, release_id)
            if candidate is None or candidate.vocabulary_id != vocabulary_id:
                raise LookupError("Release não encontrada no vocabulário.")
            if candidate.status not in {"candidate", "superseded", "active"}:
                raise VocabularyImportConflictError("Release não pode ser ativada neste estado.")
            active = await session.scalar(
                select(VocabularyReleaseRecord)
                .where(
                    VocabularyReleaseRecord.vocabulary_id == vocabulary_id,
                    VocabularyReleaseRecord.status == "active",
                )
                .with_for_update()
            )
            actual = active.id if active is not None else None
            if actual != expected_active_release_id:
                raise VocabularyImportConflictError("Release ativa mudou durante a ativação.")
            if active is not None and active.id != candidate.id:
                active.status = "superseded"
                await session.flush()
            candidate.status = "active"
            await session.flush()
            return candidate

    async def search_active(
        self, vocabulary_id: UUID, query: str, *, limit: int = 20
    ) -> tuple[tuple[str, str | None, str | None, str | None], ...]:
        """Search the local active release by exact URI/code or case-folded label."""
        needle = query.strip()
        if not needle or not 1 <= limit <= 100:
            return ()
        async with self.sessions() as session:
            statement = (
                select(
                    VocabularyReleaseConceptRecord.concept_uri,
                    VocabularyReleaseConceptRecord.notation,
                    VocabularyLabelRecord.value,
                    VocabularyLabelRecord.language,
                )
                .join(
                    VocabularyReleaseRecord,
                    VocabularyReleaseRecord.id == VocabularyReleaseConceptRecord.release_id,
                )
                .outerjoin(
                    VocabularyLabelRecord,
                    VocabularyLabelRecord.release_concept_id == VocabularyReleaseConceptRecord.id,
                )
                .outerjoin(
                    VocabularyIdentifierRecord,
                    VocabularyIdentifierRecord.release_concept_id
                    == VocabularyReleaseConceptRecord.id,
                )
                .where(
                    VocabularyReleaseRecord.vocabulary_id == vocabulary_id,
                    VocabularyReleaseRecord.status == "active",
                    or_(
                        VocabularyReleaseConceptRecord.concept_uri == needle,
                        VocabularyReleaseConceptRecord.notation == needle.lower(),
                        VocabularyIdentifierRecord.value == needle.lower(),
                        func.lower(VocabularyLabelRecord.value).contains(needle.lower()),
                    ),
                )
                .order_by(
                    VocabularyReleaseConceptRecord.concept_uri,
                    VocabularyLabelRecord.kind,
                    VocabularyLabelRecord.language,
                )
                .limit(limit)
            )
            result = await session.execute(statement)
            return tuple((row[0], row[1], row[2], row[3]) for row in result)

    async def compare_releases(
        self,
        candidate_release_id: UUID,
        baseline_release_id: UUID | None,
    ) -> VocabularyChangeReport:
        """Report logical change without mutating either immutable release."""
        async with self.sessions() as session:
            candidate = await session.get(VocabularyReleaseRecord, candidate_release_id)
            if candidate is None:
                raise LookupError("Release candidata não encontrada.")
            baseline = (
                await session.get(VocabularyReleaseRecord, baseline_release_id)
                if baseline_release_id is not None
                else None
            )
            if baseline is not None and baseline.vocabulary_id != candidate.vocabulary_id:
                raise ValueError("Releases pertencem a vocabulários diferentes.")

            async def snapshot(
                release_id: UUID | None,
            ) -> dict[str, tuple[str, frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]]]:
                if release_id is None:
                    return {}
                concepts = (
                    await session.execute(
                        select(
                            VocabularyReleaseConceptRecord.id,
                            VocabularyReleaseConceptRecord.concept_uri,
                            VocabularyReleaseConceptRecord.status,
                        ).where(VocabularyReleaseConceptRecord.release_id == release_id)
                    )
                ).all()
                result: dict[
                    str,
                    tuple[str, frozenset[tuple[str, ...]], frozenset[tuple[str, ...]]],
                ] = {}
                for occurrence_id, uri, status in concepts:
                    labels = frozenset(
                        tuple(row)
                        for row in (
                            await session.execute(
                                select(
                                    VocabularyLabelRecord.kind,
                                    VocabularyLabelRecord.language,
                                    VocabularyLabelRecord.value,
                                    VocabularyLabelRecord.origin,
                                    VocabularyLabelRecord.source_uri,
                                ).where(
                                    VocabularyLabelRecord.release_concept_id == occurrence_id
                                )
                            )
                        ).all()
                    )
                    identifiers = frozenset(
                        tuple(row)
                        for row in (
                            await session.execute(
                                select(
                                    VocabularyIdentifierRecord.scheme,
                                    VocabularyIdentifierRecord.value,
                                    VocabularyIdentifierRecord.source_uri,
                                ).where(
                                    VocabularyIdentifierRecord.release_concept_id == occurrence_id
                                )
                            )
                        ).all()
                    )
                    result[uri] = (status, labels, identifiers)
                return result

            old = await snapshot(baseline_release_id)
            new = await snapshot(candidate_release_id)
            shared = set(old) & set(new)
            return VocabularyChangeReport(
                baseline_release_id=baseline_release_id,
                candidate_release_id=candidate_release_id,
                added=tuple(sorted(set(new) - set(old))),
                changed_labels=tuple(sorted(uri for uri in shared if old[uri][1] != new[uri][1])),
                changed_identifiers=tuple(
                    sorted(uri for uri in shared if old[uri][2] != new[uri][2])
                ),
                absent=tuple(sorted(set(old) - set(new))),
                explicitly_obsolete=tuple(
                    sorted(
                        uri
                        for uri, value in new.items()
                        if value[0] == "deprecated"
                        and (uri not in old or old[uri][0] != "deprecated")
                    )
                ),
                absence_proves_removal=candidate.completeness == "complete",
            )

    async def record_resolution(
        self,
        vocabulary_id: UUID,
        result: ResolutionResult,
        *,
        configuration_version: str,
        context: str,
        policy: ResolutionCachePolicy,
        release_id: UUID | None = None,
    ) -> VocabularyResolutionAttemptRecord:
        provenance = result.provenance
        ttl = policy.ttl(result)
        expires_at = provenance.retrieved_at + timedelta(seconds=ttl)
        stale_until = (
            expires_at + timedelta(seconds=policy.stale_if_unavailable_seconds)
            if isinstance(result, ResolutionSuccess)
            else None
        )
        payload = (
            result.concept.model_dump(mode="json")
            if isinstance(result, ResolutionSuccess)
            else None
        )
        reason = result.reason if isinstance(result, ResolutionFailure) else None
        retry = result.retry_after_seconds if isinstance(result, ResolutionFailure) else None
        record = VocabularyResolutionAttemptRecord(
            id=uuid4(),
            vocabulary_id=vocabulary_id,
            release_id=release_id,
            concept_uri=result.uri,
            provider_uri=provenance.provider,
            source_uri=provenance.source,
            source_version=provenance.source_version,
            configuration_version=configuration_version,
            context=context,
            outcome=result.outcome,
            reason=reason,
            reliable=isinstance(result, ResolutionSuccess),
            payload=payload,
            retrieved_at=provenance.retrieved_at,
            expires_at=expires_at,
            stale_until=stale_until,
            retry_after_seconds=retry,
        )
        async with self.sessions() as session, session.begin():
            if await session.get(VocabularyRecord, vocabulary_id) is None:
                raise LookupError("Vocabulário não encontrado.")
            if release_id is not None:
                release = await session.get(VocabularyReleaseRecord, release_id)
                if release is None or release.vocabulary_id != vocabulary_id:
                    raise ValueError("Release não pertence ao vocabulário.")
            session.add(record)
            await session.flush()
            return record
