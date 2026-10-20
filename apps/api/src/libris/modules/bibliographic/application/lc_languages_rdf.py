"""Offline parser for the Library of Congress MARC Languages SKOS/RDF snapshot."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import ValidationError
from rdflib import OWL, RDF, RDFS, Graph, URIRef
from rdflib import Literal as RDFLiteral
from rdflib.exceptions import ParserError
from rdflib.namespace import SKOS

from libris.modules.bibliographic.application.lc_languages import (
    CODE,
    LC_LANGUAGES_VOCABULARY,
    LCLanguagesError,
    ParsedLCLanguages,
    _load_lc_language_sidecars,
)
from libris.modules.bibliographic.application.vocabulary_catalog import (
    ImportedConcept,
    VocabularyIdentifierInput,
    VocabularyLabelInput,
)

MAX_TURTLE_BYTES = 10 * 1024 * 1024
LC_LANGUAGES_SCHEME = LC_LANGUAGES_VOCABULARY.rstrip("/")
LC_MARC_LABEL_URI = f"{LC_LANGUAGES_VOCABULARY}MARC_Language"
SKOS_EDITORIAL = URIRef(f"{SKOS}editorial")
REQUIRED_COMPLETE_CODES = frozenset({"eng", "por", "spa"})
UNMAPPED_SKOS_PROPERTIES = frozenset(
    {SKOS.changeNote, SKOS_EDITORIAL, SKOS.exactMatch, SKOS.historyNote}
)
MAPPED_CONCEPT_PROPERTIES = frozenset(
    {
        RDF.type,
        SKOS.inScheme,
        SKOS.notation,
        SKOS.prefLabel,
        SKOS.altLabel,
        OWL.deprecated,
    }
)


def _labels(graph: Graph, subject: URIRef, predicate: URIRef) -> list[RDFLiteral]:
    values = list(graph.objects(subject, predicate))
    if any(not isinstance(value, RDFLiteral) for value in values):
        raise LCLanguagesError(f"Rótulo RDF não literal no conceito {subject}.")
    result = [value for value in values if isinstance(value, RDFLiteral)]
    if any(not str(value).strip() for value in result):
        raise LCLanguagesError(f"Rótulo RDF vazio no conceito {subject}.")
    if any(
        value.datatype not in (None, URIRef("http://www.w3.org/2001/XMLSchema#string"))
        for value in result
    ):
        raise LCLanguagesError(f"Datatype de rótulo não suportado no conceito {subject}.")
    return sorted(result, key=lambda item: (item.language or "", str(item)))


def parse_lc_languages_rdf(
    artifact: bytes,
    *,
    identifier_sidecar: Path | None = None,
    translation_sidecar: Path | None = None,
    complete: bool = True,
    expected_concepts: int | None = None,
    max_bytes: int = MAX_TURTLE_BYTES,
) -> ParsedLCLanguages:
    """Parse local Turtle bytes; never dereference RDF resources or remote contexts."""
    if max_bytes <= 0:
        raise LCLanguagesError("O limite de bytes deve ser positivo.")
    if not artifact:
        raise LCLanguagesError("Artefato Turtle de LC Languages vazio.")
    if len(artifact) > max_bytes:
        raise LCLanguagesError(f"Artefato Turtle excede o limite de {max_bytes} bytes.")
    try:
        text = artifact.decode("utf-8")
    except UnicodeDecodeError as error:
        raise LCLanguagesError("Turtle de LC Languages deve usar UTF-8 válido.") from error

    graph = Graph()
    try:
        graph.parse(data=text, format="turtle")
    except (ParserError, SyntaxError, ValueError) as error:
        raise LCLanguagesError("Turtle de LC Languages malformado ou inválido.") from error

    raw_concepts = set(graph.subjects(RDF.type, SKOS.Concept))
    if not raw_concepts:
        raise LCLanguagesError("Grafo não contém conceitos SKOS.")
    concepts = {subject for subject in raw_concepts if isinstance(subject, URIRef)}
    if len(concepts) != len(raw_concepts):
        raise LCLanguagesError("Conceito LC Languages sem URI persistente.")

    scheme_uri = URIRef(LC_LANGUAGES_SCHEME)
    memberships = {
        subject: set(graph.objects(subject, SKOS.inScheme)) for subject in concepts
    }
    if any(value != {scheme_uri} for value in memberships.values()):
        raise LCLanguagesError(
            f"Todos os conceitos devem pertencer somente ao esquema {LC_LANGUAGES_SCHEME}."
        )
    if set(graph.subjects(SKOS.inScheme, scheme_uri)) != concepts:
        raise LCLanguagesError("O esquema inclui recursos não tipados como skos:Concept.")

    marker = URIRef(LC_MARC_LABEL_URI)
    if (
        marker,
        RDFS.label,
        RDFLiteral("MARC Language", lang="en"),
    ) not in graph:
        raise LCLanguagesError("O artefato não identifica explicitamente o vocabulário MARC.")

    mappings, translations = _load_lc_language_sidecars(
        identifier_sidecar, translation_sidecar
    )
    imported: list[ImportedConcept] = []
    seen_codes: set[str] = set()
    unmapped: Counter[str] = Counter()
    for subject in sorted(concepts, key=str):
        uri = str(subject)
        if not uri.startswith(LC_LANGUAGES_VOCABULARY):
            raise LCLanguagesError(f"URI fora do namespace canônico LC Languages: {uri}")
        raw_notations = list(graph.objects(subject, SKOS.notation))
        if len(raw_notations) != 1 or not isinstance(raw_notations[0], RDFLiteral):
            raise LCLanguagesError(f"Conceito deve ter exatamente uma skos:notation: {uri}")
        notation_node = raw_notations[0]
        if notation_node.language is not None or notation_node.datatype not in (
            None,
            URIRef("http://www.w3.org/2001/XMLSchema#string"),
        ):
            raise LCLanguagesError(f"Notação MARC inválida: {uri}")
        notation = str(notation_node)
        if (
            not CODE.fullmatch(notation)
            or uri != f"{LC_LANGUAGES_VOCABULARY}{notation}"
            or urlsplit(uri).query
            or urlsplit(uri).fragment
        ):
            raise LCLanguagesError(f"Notação MARC não corresponde à URI canônica: {uri}")
        if notation in seen_codes:
            raise LCLanguagesError(f"Notação MARC duplicada: {notation}")
        seen_codes.add(notation)

        preferred = _labels(graph, subject, SKOS.prefLabel)
        alternative = _labels(graph, subject, SKOS.altLabel)
        if not preferred:
            raise LCLanguagesError(f"Conceito sem skos:prefLabel: {uri}")
        preferred_languages = [value.language or "" for value in preferred]
        if len(preferred_languages) != len(set(preferred_languages)):
            raise LCLanguagesError(f"Mais de um rótulo preferencial por idioma: {uri}")

        deprecated_nodes = list(graph.objects(subject, OWL.deprecated))
        if any(not isinstance(value, RDFLiteral) for value in deprecated_nodes):
            raise LCLanguagesError(f"Valor owl:deprecated inválido: {uri}")
        deprecated_literals = [
            value for value in deprecated_nodes if isinstance(value, RDFLiteral)
        ]
        if len(deprecated_literals) > 1 or any(
            not isinstance(value.toPython(), bool) for value in deprecated_literals
        ):
            raise LCLanguagesError(f"Valor owl:deprecated inválido: {uri}")
        deprecated = bool(
            deprecated_literals and deprecated_literals[0].toPython()
        )

        for predicate in set(graph.predicates(subject=subject)):
            if predicate not in MAPPED_CONCEPT_PROPERTIES:
                if predicate not in UNMAPPED_SKOS_PROPERTIES:
                    raise LCLanguagesError(
                        f"Propriedade RDF não mapeada; revisão necessária: {predicate}"
                    )
                unmapped[str(predicate)] += len(list(graph.objects(subject, predicate)))

        labels = [
            VocabularyLabelInput(
                kind="preferred",
                value=str(value),
                language=(value.language or "").lower(),
                origin="provider_official",
                source_uri=uri,
            )
            for value in preferred
        ]
        labels.extend(
            VocabularyLabelInput(
                kind="alternative",
                value=str(value),
                language=(value.language or "").lower(),
                origin="provider_official",
                source_uri=uri,
            )
            for value in alternative
        )
        for evidence in translations.get(uri, []):
            kind: Literal["preferred", "alternative"] = (
                "alternative"
                if any(
                    label.kind == "preferred" and label.language == evidence.language
                    for label in labels
                )
                else "preferred"
            )
            labels.append(
                VocabularyLabelInput(
                    kind=kind,
                    value=evidence.value,
                    language=evidence.language,
                    origin="institutional_translation",
                    source_uri=evidence.source_uri,
                )
            )

        identifiers = [
            VocabularyIdentifierInput(scheme="marc", value=notation, source_uri=uri)
        ]
        mapping = mappings.get(uri)
        if mapping is not None:
            identifiers.extend(
                VocabularyIdentifierInput(
                    scheme=value.scheme,
                    value=value.value,
                    source_uri=value.source_uri,
                )
                for value in mapping.identifiers
            )

        try:
            imported.append(
                ImportedConcept.model_validate(
                    {
                        "concept": {
                            "kind": "controlled_concept",
                            "uri": uri,
                            "vocabulary": LC_LANGUAGES_VOCABULARY,
                            "label": [
                                {
                                    "value": str(value),
                                    "language": (value.language or "").lower(),
                                }
                                for value in preferred
                            ],
                            "alternative_labels": [
                                {
                                    "value": str(value),
                                    "language": (value.language or "").lower(),
                                }
                                for value in alternative
                            ],
                            "notation": notation,
                            "source": uri,
                            "status": "deprecated" if deprecated else "resolved",
                        },
                        "labels": [value.model_dump() for value in labels],
                        "identifiers": [value.model_dump() for value in identifiers],
                    }
                )
            )
        except ValidationError as error:
            raise LCLanguagesError(f"Metadados incompatíveis com o catálogo: {uri}") from error

    unknown_sidecar = (set(mappings) | set(translations)) - {item.concept.uri for item in imported}
    if unknown_sidecar:
        raise LCLanguagesError("Sidecar referencia conceitos ausentes no snapshot.")

    codes = {item.concept.notation for item in imported}
    if any(
        item.concept.uri != f"{LC_LANGUAGES_VOCABULARY}{item.concept.notation}"
        for item in imported
    ):
        raise LCLanguagesError("URI e notação não formam identidade canônica única.")
    if complete:
        if expected_concepts is None or expected_concepts <= 0:
            raise LCLanguagesError(
                "Release Turtle completa exige --expected-concepts com a cobertura auditada."
            )
        if len(imported) != expected_concepts:
            raise LCLanguagesError(
                f"Contagem de conceitos divergente: esperado {expected_concepts}, "
                f"encontrado {len(imported)}."
            )
        if not REQUIRED_COMPLETE_CODES <= codes:
            missing = ", ".join(sorted(REQUIRED_COMPLETE_CODES - codes))
            raise LCLanguagesError(f"Cobertura completa sem códigos MARC obrigatórios: {missing}")
    elif expected_concepts is not None and len(imported) != expected_concepts:
        raise LCLanguagesError(
            f"Contagem de conceitos divergente: esperado {expected_concepts}, "
            f"encontrado {len(imported)}."
        )

    return ParsedLCLanguages(
        digest=sha256(artifact).hexdigest(),
        title="MARC Languages SKOS/RDF",
        source_identifier=LC_LANGUAGES_SCHEME,
        concepts=tuple(imported),
        source_uri=LC_LANGUAGES_SCHEME,
        format="turtle",
        unmapped_properties=tuple(sorted(unmapped.items())),
    )
