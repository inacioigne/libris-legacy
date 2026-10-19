"""Deterministic offline ingestion for the official LC MARC Languages XML."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyIdentifierInput,
    VocabularyLabelInput,
)

LC_LANGUAGES_VOCABULARY = "http://id.loc.gov/vocabulary/languages/"
LC_LANGUAGES_XML = "https://www.loc.gov/standards/codelists/languages.xml"
LC_PROVIDER = "https://www.loc.gov/"
IMPORTER_VERSION = "libris-lc-languages-1"
XML_NAMESPACE = "info:lc/xmlns/codelist-v1"
CODE = re.compile(r"^[a-z]{3}$")


class LCLanguagesError(ValueError):
    """The source cannot be trusted as an LC Languages snapshot."""


IdentifierScheme = Literal["iso639-1", "iso639-2-b", "iso639-2-t", "iso639-3"]


class IdentifierEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scheme: IdentifierScheme
    value: str = Field(pattern=r"^[a-z]{2,3}$")
    source_uri: str


class IdentifierMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uri: str
    identifiers: tuple[IdentifierEvidence, ...]

    @model_validator(mode="after")
    def exact_uri(self) -> IdentifierMapping:
        if not self.uri.startswith(LC_LANGUAGES_VOCABULARY):
            raise ValueError("Mapeamento ISO exige URI canônica de LC Languages.")
        return self


class TranslationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    uri: str
    language: Literal["pt", "en"]
    value: str = Field(min_length=1)
    source_uri: str


@dataclass(frozen=True)
class ParsedLCLanguages:
    digest: str
    title: str
    source_identifier: str
    concepts: tuple[ImportedConcept, ...]


@dataclass(frozen=True)
class LCLanguagesChangeReport:
    added: tuple[str, ...]
    changed_labels: tuple[str, ...]
    changed_identifiers: tuple[str, ...]
    absent: tuple[str, ...]
    obsolete: tuple[str, ...]
    absence_means_obsolete: bool


def compare_lc_languages(
    previous: ParsedLCLanguages,
    current: ParsedLCLanguages,
    *,
    current_is_complete: bool,
) -> LCLanguagesChangeReport:
    """Compare logical releases; absence is not obsolescence for a partial source."""
    old = {item.concept.uri: item for item in previous.concepts}
    new = {item.concept.uri: item for item in current.concepts}
    shared = set(old) & set(new)
    absent = tuple(sorted(set(old) - set(new)))

    def labels(item: ImportedConcept) -> set[tuple[str, str, str, str]]:
        return {(label.kind, label.language, label.value, label.origin) for label in item.labels}

    def identifiers(item: ImportedConcept) -> set[tuple[str, str, str]]:
        return {
            (identifier.scheme, identifier.value, identifier.source_uri)
            for identifier in item.identifiers
        }

    return LCLanguagesChangeReport(
        added=tuple(sorted(set(new) - set(old))),
        changed_labels=tuple(sorted(uri for uri in shared if labels(old[uri]) != labels(new[uri]))),
        changed_identifiers=tuple(
            sorted(uri for uri in shared if identifiers(old[uri]) != identifiers(new[uri]))
        ),
        absent=absent,
        obsolete=tuple(
            sorted(
                uri
                for uri, item in new.items()
                if item.concept.status == "deprecated"
                and old.get(uri) is not None
                and old[uri].concept.status != "deprecated"
            )
        ),
        absence_means_obsolete=current_is_complete,
    )


def _text(element: ET.Element, name: str) -> str | None:
    child = element.find(f"{{{XML_NAMESPACE}}}{name}")
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _load_sidecar(path: Path | None, model: type[BaseModel]) -> list[BaseModel]:
    if path is None:
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise LCLanguagesError("Sidecar deve conter uma lista JSON.")
        return [model.model_validate(item) for item in payload]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        raise LCLanguagesError(f"Sidecar inválido: {path}") from error


def parse_lc_languages(
    artifact: bytes,
    *,
    identifier_sidecar: Path | None = None,
    translation_sidecar: Path | None = None,
    complete: bool = True,
) -> ParsedLCLanguages:
    """Parse bytes without network access, external entities, or remote contexts."""
    if not artifact:
        raise LCLanguagesError("Artefato LC Languages vazio.")
    try:
        text = artifact.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LCLanguagesError("LC Languages deve usar UTF-8 válido.") from error
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise LCLanguagesError("DTD e entidades externas não são aceitas.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise LCLanguagesError("XML LC Languages malformado.") from error
    if root.tag != f"{{{XML_NAMESPACE}}}codelist":
        raise LCLanguagesError("Namespace/raiz não corresponde ao codelist oficial da LC.")
    title = _text(root, "title")
    source_identifier = _text(root, "codelistId")
    if title != "MARC Code List for Languages" or not source_identifier:
        raise LCLanguagesError("Metadados obrigatórios do codelist estão ausentes.")

    mappings = {
        entry.uri: entry
        for entry in _load_sidecar(identifier_sidecar, IdentifierMapping)
        if isinstance(entry, IdentifierMapping)
    }
    translations: dict[str, list[TranslationEvidence]] = {}
    for entry in _load_sidecar(translation_sidecar, TranslationEvidence):
        if isinstance(entry, TranslationEvidence):
            translations.setdefault(entry.uri, []).append(entry)

    concepts: list[ImportedConcept] = []
    seen_uris: set[str] = set()
    seen_marc: set[str] = set()
    for language in root.findall(f".//{{{XML_NAMESPACE}}}language"):
        raw_code = _text(language, "code")
        name_nodes = language.findall(f"{{{XML_NAMESPACE}}}name")
        names = [node.text.strip() for node in name_nodes if node.text and node.text.strip()]
        if raw_code is None or not names:
            raise LCLanguagesError("Conceito sem código ou rótulo autorizado.")
        deprecated = raw_code.startswith("-") or language.get("status") == "discontinued"
        marc = raw_code.removeprefix("-").lower()
        if not CODE.fullmatch(marc):
            raise LCLanguagesError(f"Código MARC inválido: {raw_code}")
        uri = f"{LC_LANGUAGES_VOCABULARY}{marc}"
        if uri in seen_uris or marc in seen_marc:
            raise LCLanguagesError(f"URI/código MARC duplicado: {marc}")
        seen_uris.add(uri)
        seen_marc.add(marc)
        official_source = f"{LC_LANGUAGES_XML}#{marc}"
        labels = [
            VocabularyLabelInput(
                kind="preferred" if index == 0 else "alternative",
                value=name,
                language="en",
                origin="provider_official",
                source_uri=official_source,
            )
            for index, name in enumerate(names)
        ]
        labels.extend(
            VocabularyLabelInput(
                kind="preferred"
                if not any(
                    label.language == item.language and label.kind == "preferred"
                    for label in labels
                )
                else "alternative",
                value=item.value,
                language=item.language,
                origin="institutional_translation",
                source_uri=item.source_uri,
            )
            for item in translations.get(uri, [])
        )
        identifiers = [
            VocabularyIdentifierInput(scheme="marc", value=marc, source_uri=official_source)
        ]
        mapping = mappings.get(uri)
        if mapping is not None:
            identifiers.extend(
                VocabularyIdentifierInput(
                    scheme=item.scheme, value=item.value, source_uri=item.source_uri
                )
                for item in mapping.identifiers
            )
        concepts.append(
            ImportedConcept.model_validate(
                {
                    "concept": {
                        "kind": "controlled_concept",
                        "uri": uri,
                        "vocabulary": LC_LANGUAGES_VOCABULARY,
                        "label": [{"value": names[0], "language": "en"}],
                        "alternative_labels": [
                            {"value": name, "language": "en"} for name in names[1:]
                        ],
                        "notation": marc,
                        "source": official_source,
                        "status": "deprecated" if deprecated else "resolved",
                    },
                    "labels": [label.model_dump() for label in labels],
                    "identifiers": [item.model_dump() for item in identifiers],
                }
            )
        )
    if not concepts:
        raise LCLanguagesError("Snapshot não contém conceitos de idioma.")
    if complete and len(concepts) < 400:
        raise LCLanguagesError("Snapshot declarado completo tem menos de 400 conceitos.")
    unknown_sidecar = (set(mappings) | set(translations)) - seen_uris
    if unknown_sidecar:
        raise LCLanguagesError("Sidecar referencia conceitos ausentes no snapshot.")
    return ParsedLCLanguages(
        digest=sha256(artifact).hexdigest(),
        title=title,
        source_identifier=source_identifier,
        concepts=tuple(sorted(concepts, key=lambda item: item.concept.uri)),
    )
