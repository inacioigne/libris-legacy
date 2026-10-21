"""M08.02 catalog contracts remain deterministic and independent of PostgreSQL."""

from datetime import UTC, datetime

import pytest
from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyImport,
    VocabularyReleaseInput,
)
from pydantic import ValidationError

NOW = datetime(2026, 10, 19, tzinfo=UTC)
VOCABULARY = "http://id.loc.gov/vocabulary/languages/"


def release(**updates: object) -> VocabularyReleaseInput:
    return VocabularyReleaseInput.model_validate(
        {
            "release_key": "lc-languages-2026",
            "source_version": "2026",
            "source_uri": "https://id.loc.gov/vocabulary/languages.jsonld",
            "digest": "a" * 64,
            "completeness": "complete",
            "retrieved_at": NOW,
            **updates,
        }
    )


def imported(uri: str = "http://id.loc.gov/vocabulary/languages/por") -> ImportedConcept:
    return ImportedConcept.model_validate(
        {
            "concept": {
                "kind": "controlled_concept",
                "uri": uri,
                "vocabulary": VOCABULARY,
                "label": [
                    {"value": "Português", "language": "pt"},
                    {"value": "Portuguese", "language": "en"},
                ],
                "alternative_labels": [
                    {"value": "Língua portuguesa", "language": "pt"}
                ],
                "notation": "por",
                "source": "https://id.loc.gov/vocabulary/languages/por",
                "status": "resolved",
            }
        }
    )


def test_release_completeness_is_explicit() -> None:
    assert release().completeness == "complete"
    partial = release(completeness="partial", scope="fixture:por-only")
    assert partial.scope == "fixture:por-only"
    with pytest.raises(ValidationError, match="escopo explícito"):
        release(completeness="partial")


def test_import_rejects_duplicate_exact_uri_and_has_stable_payload_digest() -> None:
    request = VocabularyImport(
        idempotency_key="fixture-1", release=release(), concepts=(imported(),)
    )
    repeated_key = request.model_copy(update={"idempotency_key": "fixture-2"})
    assert request.payload_digest() == repeated_key.payload_digest()
    with pytest.raises(ValidationError, match="URI de conceito duplicada"):
        VocabularyImport(
            idempotency_key="fixture-3",
            release=release(),
            concepts=(imported(), imported()),
        )


def test_import_preserves_multilingual_transcription_notation_and_status() -> None:
    value = imported().concept
    assert [(label.value, label.language) for label in value.label] == [
        ("Português", "pt"),
        ("Portuguese", "en"),
    ]
    assert value.alternative_labels[0].value == "Língua portuguesa"
    assert value.notation == "por" and value.status == "resolved"
