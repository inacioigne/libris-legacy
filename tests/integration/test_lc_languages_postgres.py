"""M08.03 LC Languages lifecycle against isolated PostgreSQL."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from libris.modules.bibliographic.application.lc_languages import (
    LC_LANGUAGES_VOCABULARY,
    LC_LANGUAGES_XML,
    LC_PROVIDER,
    parse_lc_languages,
)
from libris.modules.bibliographic.application.lc_languages_rdf import (
    parse_lc_languages_rdf,
)
from libris.modules.bibliographic.application.vocabulary_catalog import (
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyImportConflictError,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.infrastructure.vocabulary_catalog import (
    VocabularyIdentifierRecord,
    VocabularyReleaseRecord,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration
FIXTURES = Path(__file__).parents[1] / "fixtures/vocabularies"
NOW = datetime(2026, 10, 19, 18, tzinfo=UTC)


def test_lc_languages_import_activation_and_offline_search() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("Integração M08.03 exige PostgreSQL real via asyncpg.")
    asyncio.run(_exercise(url))


async def _exercise(url: str) -> None:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = VocabularyCatalogService(sessions)
    try:
        vocabulary = await service.register(
            VocabularySourceInput(
                key="lc-languages-integration",
                provider_uri=LC_PROVIDER,
                source_uri=LC_LANGUAGES_XML,
                provenance={"fixture": True},
            ),
            VocabularyInput(uri=LC_LANGUAGES_VOCABULARY, status="active"),
            now=NOW,
        )
        parsed = parse_lc_languages(
            (FIXTURES / "lc-languages-small.xml").read_bytes(),
            identifier_sidecar=FIXTURES / "lc-languages-identifiers.json",
            translation_sidecar=FIXTURES / "lc-languages-translations.json",
            complete=False,
        )
        request = VocabularyImport(
            idempotency_key=f"lc-small:{parsed.digest}",
            release=VocabularyReleaseInput(
                release_key="fixture-1",
                source_uri=LC_LANGUAGES_XML,
                digest=parsed.digest,
                provenance={
                    "format": "MARC Code List XML",
                    "concept_count": len(parsed.concepts),
                    "rights_status": "pending-institutional-review",
                },
                completeness="partial",
                scope="por,eng,spa test fixture",
                retrieved_at=NOW,
            ),
            concepts=parsed.concepts,
        )
        first = await service.import_release(vocabulary.id, request, now=NOW)
        assert (await service.import_release(vocabulary.id, request, now=NOW)).id == first.id
        initial_report = await service.compare_releases(first.id, None)
        assert len(initial_report.added) == 3 and not initial_report.absence_proves_removal
        active = await service.activate_release(
            vocabulary.id, first.id, expected_active_release_id=None
        )
        assert active.status == "active"
        for query, expected in (
            ("por", "/por"),
            ("pt", "/por"),
            ("Português", "/por"),
            ("eng", "/eng"),
            ("spa", "/spa"),
            ("http://id.loc.gov/vocabulary/languages/por", "/por"),
        ):
            rows = await service.search_active(vocabulary.id, query)
            assert rows and rows[0][0].endswith(expected)

        turtle = parse_lc_languages_rdf(
            (FIXTURES / "lc-languages-small.ttl").read_bytes(),
            identifier_sidecar=FIXTURES / "lc-languages-identifiers.json",
            translation_sidecar=FIXTURES / "lc-languages-translations.json",
            expected_concepts=3,
        )
        second_request = VocabularyImport(
            idempotency_key=f"lc-turtle:{turtle.digest}",
            release=VocabularyReleaseInput(
                release_key="fixture-2",
                source_uri=turtle.source_uri,
                digest=turtle.digest,
                provenance={
                    "format": "SKOS/RDF Turtle",
                    "concept_count": len(turtle.concepts),
                    "rights_status": "pending-institutional-review",
                },
                completeness="complete",
                retrieved_at=NOW + timedelta(minutes=1),
            ),
            concepts=turtle.concepts,
        )
        second = await service.import_release(
            vocabulary.id, second_request, now=NOW + timedelta(minutes=1)
        )
        assert (
            await service.import_release(
                vocabulary.id, second_request, now=NOW + timedelta(minutes=1)
            )
        ).id == second.id
        assert second.source_uri == turtle.source_uri
        assert second.provenance["format"] == "SKOS/RDF Turtle"
        assert second.provenance["concept_count"] == 3
        unchanged = await service.compare_releases(second.id, first.id)
        assert unchanged.added == unchanged.absent == ()
        assert unchanged.changed_labels == tuple(
            sorted(item.concept.uri for item in turtle.concepts)
        )
        assert unchanged.changed_identifiers == ()
        with pytest.raises(VocabularyImportConflictError, match="mudou"):
            await service.activate_release(
                vocabulary.id, second.id, expected_active_release_id=None
            )
        activated = await service.activate_release(
            vocabulary.id, second.id, expected_active_release_id=first.id
        )
        assert activated.status == "active"
        async with sessions() as session:
            status_rows = (
                await session.execute(
                    select(
                        VocabularyReleaseRecord.release_key, VocabularyReleaseRecord.status
                    ).where(VocabularyReleaseRecord.vocabulary_id == vocabulary.id)
                )
            ).all()
            statuses: dict[str, str] = {
                release_key: status for release_key, status in status_rows
            }
            assert statuses == {"fixture-1": "superseded", "fixture-2": "active"}
            assert (
                await session.scalar(select(func.count()).select_from(VocabularyIdentifierRecord))
                == 30
            )
    finally:
        await engine.dispose()
