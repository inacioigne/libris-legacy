"""M08.02 operational vocabulary persistence against isolated PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyImportConflictError,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.application.vocabulary_resolution import (
    ResolutionCachePolicy,
    ResolutionFailure,
    ResolutionProvenance,
    ResolutionSuccess,
)
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyCatalogRepository,
    VocabularyConceptRecord,
    VocabularyImportRecord,
    VocabularyLabelRecord,
    VocabularyReleaseConceptRecord,
    VocabularyReleaseRecord,
    VocabularyResolutionAttemptRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
VOCABULARY = "urn:test:m0802:vocabulary"
PROVIDER = "urn:test:m0802:provider"
SOURCE = "urn:test:m0802:source"
URI = "urn:test:m0802:concept:one"


def concept(
    uri: str = URI,
    *,
    vocabulary: str = VOCABULARY,
    label: str = "Conceito",
    notation: str = "same-notation",
) -> ImportedConcept:
    return ImportedConcept.model_validate(
        {
            "concept": {
                "kind": "controlled_concept",
                "uri": uri,
                "vocabulary": vocabulary,
                "label": [
                    {"value": label, "language": "pt"},
                    {"value": "Concept", "language": "en"},
                ],
                "alternative_labels": [{"value": "Alternativo", "language": "pt"}],
                "notation": notation,
                "source": SOURCE,
                "status": "resolved",
            },
            "label_origin": "provider_official",
        }
    )


def request(
    key: str,
    release_key: str,
    digest_character: str,
    concepts: tuple[ImportedConcept, ...],
    *,
    completeness: str = "complete",
) -> VocabularyImport:
    return VocabularyImport(
        idempotency_key=key,
        release=VocabularyReleaseInput(
            release_key=release_key,
            source_version=release_key,
            source_uri=SOURCE,
            digest=digest_character * 64,
            completeness=completeness,
            scope="fixture subset" if completeness == "partial" else None,
            retrieved_at=NOW,
        ),
        concepts=concepts,
    )


def test_idempotent_atomic_import_and_resolution_evidence() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração M08.02 exige PostgreSQL real via asyncpg.")
    asyncio.run(exercise(url))


async def exercise(url: str) -> None:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = VocabularyCatalogService(sessions)
    repository = VocabularyCatalogRepository()
    try:
        vocabulary = await service.register(
            VocabularySourceInput(
                key="m0802-fixture", provider_uri=PROVIDER, source_uri=SOURCE,
                provenance={"fixture": True},
            ),
            VocabularyInput(uri=VOCABULARY, status="active"),
            now=NOW,
        )
        first_request = request(
            "import-1",
            "release-1",
            "a",
            (
                concept(),
                concept("urn:test:m0802:concept:two", label="Conceito", notation="same-notation"),
            ),
        )
        first = await service.import_release(vocabulary.id, first_request, now=NOW)
        repeated = await service.import_release(vocabulary.id, first_request, now=NOW)
        assert repeated.id == first.id
        with pytest.raises(VocabularyImportConflictError, match="idempotência"):
            await service.import_release(
                vocabulary.id,
                request("import-1", "release-other", "b", (concept(),)),
                now=NOW,
            )
        second = await service.import_release(
            vocabulary.id,
            request("import-2", "release-2", "b", (concept(),), completeness="partial"),
            now=NOW + timedelta(minutes=1),
        )
        assert first.completeness == "complete" and second.completeness == "partial"
        concurrent_request = request(
            "import-concurrent",
            "release-concurrent",
            "d",
            (concept("urn:test:m0802:concept:concurrent"),),
        )
        concurrent = await asyncio.gather(
            service.import_release(vocabulary.id, concurrent_request, now=NOW),
            service.import_release(vocabulary.id, concurrent_request, now=NOW),
        )
        assert concurrent[0].id == concurrent[1].id

        # A late failure proves transaction rollback: no release/import/occurrence survives.
        broken = request(
            "import-broken",
            "release-broken",
            "c",
            (concept("urn:test:m0802:concept:three"), concept(vocabulary="urn:wrong")),
        )
        with pytest.raises(VocabularyImportConflictError, match="outro vocabulário"):
            await service.import_release(vocabulary.id, broken, now=NOW)

        provenance = ResolutionProvenance(
            provider=PROVIDER, source=SOURCE, retrieved_at=NOW, source_version="release-1"
        )
        success = ResolutionSuccess(
            outcome="success", uri=URI, provenance=provenance, concept=concept().concept
        )
        policy = ResolutionCachePolicy()
        reliable = await service.record_resolution(
            vocabulary.id,
            success,
            configuration_version="config-1",
            context="catalog-validation",
            policy=policy,
            release_id=first.id,
        )
        failure = ResolutionFailure(
            outcome="temporary_failure",
            uri=URI,
            provenance=provenance.model_copy(update={"retrieved_at": NOW + timedelta(hours=1)}),
            reason="unavailable",
            retry_after_seconds=15,
        )
        await service.record_resolution(
            vocabulary.id,
            failure,
            configuration_version="config-1",
            context="catalog-validation",
            policy=policy,
            release_id=first.id,
        )
        permanent = ResolutionFailure(
            outcome="permanent_failure",
            uri=URI,
            provenance=provenance.model_copy(
                update={"retrieved_at": NOW + timedelta(hours=2)}
            ),
            reason="not_found",
        )
        latest = await service.record_resolution(
            vocabulary.id,
            permanent,
            configuration_version="config-1",
            context="catalog-validation",
            policy=policy,
            release_id=first.id,
        )
        async with sessions() as session:
            stored_latest = await repository.latest_attempt(
                session,
                uri=URI,
                provider_uri=PROVIDER,
                configuration_version="config-1",
                context="catalog-validation",
            )
            assert stored_latest is not None and stored_latest.id == latest.id
            stored_reliable = await repository.latest_reliable_evidence(
                session,
                uri=URI,
                provider_uri=PROVIDER,
                configuration_version="config-1",
                context="catalog-validation",
            )
            assert stored_reliable is not None and stored_reliable.id == reliable.id
            assert await session.scalar(
                select(func.count()).select_from(VocabularyConceptRecord)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(VocabularyReleaseRecord)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(VocabularyImportRecord)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(VocabularyReleaseConceptRecord)
            ) == 4
            assert await session.scalar(
                select(func.count()).select_from(VocabularyLabelRecord)
            ) == 12
            assert await session.scalar(
                select(func.count()).select_from(VocabularyResolutionAttemptRecord)
            ) == 3
            assert not await session.scalar(
                select(VocabularyReleaseRecord.id).where(
                    VocabularyReleaseRecord.release_key == "release-broken"
                )
            )
    finally:
        await engine.dispose()
