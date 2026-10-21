"""Offline RDF/Turtle parsing for the M08.03 LC Languages importer."""

from pathlib import Path

import pytest
from libris.modules.bibliographic.application.lc_languages import LCLanguagesError
from libris.modules.bibliographic.application.lc_languages_rdf import (
    parse_lc_languages_rdf,
)
from libris.modules.bibliographic.lc_languages_cli import _artifact_format

FIXTURES = Path(__file__).parents[1] / "fixtures/vocabularies"
TURTLE = (FIXTURES / "lc-languages-small.ttl").read_bytes()
IDENTIFIERS = FIXTURES / "lc-languages-identifiers.json"
TRANSLATIONS = FIXTURES / "lc-languages-translations.json"


def test_maps_canonical_uris_marc_notations_and_language_tagged_labels() -> None:
    result = parse_lc_languages_rdf(TURTLE, complete=False)
    concepts = {entry.concept.notation: entry for entry in result.concepts}

    assert set(concepts) == {"eng", "por", "spa"}
    assert concepts["por"].concept.uri == "http://id.loc.gov/vocabulary/languages/por"
    assert [(value.value, value.language) for value in concepts["por"].concept.label] == [
        ("Portugiesisch", "de"),
        ("Portuguese", "en"),
    ]
    assert [
        (value.value, value.language) for value in concepts["eng"].concept.alternative_labels
    ] == [("English language", "en")]
    assert {
        (label.kind, label.value, label.language, label.origin)
        for label in concepts["eng"].labels
    } >= {("preferred", "English", "en", "provider_official")}
    assert (concepts["por"].identifiers[0].scheme, concepts["por"].identifiers[0].value) == (
        "marc",
        "por",
    )
    assert result.format == "turtle"
    assert result.source_uri == "http://id.loc.gov/vocabulary/languages"


def test_preserves_existing_translation_and_identifier_sidecars() -> None:
    result = parse_lc_languages_rdf(
        TURTLE,
        identifier_sidecar=IDENTIFIERS,
        translation_sidecar=TRANSLATIONS,
        complete=False,
    )
    concepts = {entry.concept.notation: entry for entry in result.concepts}

    assert ("iso639-1", "pt") in {
        (item.scheme, item.value) for item in concepts["por"].identifiers
    }
    assert ("pt", "Português", "institutional_translation") in {
        (item.language, item.value, item.origin) for item in concepts["por"].labels
    }


def test_complete_release_requires_explicit_count_and_required_coverage() -> None:
    with pytest.raises(LCLanguagesError, match="expected-concepts"):
        parse_lc_languages_rdf(TURTLE)
    parsed = parse_lc_languages_rdf(TURTLE, expected_concepts=3)
    assert len(parsed.concepts) == 3
    with pytest.raises(LCLanguagesError, match="Contagem de conceitos"):
        parse_lc_languages_rdf(TURTLE, expected_concepts=4)


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        (b"", "vazio"),
        (b"\xff", "UTF-8"),
        (b"@prefix skos: <", "malformado"),
        (
            TURTLE
            + b"\n<http://id.loc.gov/vocabulary/languages/eng> skos:notation \"foo\" .",
            "exatamente uma",
        ),
        (
            TURTLE.replace(
                b'<http://id.loc.gov/vocabulary/languages> ;',
                b'<http://example.org/wrong-scheme> ;',
                1,
            ),
            "pertencer",
        ),
        (
            TURTLE.replace(b'"MARC Language"@en', b'"Language"@en'),
            "identifica explicitamente",
        ),
    ],
)
def test_rejects_invalid_or_inconsistent_rdf(artifact: bytes, message: str) -> None:
    with pytest.raises(LCLanguagesError, match=message):
        parse_lc_languages_rdf(artifact, complete=False)


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        (
            TURTLE.replace(
                b'    skos:inScheme <http://id.loc.gov/vocabulary/languages> ;\n',
                b"",
                1,
            ),
            "pertencer",
        ),
        (
            TURTLE.replace(
                b'    skos:notation "por" ;\n'
                b'    skos:prefLabel "Portuguese"@en, "Portugiesisch"@de .',
                b'    skos:notation "por" .',
            ),
            "prefLabel",
        ),
        (
            TURTLE.replace(b'    skos:notation "spa" ;\n', b""),
            "exatamente uma",
        ),
    ],
)
def test_rejects_missing_scheme_label_or_notation(
    artifact: bytes, message: str
) -> None:
    with pytest.raises(LCLanguagesError, match=message):
        parse_lc_languages_rdf(artifact, complete=False)


def test_rejects_duplicate_marc_notation_and_oversized_input() -> None:
    duplicate = TURTLE + b"""
<http://id.loc.gov/vocabulary/languages/extra/por>
    a <http://www.w3.org/2004/02/skos/core#Concept> ;
    <http://www.w3.org/2004/02/skos/core#inScheme>
        <http://id.loc.gov/vocabulary/languages> ;
    <http://www.w3.org/2004/02/skos/core#notation> "por" ;
    <http://www.w3.org/2004/02/skos/core#prefLabel> "Portuguese copy"@en .
"""
    with pytest.raises(LCLanguagesError, match="URI canônica"):
        parse_lc_languages_rdf(duplicate, complete=False)
    double_notation = TURTLE + b"""
<http://id.loc.gov/vocabulary/languages/eng>
    <http://www.w3.org/2004/02/skos/core#notation> "english" .
"""
    with pytest.raises(LCLanguagesError, match="exatamente uma"):
        parse_lc_languages_rdf(double_notation, complete=False)
    with pytest.raises(LCLanguagesError, match="limite"):
        parse_lc_languages_rdf(TURTLE, complete=False, max_bytes=len(TURTLE) - 1)


def test_maps_explicit_deprecation_only() -> None:
    deprecated = TURTLE.replace(
        b'<http://id.loc.gov/vocabulary/languages/spa>\n    a skos:Concept ;',
        b'<http://id.loc.gov/vocabulary/languages/spa>\n    a skos:Concept ;\n'
        b'    owl:deprecated true ;',
    )
    concepts = parse_lc_languages_rdf(deprecated, complete=False).concepts
    spanish = next(item for item in concepts if item.concept.notation == "spa")
    assert spanish.concept.status == "deprecated"


def test_digest_and_concept_order_are_deterministic() -> None:
    first = parse_lc_languages_rdf(TURTLE, complete=False)
    second = parse_lc_languages_rdf(TURTLE, complete=False)
    assert first.digest == second.digest
    assert [item.concept.uri for item in first.concepts] == [
        item.concept.uri for item in second.concepts
    ]


def test_cli_detects_turtle_by_extension_or_explicit_format(tmp_path: Path) -> None:
    assert _artifact_format(tmp_path / "languages.skosrdf.ttl", None) == "turtle"
    assert _artifact_format(tmp_path / "source.data", "turtle") == "turtle"

    with pytest.raises(LCLanguagesError, match="Formato não reconhecido"):
        _artifact_format(tmp_path / "languages.data", None)


def test_invalid_concept_model_data_is_reported_as_parser_error() -> None:
    invalid = TURTLE.replace(b'"Portuguese"@en', b'" " @en')
    with pytest.raises(LCLanguagesError):
        parse_lc_languages_rdf(invalid, complete=False)
