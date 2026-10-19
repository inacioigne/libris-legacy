"""Offline read use cases for approved operational vocabulary releases."""

from __future__ import annotations

import unicodedata
from typing import cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libris.modules.bibliographic.api.schemas.vocabularies import (
    ConceptDetail,
    ConceptLabel,
    ConceptProvenance,
    ConceptSearchItem,
    ConceptSearchPage,
    ConceptValidation,
    Pagination,
    ValidateConceptRequest,
    VocabularyIdentity,
    VocabularyPage,
    VocabularyReleaseSummary,
    VocabularySummary,
)
from libris.modules.bibliographic.application.controlled_values import ReferenceStatus
from libris.modules.bibliographic.application.vocabulary_catalog import Completeness
from libris.modules.bibliographic.application.vocabulary_validation import (
    reference_status_diagnostics,
    vocabulary_membership_diagnostics,
)
from libris.modules.bibliographic.domain.errors import ResourceNotFoundError
from libris.modules.bibliographic.domain.vocabularies import membership_from_evidence
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyCatalogRepository,
    VocabularyConceptDetailRow,
    VocabularyConceptSearchRow,
    VocabularyListingRow,
)


def vocabulary_name(source_key: str, provenance: dict[str, object]) -> str:
    name = provenance.get("name")
    return name if isinstance(name, str) and name.strip() else source_key


def completeness(value: str) -> Completeness:
    if value not in {"complete", "partial"}:
        raise RuntimeError("Persisted vocabulary release has an invalid completeness value.")
    return cast(Completeness, value)


def reference_status(value: str) -> ReferenceStatus:
    if value not in {"resolved", "unverified", "invalid", "deprecated", "unavailable"}:
        raise RuntimeError("Persisted vocabulary concept has an invalid status.")
    return cast(ReferenceStatus, value)


def release_summary(row: VocabularyListingRow) -> VocabularyReleaseSummary:
    release = row.release
    return VocabularyReleaseSummary(
        id=release.id,
        key=release.release_key,
        source_version=release.source_version,
        retrieved_at=release.retrieved_at,
        completeness=completeness(release.completeness),
        scope=release.scope,
    )


def concept_provenance(
    row: VocabularyConceptDetailRow | VocabularyConceptSearchRow,
    *,
    label_origin: str | None = None,
    label_source_uri: str | None = None,
) -> ConceptProvenance:
    occurrence = row.occurrence
    release = row.release
    return ConceptProvenance(
        vocabulary_source=row.source.key,
        provider_uri=row.source.provider_uri,
        source_uri=occurrence.source_uri or release.source_uri,
        release_id=release.id,
        release_key=release.release_key,
        source_version=release.source_version,
        retrieved_at=release.retrieved_at,
        completeness=completeness(release.completeness),
        label_origin=label_origin,
        label_source_uri=label_source_uri,
    )


def vocabulary_identity(
    vocabulary_id: UUID,
    vocabulary_uri: str,
    source_key: str,
    source_provenance: dict[str, object],
) -> VocabularyIdentity:
    return VocabularyIdentity(
        id=vocabulary_id,
        uri=vocabulary_uri,
        name=vocabulary_name(source_key, source_provenance),
    )


def search_item(row: VocabularyConceptSearchRow) -> ConceptSearchItem:
    return ConceptSearchItem(
        concept_id=row.concept.id,
        uri=row.concept.uri,
        label=row.label,
        language=row.label_language,
        notation=row.occurrence.notation,
        vocabulary=vocabulary_identity(
            row.vocabulary.id,
            row.vocabulary.uri,
            row.source.key,
            row.source.provenance,
        ),
        status=row.occurrence.status,
        release_id=row.release.id,
        release_key=row.release.release_key,
        provenance=concept_provenance(
            row,
            label_origin=row.label_origin,
            label_source_uri=row.label_source_uri,
        ),
    )


def _normalized_search_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(unicodedata.normalize("NFC", value).split())


def _locale_fallback(locale: str) -> tuple[str, ...]:
    normalized = locale.lower()
    values = [normalized]
    base = normalized.split("-", maxsplit=1)[0]
    if base not in values:
        values.append(base)
    if "en" not in values:
        values.append("en")
    return tuple(values)


class VocabularyQueryService:
    """Read-only operations over immutable, locally persisted releases."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        repository: VocabularyCatalogRepository | None = None,
    ) -> None:
        self.sessions = sessions
        self.repository = repository or VocabularyCatalogRepository()

    async def list_available(self) -> VocabularyPage:
        async with self.sessions() as session:
            rows = await self.repository.available_vocabularies(session)
        return VocabularyPage(
            items=[
                VocabularySummary(
                    id=row.vocabulary.id,
                    uri=row.vocabulary.uri,
                    name=vocabulary_name(row.source.key, row.source.provenance),
                    description=(
                        row.source.provenance.get("description")
                        if isinstance(row.source.provenance.get("description"), str)
                        else None
                    ),
                    source_uri=row.source.source_uri,
                    provider_uri=row.source.provider_uri,
                    release=release_summary(row),
                )
                for row in rows
            ],
            total=len(rows),
        )

    async def search(
        self,
        vocabulary_id: UUID,
        *,
        q: str | None,
        uri: str | None,
        locale: str,
        label_language: str | None,
        status: str | None,
        release_id: UUID | None,
        limit: int,
        offset: int,
    ) -> ConceptSearchPage:
        normalized_query = _normalized_search_text(q)
        async with self.sessions() as session:
            vocabulary = await self.repository.vocabulary_by_id(session, vocabulary_id)
            if vocabulary is None:
                raise ResourceNotFoundError("Vocabulário não encontrado.")
            release = await self.repository.query_release(session, vocabulary_id, release_id)
            if release_id is not None and release is None:
                raise ResourceNotFoundError("Release não encontrada no vocabulário.")
            if release is None:
                total = 0
                rows: tuple[VocabularyConceptSearchRow, ...] = ()
            else:
                total, rows = await self.repository.search_concepts(
                    session,
                    vocabulary_id=vocabulary_id,
                    release_id=release.id,
                    query=normalized_query,
                    uri=uri,
                    status=status,
                    label_language=label_language.lower() if label_language else None,
                    locale_fallback=_locale_fallback(locale),
                    limit=limit,
                    offset=offset,
                )
        return ConceptSearchPage(
            items=[search_item(row) for row in rows],
            pagination=Pagination(limit=limit, offset=offset, total=total),
        )

    async def detail(
        self,
        vocabulary_id: UUID,
        concept_id: UUID,
        *,
        locale: str,
        release_id: UUID | None,
    ) -> ConceptDetail:
        async with self.sessions() as session:
            vocabulary = await self.repository.vocabulary_by_id(session, vocabulary_id)
            if vocabulary is None:
                raise ResourceNotFoundError("Vocabulário não encontrado.")
            release = await self.repository.query_release(session, vocabulary_id, release_id)
            if release is None:
                raise ResourceNotFoundError("Release ativa não encontrada.")
            if release_id is not None and release.id != release_id:
                raise ResourceNotFoundError("Release não encontrada no vocabulário.")
            row: VocabularyConceptDetailRow | None = await self.repository.concept_detail(
                session,
                vocabulary_id=vocabulary_id,
                release_id=release.id,
                concept_id=concept_id,
            )
            if row is None:
                raise ResourceNotFoundError("Conceito não encontrado na release.")
            labels = await self.repository.concept_labels(session, row.occurrence.id)

        ordered_labels = [
            ConceptLabel(
                kind=label.kind,
                value=label.value,
                language=label.language,
                origin=label.origin,
                source_uri=label.source_uri,
            )
            for label in labels
        ]
        priorities = _locale_fallback(locale)
        selected_labels = sorted(
            labels,
            key=lambda label: (
                0 if label.kind == "preferred" else 1,
                priorities.index(label.language)
                if label.language in priorities
                else len(priorities),
                label.language,
                label.value,
            ),
        )
        selected = selected_labels[0] if selected_labels else None
        base = search_item(
            VocabularyConceptSearchRow(
                row.concept,
                row.occurrence,
                row.release,
                row.vocabulary,
                row.source,
                selected.value if selected is not None else None,
                selected.language if selected is not None else None,
                selected.origin if selected is not None else None,
                selected.source_uri if selected is not None else None,
            )
        )
        return ConceptDetail(
            **base.model_dump(),
            labels=ordered_labels,
            replacement_uri=row.occurrence.replacement_uri,
            deprecated=row.occurrence.status == "deprecated",
        )

    async def validate(
        self,
        vocabulary_id: UUID,
        payload: ValidateConceptRequest,
        *,
        release_id: UUID | None,
    ) -> ConceptValidation:
        async with self.sessions() as session:
            vocabulary = await self.repository.vocabulary_by_id(session, vocabulary_id)
            if vocabulary is None:
                raise ResourceNotFoundError("Vocabulário não encontrado.")
            source = await self.repository.active_vocabulary_source(session, vocabulary_id)
            if source is None:
                raise ResourceNotFoundError("Fonte aprovada não encontrada.")
            release = await self.repository.query_release(session, vocabulary_id, release_id)
            if release_id is not None and release is None:
                raise ResourceNotFoundError("Release não encontrada no vocabulário.")
            row = (
                await self.repository.concept_by_uri(
                    session,
                    vocabulary_id=vocabulary_id,
                    release_id=release.id,
                    uri=payload.uri,
                )
                if release is not None
                else None
            )

        identity = vocabulary_identity(vocabulary.id, vocabulary.uri, source.key, source.provenance)
        if release is None:
            return ConceptValidation(
                uri=payload.uri,
                vocabulary=identity,
                belongs=None,
                status="unavailable",
                valid=None,
                release_id=None,
                release_key=None,
                provenance=None,
                replacement_uri=None,
                diagnostics=reference_status_diagnostics("unavailable"),
            )
        if row is None:
            complete = release.completeness == "complete"
            belongs = membership_from_evidence(present=False, complete=complete)
            status = "invalid" if belongs is False else "unverified"
            return ConceptValidation(
                uri=payload.uri,
                vocabulary=identity,
                belongs=belongs,
                status=status,
                valid=False if belongs is False else None,
                release_id=release.id,
                release_key=release.release_key,
                provenance=ConceptProvenance(
                    vocabulary_source=source.key,
                    provider_uri=source.provider_uri,
                    source_uri=release.source_uri,
                    release_id=release.id,
                    release_key=release.release_key,
                    source_version=release.source_version,
                    retrieved_at=release.retrieved_at,
                    completeness=completeness(release.completeness),
                ),
                replacement_uri=None,
                diagnostics=vocabulary_membership_diagnostics(belongs),
            )

        status = reference_status(row.occurrence.status)
        belongs = (
            True if status in {"resolved", "deprecated"} else False if status == "invalid" else None
        )
        valid = (
            True
            if status == "resolved"
            else False
            if status in {"invalid", "deprecated"}
            else None
        )
        provenance = concept_provenance(row)
        return ConceptValidation(
            uri=payload.uri,
            vocabulary=identity,
            belongs=belongs,
            status=status,
            valid=valid,
            release_id=release.id,
            release_key=release.release_key,
            provenance=provenance,
            replacement_uri=row.occurrence.replacement_uri,
            diagnostics=reference_status_diagnostics(status),
        )
