"""Operational catalog v1: only persistent create, editorial read and partial update."""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from libris.api.problems import require_if_match
from libris.modules.bibliographic.api.capabilities import router as capabilities_router
from libris.modules.bibliographic.api.dependencies import get_catalog_service, get_resource_base_uri
from libris.modules.bibliographic.api.editorial import router as editorial_router
from libris.modules.bibliographic.api.listing import router as listing_router
from libris.modules.bibliographic.api.representations import (
    SEMANTIC_RESPONSES,
    editing_etag,
    get_if_match,
    read_representation,
    set_representation_headers,
)
from libris.modules.bibliographic.api.schemas import (
    AgentCreate,
    AgentRead,
    AgentUpdate,
    InstanceCreate,
    InstanceRead,
    InstanceUpdate,
    ItemCreate,
    ItemRead,
    ItemUpdate,
    SubjectCreate,
    SubjectRead,
    SubjectUpdate,
    WorkCreate,
    WorkRead,
    WorkUpdate,
)
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.api.dependencies import get_execution_context
from libris.modules.identity.application.authorization import HumanContext

router = APIRouter(
    prefix="/api/v1/catalog",
    tags=["catalog"],
    responses={
        401: {"description": "Authentication required"},
        403: {"description": "Permission denied"},
    },
)
Service = Annotated[CatalogService, Depends(get_catalog_service)]
Context = Annotated[HumanContext, Depends(get_execution_context)]
BaseURI = Annotated[str, Depends(get_resource_base_uri)]
IfMatch = Annotated[str, Depends(get_if_match)]


@router.post("/works", response_model=WorkRead, status_code=201)
async def create_work(
    payload: WorkCreate,
    response: Response,
    context: Context,
    service: Service,
    base_uri: BaseURI,
) -> WorkRead:
    view = await service.create_resource(
        payload,
        ResourceIdentity.create(base_uri),
        CONTROLLED_VOCABULARY_PROFILE_ID,
        context,
    )
    set_representation_headers(response, view)
    response.headers["Location"] = f"/api/v1/catalog/works/{view.resource.internal_id}"
    return cast(WorkRead, view.resource)


@router.get("/works/{id}", response_model=WorkRead, responses=SEMANTIC_RESPONSES)
async def read_work(
    id: UUID,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> Response:
    view = await service.current(id, INSTITUTIONAL_PROFILE_ID, WorkRead, context)
    return read_representation(request, response, view)


@router.patch("/works/{id}", response_model=WorkRead)
async def update_work(
    id: UUID,
    payload: WorkUpdate,
    response: Response,
    context: Context,
    service: Service,
    if_match: IfMatch,
) -> WorkRead:
    current = await service.current(id, INSTITUTIONAL_PROFILE_ID, WorkRead, context, editing=True)
    require_if_match(if_match, current_etag=editing_etag(current))
    view = await service.update_resource(id, payload, current, context)
    set_representation_headers(response, view)
    return cast(WorkRead, view.resource)


@router.post("/instances", response_model=InstanceRead, status_code=201)
async def create_instance(
    payload: InstanceCreate,
    response: Response,
    context: Context,
    service: Service,
    base_uri: BaseURI,
) -> InstanceRead:
    view = await service.create_resource(
        payload,
        ResourceIdentity.create(base_uri),
        CONTROLLED_VOCABULARY_PROFILE_ID,
        context,
    )
    set_representation_headers(response, view)
    response.headers["Location"] = f"/api/v1/catalog/instances/{view.resource.internal_id}"
    return cast(InstanceRead, view.resource)


@router.get("/instances/{id}", response_model=InstanceRead, responses=SEMANTIC_RESPONSES)
async def read_instance(
    id: UUID,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> Response:
    view = await service.current(id, INSTITUTIONAL_PROFILE_ID, InstanceRead, context)
    return read_representation(request, response, view)


@router.patch("/instances/{id}", response_model=InstanceRead)
async def update_instance(
    id: UUID,
    payload: InstanceUpdate,
    response: Response,
    context: Context,
    service: Service,
    if_match: IfMatch,
) -> InstanceRead:
    current = await service.current(
        id, INSTITUTIONAL_PROFILE_ID, InstanceRead, context, editing=True
    )
    require_if_match(if_match, current_etag=editing_etag(current))
    view = await service.update_resource(id, payload, current, context)
    set_representation_headers(response, view)
    return cast(InstanceRead, view.resource)


@router.post("/items", response_model=ItemRead, status_code=201)
async def create_item(
    payload: ItemCreate,
    response: Response,
    context: Context,
    service: Service,
    base_uri: BaseURI,
) -> ItemRead:
    view = await service.create_resource(
        payload,
        ResourceIdentity.create(base_uri),
        CONTROLLED_VOCABULARY_PROFILE_ID,
        context,
    )
    set_representation_headers(response, view)
    response.headers["Location"] = f"/api/v1/catalog/items/{view.resource.internal_id}"
    return cast(ItemRead, view.resource)


@router.get("/items/{id}", response_model=ItemRead, responses=SEMANTIC_RESPONSES)
async def read_item(
    id: UUID,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> Response:
    view = await service.current(id, INSTITUTIONAL_PROFILE_ID, ItemRead, context)
    return read_representation(request, response, view)


@router.patch("/items/{id}", response_model=ItemRead)
async def update_item(
    id: UUID,
    payload: ItemUpdate,
    response: Response,
    context: Context,
    service: Service,
    if_match: IfMatch,
) -> ItemRead:
    current = await service.current(id, INSTITUTIONAL_PROFILE_ID, ItemRead, context, editing=True)
    require_if_match(if_match, current_etag=editing_etag(current))
    view = await service.update_resource(id, payload, current, context)
    set_representation_headers(response, view)
    return cast(ItemRead, view.resource)


@router.post("/agents", response_model=AgentRead, status_code=201)
async def create_agent(
    payload: AgentCreate,
    response: Response,
    context: Context,
    service: Service,
    base_uri: BaseURI,
) -> AgentRead:
    view = await service.create_resource(
        payload,
        ResourceIdentity.create(base_uri),
        AUTHORITY_PROFILE_ID,
        context,
    )
    set_representation_headers(response, view)
    response.headers["Location"] = f"/api/v1/catalog/agents/{view.resource.internal_id}"
    return cast(AgentRead, view.resource)


@router.get("/agents/{id}", response_model=AgentRead, responses=SEMANTIC_RESPONSES)
async def read_agent(
    id: UUID,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> Response:
    view = await service.current(id, AUTHORITY_PROFILE_ID, AgentRead, context)
    return read_representation(request, response, view)


@router.patch("/agents/{id}", response_model=AgentRead)
async def update_agent(
    id: UUID,
    payload: AgentUpdate,
    response: Response,
    context: Context,
    service: Service,
    if_match: IfMatch,
) -> AgentRead:
    current = await service.current(id, AUTHORITY_PROFILE_ID, AgentRead, context, editing=True)
    require_if_match(if_match, current_etag=editing_etag(current))
    view = await service.update_resource(id, payload, current, context)
    set_representation_headers(response, view)
    return cast(AgentRead, view.resource)


@router.post("/subjects", response_model=SubjectRead, status_code=201)
async def create_subject(
    payload: SubjectCreate,
    response: Response,
    context: Context,
    service: Service,
    base_uri: BaseURI,
) -> SubjectRead:
    view = await service.create_resource(
        payload,
        ResourceIdentity.create(base_uri),
        AUTHORITY_PROFILE_ID,
        context,
    )
    set_representation_headers(response, view)
    response.headers["Location"] = f"/api/v1/catalog/subjects/{view.resource.internal_id}"
    return cast(SubjectRead, view.resource)


@router.get("/subjects/{id}", response_model=SubjectRead, responses=SEMANTIC_RESPONSES)
async def read_subject(
    id: UUID,
    request: Request,
    response: Response,
    context: Context,
    service: Service,
) -> Response:
    view = await service.current(id, AUTHORITY_PROFILE_ID, SubjectRead, context)
    return read_representation(request, response, view)


@router.patch("/subjects/{id}", response_model=SubjectRead)
async def update_subject(
    id: UUID,
    payload: SubjectUpdate,
    response: Response,
    context: Context,
    service: Service,
    if_match: IfMatch,
) -> SubjectRead:
    current = await service.current(id, AUTHORITY_PROFILE_ID, SubjectRead, context, editing=True)
    require_if_match(if_match, current_etag=editing_etag(current))
    view = await service.update_resource(id, payload, current, context)
    set_representation_headers(response, view)
    return cast(SubjectRead, view.resource)


router.include_router(listing_router)

router.include_router(capabilities_router)

router.include_router(editorial_router)
