"""M08.03 deterministic LC Languages ingestion and release comparison."""

from pathlib import Path

import pytest
from libris.modules.bibliographic.application.lc_languages import (
    LCLanguagesError,
    compare_lc_languages,
    parse_lc_languages,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/vocabularies"
XML = (FIXTURES / "lc-languages-small.xml").read_bytes()
IDENTIFIERS = FIXTURES / "lc-languages-identifiers.json"
TRANSLATIONS = FIXTURES / "lc-languages-translations.json"


def parsed(xml: bytes = XML):
    return parse_lc_languages(
        xml,
        identifier_sidecar=IDENTIFIERS,
        translation_sidecar=TRANSLATIONS,
        complete=False,
    )


def test_parses_required_languages_with_exact_identity_codes_labels_and_provenance() -> None:
    result = parsed()
    assert len(result.digest) == 64
    concepts = {entry.concept.notation: entry for entry in result.concepts}
    assert set(concepts) == {"por", "eng", "spa"}
    assert concepts["por"].concept.uri == "http://id.loc.gov/vocabulary/languages/por"
    assert {(item.scheme, item.value) for item in concepts["por"].identifiers} == {
        ("marc", "por"),
        ("iso639-1", "pt"),
        ("iso639-2-b", "por"),
        ("iso639-2-t", "por"),
        ("iso639-3", "por"),
    }
    labels = {(item.language, item.value, item.origin) for item in concepts["por"].labels}
    assert ("en", "Portuguese", "provider_official") in labels
    assert ("pt", "Português", "institutional_translation") in labels


def test_iso_is_absent_without_governed_evidence() -> None:
    result = parse_lc_languages(XML, complete=False)
    assert all(
        [identifier.scheme for identifier in entry.identifiers] == ["marc"]
        for entry in result.concepts
    )


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        (b"", "vazio"),
        (b"\xff", "UTF-8"),
        (b"<broken>", "malformado"),
        (b"<!DOCTYPE x [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><x>&x;</x>", "entidades"),
        (XML.replace(b"<code>spa</code>", b""), "sem código"),
        (XML.replace(b'<name authorized="yes">Spanish</name>', b""), "sem código ou rótulo"),
        (XML.replace(b"<code>spa</code>", b"<code>por</code>"), "duplicado"),
    ],
)
def test_rejects_empty_invalid_malformed_unsafe_and_inconsistent_sources(
    artifact: bytes, message: str
) -> None:
    with pytest.raises(LCLanguagesError, match=message):
        parse_lc_languages(artifact, complete=False)


def test_complete_snapshot_requires_plausible_collection_size() -> None:
    with pytest.raises(LCLanguagesError, match="menos de 400"):
        parse_lc_languages(XML, complete=True)


def test_unicode_is_preserved_and_digest_is_reproducible() -> None:
    changed = XML.replace(b"Portuguese", "Português".encode())
    first = parse_lc_languages(changed, complete=False)
    second = parse_lc_languages(changed, complete=False)
    assert first.digest == second.digest
    assert any(label.value == "Português" for label in first.concepts[1].labels)


def test_comparison_distinguishes_changes_obsolescence_and_partial_absence() -> None:
    old = parse_lc_languages(XML, complete=False)
    changed_xml = XML.replace(
        b'<name authorized="yes">Portuguese</name>',
        b'<name authorized="yes">Portuguese language</name>',
    )
    changed_xml = changed_xml.replace(
        b"<language><uri>info:lc/vocabulary/languages/spa</uri>"
        b'<name authorized="yes">Spanish</name><code>spa</code></language>',
        b"",
    )
    current = parse_lc_languages(changed_xml, complete=False)
    report = compare_lc_languages(old, current, current_is_complete=False)
    assert report.changed_labels == ("http://id.loc.gov/vocabulary/languages/por",)
    assert report.absent == ("http://id.loc.gov/vocabulary/languages/spa",)
    assert report.obsolete == () and not report.absence_means_obsolete

    deprecated = parse_lc_languages(
        XML.replace(b"<code>spa</code>", b"<code>-spa</code>"), complete=False
    )
    explicit = compare_lc_languages(old, deprecated, current_is_complete=False)
    assert explicit.obsolete == ("http://id.loc.gov/vocabulary/languages/spa",)
