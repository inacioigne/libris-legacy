"""Authenticated, read-only HTTP access to active vocabulary releases."""

import hashlib
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from libris.modules.bibliographic.api.dependencies import get_vocabulary_query_service
from libris.modules.bibliographic.api.schemas.vocabularies import (
    ConceptDetail,
    ConceptSearchPage,
    ConceptSearchParameters,
    ConceptValidation,
    ValidateConceptRequest,
    VocabularyPage,
)
from libris.modules.bibliographic.application.vocabulary_queries import VocabularyQueryService
from libris.modules.identity.api.dependencies import require_permissions
from libris.modules.identity.domain.permissions import Permission

router = APIRouter(
    prefix="/api/v1/vocabularies",
    tags=["vocabularies"],
    dependencies=[
        Depends(
            require_permissions(
                Permission.BIBLIOGRAPHIC_READ,
                Permission.METADATA_REVISION_READ,
            )
        )
    ],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Permission denied"},
    },
)
QueryService = Annotated[VocabularyQueryService, Depends(get_vocabulary_query_service)]


def cached_response(request: Request, payload: object, *, status_code: int = 200) -> Response:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    etag = '"' + hashlib.sha256(encoded).hexdigest() + '"'
    headers = {
        "Cache-Control": "private, no-cache, must-revalidate",
        "ETag": etag,
        "Vary": "Authorization",
    }
    validators = [
        token.strip()
        for header in request.headers.getlist("if-none-match")
        for token in header.split(",")
    ]
    if any(
        validator == "*" or validator.removeprefix("W/") == etag
        for validator in validators
    ):
        return Response(status_code=304, headers=headers)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


@router.get("", response_model=VocabularyPage, responses={304: {"description": "Not modified"}})
async def list_vocabularies(
    request: Request,
    service: QueryService,
) -> Response:
    page = await service.list_available()
    return cached_response(request, page.model_dump(mode="json", exclude_none=True))


@router.get(
    "/{vocabulary_id}/concepts",
    response_model=ConceptSearchPage,
    responses={304: {"description": "Not modified"}},
)
async def search_concepts(
    vocabulary_id: UUID,
    request: Request,
    service: QueryService,
    parameters: Annotated[ConceptSearchParameters, Query()],
) -> Response:
    page = await service.search(
        vocabulary_id,
        q=parameters.q,
        uri=parameters.uri,
        locale=parameters.locale,
        label_language=parameters.label_language,
        status=parameters.status,
        release_id=parameters.release_id,
        limit=parameters.limit,
        offset=parameters.offset,
    )
    return cached_response(request, page.model_dump(mode="json", exclude_none=True))


@router.get(
    "/{vocabulary_id}/concepts/{concept_id}",
    response_model=ConceptDetail,
    responses={304: {"description": "Not modified"}},
)
async def read_concept(
    vocabulary_id: UUID,
    concept_id: UUID,
    request: Request,
    service: QueryService,
    locale: Annotated[str, Query(pattern=r"^[a-zA-Z]+(?:-[a-zA-Z0-9]+)*$", max_length=63)] = "en",
    release_id: UUID | None = None,
) -> Response:
    detail = await service.detail(
        vocabulary_id,
        concept_id,
        locale=locale,
        release_id=release_id,
    )
    return cached_response(request, detail.model_dump(mode="json", exclude_none=True))


@router.post(
    "/{vocabulary_id}/validate",
    response_model=ConceptValidation,
    responses={200: {"description": "Semantic validation result"}},
)
async def validate_concept(
    vocabulary_id: UUID,
    payload: ValidateConceptRequest,
    response: Response,
    service: QueryService,
    release_id: UUID | None = None,
) -> ConceptValidation:
    result = await service.validate(vocabulary_id, payload, release_id=release_id)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    return result
