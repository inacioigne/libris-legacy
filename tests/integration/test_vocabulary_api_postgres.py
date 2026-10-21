"""Real PostgreSQL coverage for vocabulary API reads, semantics and pagination."""

import asyncio
import hashlib
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.api.dependencies import get_session_factory
from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyCatalogService,
    VocabularyImport,
    VocabularyInput,
    VocabularyReleaseInput,
    VocabularySourceInput,
)
from libris.modules.bibliographic.application.vocabulary_resolution import (
    ResolutionCachePolicy,
    ResolutionFailure,
    ResolutionProvenance,
)
from libris.modules.identity.api.dependencies import (
    get_current_actor,
    get_identity_service,
)
from libris.modules.identity.domain.models import AuthenticatedActor, actor_identifier
from libris.modules.identity.domain.permissions import Permission
from libris.modules.security_audit.infrastructure.logging import LoggingSecuritySink
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def concept(
    *,
    uri: str,
    vocabulary: str,
    source: str,
    label_pt: str,
    label_en: str,
    status: str = "resolved",
    replacement: str | None = None,
) -> ImportedConcept:
    return ImportedConcept.model_validate(
        {
            "concept": {
                "kind": "controlled_concept",
                "uri": uri,
                "vocabulary": vocabulary,
                "label": [
                    {"value": label_pt, "language": "pt"},
                    {"value": label_en, "language": "en"},
                ],
                "alternative_labels": [
                    {"value": f"Alternativo {label_pt}", "language": "pt"}
                ],
                "notation": uri.rsplit("/", maxsplit=1)[-1],
                "source": source,
                "status": status,
                "replacement": replacement,
            },
            "label_origin": "provider_official",
        }
    )


def actor() -> AuthenticatedActor:
    issuer, subject = "https://issuer.example.invalid", "vocabulary-api-reader"
    return AuthenticatedActor(
        actor_identifier(issuer, subject),
        issuer,
        subject,
        frozenset(),
        frozenset({Permission.BIBLIOGRAPHIC_READ, Permission.METADATA_REVISION_READ}),
    )


def test_vocabulary_routes_query_postgresql_without_n_plus_one() -> None:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL descartável migrado.")
    if not url.startswith("postgresql+asyncpg://"):
        pytest.fail("A API M08.04 exige PostgreSQL real via asyncpg.")
    asyncio.run(exercise(url))


async def exercise(url: str) -> None:
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = VocabularyCatalogService(sessions)
    suffix = str(uuid4())
    vocabulary_uri = f"urn:test:vocabulary-api:{suffix}"
    source_uri = f"https://source.example.invalid/snapshot/{suffix}"
    provider_uri = f"https://provider.example.invalid/{suffix}"
    vocabulary = await service.register(
        VocabularySourceInput(
            key=f"vocabulary-api-{suffix}",
            provider_uri=provider_uri,
            source_uri=source_uri,
            provenance={"name": "Fixture controlled values", "description": "API test snapshot"},
        ),
        VocabularyInput(uri=vocabulary_uri, status="active"),
        now=NOW,
    )
    portuguese_uri = f"https://example.invalid/concepts/{suffix}/por"
    english_uri = f"https://example.invalid/concepts/{suffix}/eng"
    spanish_uri = f"https://example.invalid/concepts/{suffix}/spa"
    deprecated_uri = f"https://example.invalid/concepts/{suffix}/old"
    replacement_uri = f"https://example.invalid/concepts/{suffix}/new"
    release_key = f"api-test-{suffix}"
    request = VocabularyImport(
        idempotency_key=f"api-test-import-{suffix}",
        release=VocabularyReleaseInput(
            release_key=release_key,
            source_uri=source_uri,
            digest=hashlib.sha256(suffix.encode()).hexdigest(),
            completeness="complete",
            retrieved_at=NOW,
        ),
        concepts=(
            concept(
                uri=portuguese_uri,
                vocabulary=vocabulary_uri,
                source=source_uri,
                label_pt="Português",
                label_en="Portuguese",
            ),
            concept(
                uri=english_uri,
                vocabulary=vocabulary_uri,
                source=source_uri,
                label_pt="Portugue\u0302s",
                label_en="English",
            ),
            concept(
                uri=spanish_uri,
                vocabulary=vocabulary_uri,
                source=source_uri,
                label_pt="Ze\u0302bra",
                label_en="Spanish",
            ),
            concept(
                uri=deprecated_uri,
                vocabulary=vocabulary_uri,
                source=source_uri,
                label_pt="Rótulo antigo",
                label_en="Old label",
                status="deprecated",
                replacement=replacement_uri,
            ),
            *(
                concept(
                    uri=f"https://example.invalid/concepts/{suffix}/bulk-{index:03d}",
                    vocabulary=vocabulary_uri,
                    source=source_uri,
                    label_pt=f"Conceito em lote {index:03d}",
                    label_en=f"Bulk concept {index:03d}",
                )
                for index in range(100)
            ),
        ),
    )
    release = await service.import_release(vocabulary.id, request, now=NOW)
    await service.activate_release(vocabulary.id, release.id, expected_active_release_id=None)
    partial_uri = f"urn:test:vocabulary-api-partial:{suffix}"
    partial_source_uri = f"https://source.example.invalid/partial/{suffix}"
    partial_vocabulary = await service.register(
        VocabularySourceInput(
            key=f"vocabulary-api-partial-{suffix}",
            provider_uri=provider_uri,
            source_uri=partial_source_uri,
            provenance={"name": "Partial fixture"},
        ),
        VocabularyInput(uri=partial_uri, status="active"),
        now=NOW,
    )
    partial_release = await service.import_release(
        partial_vocabulary.id,
        VocabularyImport(
            idempotency_key=f"api-test-partial-import-{suffix}",
            release=VocabularyReleaseInput(
                release_key=f"partial-{suffix}",
                source_uri=partial_source_uri,
                digest=hashlib.sha256(f"partial:{suffix}".encode()).hexdigest(),
                completeness="partial",
                scope="fixture subset with no queried URI",
                retrieved_at=NOW,
            ),
            concepts=(),
        ),
        now=NOW,
    )
    await service.activate_release(
        partial_vocabulary.id,
        partial_release.id,
        expected_active_release_id=None,
    )
    unavailable_uri = f"urn:test:vocabulary-api-unavailable:{suffix}"
    unavailable_vocabulary = await service.register(
        VocabularySourceInput(
            key=f"vocabulary-api-unavailable-{suffix}",
            provider_uri=provider_uri,
            source_uri=f"https://source.example.invalid/unavailable/{suffix}",
        ),
        VocabularyInput(uri=unavailable_uri, status="active"),
        now=NOW,
    )

    await service.record_resolution(
        vocabulary.id,
        ResolutionFailure(
            outcome="temporary_failure",
            uri=portuguese_uri,
            provenance=ResolutionProvenance(
                provider=provider_uri,
                source=source_uri,
                retrieved_at=NOW,
            ),
            reason="unavailable",
            retry_after_seconds=60,
        ),
        configuration_version="test-configuration",
        context="catalog-validation",
        policy=ResolutionCachePolicy(),
        release_id=release.id,
    )

    app = create_app(Settings(_env_file=None, app_env="test", oidc_enabled=False))
    app.dependency_overrides[get_current_actor] = actor
    app.dependency_overrides[get_identity_service] = lambda: SimpleNamespace(
        observer=LoggingSecuritySink()
    )
    app.dependency_overrides[get_session_factory] = lambda: sessions
    statement_count = 0

    def count_statement(*_: object) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://libris.test",
        ) as client:
            listed = await client.get("/api/v1/vocabularies")
            assert listed.status_code == 200
            assert listed.headers["cache-control"] == "private, no-cache, must-revalidate"
            assert listed.headers["etag"]
            assert any(item["id"] == str(vocabulary.id) for item in listed.json()["items"])
            cached = await client.get(
                "/api/v1/vocabularies",
                headers={"If-None-Match": listed.headers["etag"]},
            )
            assert cached.status_code == 304

            base = f"/api/v1/vocabularies/{vocabulary.id}/concepts"
            queries_before = statement_count
            normalized = await client.get(
                base,
                params={"q": "  Portugue\u0302s  ", "locale": "pt-BR", "limit": 20},
            )
            assert normalized.status_code == 200
            selected_portuguese = next(
                item
                for item in normalized.json()["items"]
                if item["uri"] == portuguese_uri
            )
            assert selected_portuguese["label"] == "Português"
            assert selected_portuguese["language"] == "pt"
            assert statement_count - queries_before <= 4
            unicode_equivalent = await client.get(base, params={"q": "Português"})
            assert {item["uri"] for item in unicode_equivalent.json()["items"]} == {
                portuguese_uri,
                english_uri,
            }
            accent_sensitive = await client.get(base, params={"q": "Zêbra"})
            assert accent_sensitive.json()["items"][0]["uri"] == spanish_uri
            without_accent = await client.get(base, params={"q": "Zebra"})
            assert without_accent.json()["pagination"]["total"] == 0

            notation = await client.get(base, params={"q": "POR"})
            assert notation.status_code == 200
            assert portuguese_uri in {item["uri"] for item in notation.json()["items"]}
            for notation_value, expected_uri in (("eng", english_uri), ("spa", spanish_uri)):
                code = await client.get(base, params={"q": notation_value})
                assert code.json()["items"][0]["uri"] == expected_uri
            exact_uri = await client.get(base, params={"uri": portuguese_uri})
            assert [item["uri"] for item in exact_uri.json()["items"]] == [portuguese_uri]
            localized = await client.get(base, params={"locale": "en", "label_language": "en"})
            assert all(item["language"] == "en" for item in localized.json()["items"])
            deprecated = await client.get(base, params={"status": "deprecated"})
            assert [item["uri"] for item in deprecated.json()["items"]] == [deprecated_uri]
            release_filter = await client.get(base, params={"release_id": str(release.id)})
            assert release_filter.json()["pagination"]["total"] == 104
            absent = await client.get(base, params={"q": "no matching label"})
            assert absent.json()["pagination"]["total"] == 0

            queries_before = statement_count
            first_page = await client.get(base, params={"limit": 100, "offset": 0})
            second_page = await client.get(base, params={"limit": 100, "offset": 100})
            first_uris = [item["uri"] for item in first_page.json()["items"]]
            second_uris = [item["uri"] for item in second_page.json()["items"]]
            assert first_page.json()["order"] == "uri:asc,concept_id:asc"
            assert first_uris == sorted(first_uris)
            assert len(first_uris) == 100 and len(second_uris) == 4
            assert len(set(first_uris + second_uris)) == 104
            assert statement_count - queries_before <= 8

            concept_id = next(
                item["concept_id"]
                for item in normalized.json()["items"]
                if item["uri"] == portuguese_uri
            )
            detail = await client.get(
                f"{base}/{concept_id}",
                params={"locale": "pt", "release_id": str(release.id)},
            )
            assert detail.status_code == 200
            assert detail.json()["uri"] == portuguese_uri
            assert any(label["value"] == "Português" for label in detail.json()["labels"])
            assert detail.json()["provenance"]["release_key"] == release_key

            validation_path = f"/api/v1/vocabularies/{vocabulary.id}/validate"
            valid = await client.post(validation_path, json={"uri": portuguese_uri})
            assert valid.json()["status"] == "resolved"
            assert valid.json()["belongs"] is True and valid.json()["valid"] is True
            assert valid.json()["provenance"]["source_uri"] == source_uri
            assert valid.json()["diagnostics"] == []
            deprecation = await client.post(validation_path, json={"uri": deprecated_uri})
            assert deprecation.json()["status"] == "deprecated"
            assert deprecation.json()["replacement_uri"] == replacement_uri
            assert deprecation.json()["diagnostics"][0]["code"] == "reference.deprecated"
            missing_uri = f"https://example.invalid/concepts/{suffix}/absent"
            invalid = await client.post(validation_path, json={"uri": missing_uri})
            assert invalid.json()["status"] == "invalid"
            assert invalid.json()["belongs"] is False
            assert invalid.json()["diagnostics"][0]["code"] == "reference.vocabulary_mismatch"
            preserved = await client.post(validation_path, json={"uri": portuguese_uri})
            assert preserved.json()["status"] == "resolved"

            partial_result = await client.post(
                f"/api/v1/vocabularies/{partial_vocabulary.id}/validate",
                json={"uri": missing_uri},
            )
            assert partial_result.json()["status"] == "unverified"
            assert partial_result.json()["belongs"] is None
            assert partial_result.json()["diagnostics"][0]["code"] == (
                "reference.vocabulary_unverified"
            )
            unavailable = await client.post(
                f"/api/v1/vocabularies/{unavailable_vocabulary.id}/validate",
                json={"uri": missing_uri},
            )
            assert unavailable.json()["status"] == "unavailable"
            assert unavailable.json()["belongs"] is None
            assert unavailable.json()["diagnostics"][1]["code"] == (
                "reference.resolver_unavailable"
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)
        app.dependency_overrides.clear()
        await engine.dispose()
