"""Trusted vocabulary membership evidence, independent of labels and URI prefixes."""

from typing import Literal

from pydantic import model_validator

from .profile_contract import IRI, Contract, Version


def membership_from_evidence(*, present: bool, complete: bool) -> bool | None:
    if present:
        return True
    return False if complete else None


class VocabularyDefinition(Contract):
    uri: IRI
    release: Version
    source: IRI
    members: tuple[IRI, ...] = ()
    complete: bool = False

    @model_validator(mode="after")
    def unique(self) -> "VocabularyDefinition":
        if len(set(self.members)) != len(self.members):
            raise ValueError("Conceito duplicado no vocabulário.")
        return self


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
        entry = next((entry for entry in self.vocabularies if entry.uri == vocabulary), None)
        if entry is None:
            return None
        return membership_from_evidence(present=uri in entry.members, complete=entry.complete)
