"""HTTP contracts for reading the operational vocabulary catalog."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from libris.modules.bibliographic.api.schemas.common import IRI, Contract, LanguageTag
from libris.modules.bibliographic.application.controlled_values import ReferenceStatus
from libris.modules.bibliographic.application.diagnostics import Diagnostic
from libris.modules.bibliographic.application.vocabulary_catalog import Completeness


class VocabularyReleaseSummary(Contract):
    id: UUID
    key: str
    source_version: str | None
    retrieved_at: datetime
    completeness: Completeness
    scope: str | None


class VocabularySummary(Contract):
    id: UUID
    uri: IRI
    name: str
    description: str | None
    source_uri: IRI
    provider_uri: IRI
    status: Literal["active"] = "active"
    availability: Literal["available"] = "available"
    release: VocabularyReleaseSummary


class VocabularyPage(Contract):
    items: list[VocabularySummary]
    total: int


class VocabularyIdentity(Contract):
    id: UUID
    uri: IRI
    name: str


class ConceptProvenance(Contract):
    vocabulary_source: str
    provider_uri: IRI
    source_uri: IRI
    release_id: UUID
    release_key: str
    source_version: str | None
    retrieved_at: datetime
    completeness: Completeness
    label_origin: str | None = None
    label_source_uri: IRI | None = None


class ConceptSearchItem(Contract):
    concept_id: UUID
    uri: IRI
    label: str | None
    language: str | None
    notation: str | None
    vocabulary: VocabularyIdentity
    status: ReferenceStatus
    release_id: UUID
    release_key: str
    provenance: ConceptProvenance


class Pagination(Contract):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0, le=100_000)
    total: int = Field(ge=0)


class ConceptSearchPage(Contract):
    items: list[ConceptSearchItem]
    pagination: Pagination
    order: Literal["uri:asc,concept_id:asc"] = "uri:asc,concept_id:asc"


class ConceptLabel(Contract):
    kind: Literal["preferred", "alternative"]
    value: str
    language: str
    origin: str
    source_uri: IRI


class ConceptDetail(ConceptSearchItem):
    labels: list[ConceptLabel]
    replacement_uri: IRI | None
    deprecated: bool


class ValidateConceptRequest(Contract):
    uri: IRI


class ConceptValidation(Contract):
    uri: IRI
    vocabulary: VocabularyIdentity
    belongs: bool | None
    status: ReferenceStatus
    valid: bool | None
    release_id: UUID | None
    release_key: str | None
    provenance: ConceptProvenance | None
    replacement_uri: IRI | None
    diagnostics: tuple[Diagnostic, ...]


class ConceptSearchParameters(Contract):
    q: str | None = Field(default=None, min_length=1, max_length=200)
    uri: IRI | None = None
    locale: LanguageTag = "en"
    label_language: LanguageTag | None = None
    status: ReferenceStatus | None = None
    release_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100_000)

    @model_validator(mode="after")
    def query_has_visible_characters(self) -> "ConceptSearchParameters":
        if self.q is not None and not self.q.strip():
            raise ValueError("O termo de busca não pode conter somente espaços.")
        return self
