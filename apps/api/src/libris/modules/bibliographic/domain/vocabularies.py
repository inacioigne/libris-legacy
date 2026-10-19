"""Trusted vocabulary membership evidence, independent of labels and URI prefixes."""

from typing import Literal

from pydantic import Field, model_validator

from .profile_contract import IRI, Contract

CONTROLLED_VOCABULARY_PROFILE_ID = "urn:libris:profile:monograph:v1:2"


class VocabularyConceptEvidence(Contract):
    """Exact-URI status evidence pinned to one persisted vocabulary release."""

    uri: IRI
    status: Literal["resolved", "unverified", "invalid", "deprecated", "unavailable"]
    source: IRI | None = None
    replacement: IRI | None = None

    @model_validator(mode="after")
    def consistent(self) -> "VocabularyConceptEvidence":
        if self.status in {"resolved", "invalid", "deprecated"} and self.source is None:
            raise ValueError("Evidência conclusiva exige uma fonte.")
        if self.replacement is not None and (
            self.status != "deprecated" or self.replacement == self.uri
        ):
            raise ValueError("Sucessor exige conceito deprecated e URI distinta.")
        return self


def membership_from_evidence(*, present: bool, complete: bool) -> bool | None:
    if present:
        return True
    return False if complete else None


class VocabularyDefinition(Contract):
    uri: IRI
    release: str = Field(min_length=1)
    source: IRI
    members: tuple[IRI, ...] = ()
    concepts: tuple[VocabularyConceptEvidence, ...] = ()
    complete: bool = False

    @model_validator(mode="after")
    def unique(self) -> "VocabularyDefinition":
        if len(set(self.members)) != len(self.members):
            raise ValueError("Conceito duplicado no vocabulário.")
        concept_uris = tuple(concept.uri for concept in self.concepts)
        if len(set(concept_uris)) != len(concept_uris):
            raise ValueError("Evidência de conceito duplicada no vocabulário.")
        return self

    def concept(self, uri: str) -> VocabularyConceptEvidence | None:
        return next((concept for concept in self.concepts if concept.uri == uri), None)


class VocabularyRegistry(Contract):
    """Caller-selected immutable evidence snapshot; absence in a partial set is unknown."""

    contract_version: Literal["1.0"] = "1.0"
    vocabularies: tuple[VocabularyDefinition, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> "VocabularyRegistry":
        if len({entry.uri for entry in self.vocabularies}) != len(self.vocabularies):
            raise ValueError("Selecione uma única release por vocabulário.")
        return self

    def membership(self, uri: str, vocabulary: str) -> bool | None:
        entry = self.definition(vocabulary)
        if entry is None:
            return None
        concept = entry.concept(uri)
        if concept is not None:
            if concept.status in {"resolved", "deprecated"}:
                return True
            if concept.status in {"unverified", "unavailable"}:
                return None
            return False
        return membership_from_evidence(present=uri in entry.members, complete=entry.complete)

    def concept_evidence(self, uri: str, vocabulary: str) -> VocabularyConceptEvidence | None:
        entry = self.definition(vocabulary)
        return entry.concept(uri) if entry is not None else None

    def definition(self, vocabulary: str) -> VocabularyDefinition | None:
        return next((entry for entry in self.vocabularies if entry.uri == vocabulary), None)
