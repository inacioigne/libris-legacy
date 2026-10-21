"""Explicit editorial commands and authorized historical queries for catalog v1."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from libris.api.problems import require_if_match
from libris.modules.bibliographic.api.capabilities import COLLECTIONS, Collection, profile_for
from libris.modules.bibliographic.api.dependencies import get_catalog_service
from libris.modules.bibliographic.api.limits import BoundedCatalogRoute
from libris.modules.bibliographic.api.representations import (
    SEMANTIC_RESPONSES,
    editing_etag,
    get_if_match,
    read_representation,
)
from libris.modules.bibliographic.api.schemas.editorial import (
    EditorialStateRead,
    EventCursor,
    EventPage,
    EventQuery,
    RestoreRequest,
    RevisionPage,
    TransitionRequest,
    WorkspaceValidationRead,
)
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.application.catalog_editorial import (
    CatalogEditorialService,
    EditorialView,
)
from libris.modules.bibliographic.application.comparison import SemanticDiff
from libris.modules.bibliographic.application.history import EditorialHistory
from libris.modules.bibliographic.domain.audit import AuditEvent
from libris.modules.bibliographic.domain.editorial import EditorialOperation as Op
from libris.modules.bibliographic.domain.editorial import EditorialState as State
from libris.modules.bibliographic.domain.errors import ResourceNotFoundError
from libris.modules.bibliographic.domain.models import Provenance, Revision
from libris.modules.bibliographic.infrastructure.rdf import serialize_document
from libris.modules.bibliographic.infrastructure.serialization import ResourceRead
from libris.modules.identity.api.dependencies import get_execution_context
from libris.modules.identity.application.authorization import HumanContext
from libris.modules.identity.domain.authorization import Operation

router = APIRouter(route_class=BoundedCatalogRoute, tags=["catalog-editorial"])
Context = Annotated[HumanContext, Depends(get_execution_context)]
IfMatch = Annotated[str, Depends(get_if_match)]
Positive = Annotated[int, Query(gt=0)]
WorkspaceCollection = Literal["works", "instances", "items", "agents"]
EditorialCommandCollection = Literal["works", "instances", "items", "subjects"]


def workspace_operation(collection: WorkspaceCollection, *, publish: bool) -> Operation:
    if collection == "agents":
        return (
            Operation.AUTHORITY_WORKSPACE_PUBLISH
            if publish
            else Operation.AUTHORITY_WORKSPACE_VALIDATE
        )
    return Operation.CATALOG_WORKSPACE_PUBLISH if publish else Operation.CATALOG_WORKSPACE_VALIDATE


def get_editorial_service(
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> CatalogEditorialService:
    return CatalogEditorialService(service.sessions, authorization=service.authorization)


Service = Annotated[CatalogEditorialService, Depends(get_editorial_service)]


def state_read(view: EditorialView) -> EditorialStateRead:
    state = view.state
    return EditorialStateRead(
        internal_id=view.catalog.revision.identity.internal_id,
        editorial_state=state.editorial_state if state else None,
        current_revision=view.catalog.revision.number,
        candidate_state=state.candidate_state if state else None,
        candidate_revision=state.candidate_revision if state else None,
        approved_revision=state.approved_revision if state else None,
        published_revision=state.published_revision if state else None,
        public_revision=state.public_revision if state else None,
        editorial_version=state.version if state else None,
    )


def read_operation(collection: Collection) -> Operation:
    return (
        Operation.AUTHORITY_READ_EDITORIAL
        if collection in {"agents", "subjects"}
        else Operation.BIBLIOGRAPHIC_READ_EDITORIAL
    )


@router.get("/{collection}/{id}/editorial", response_model=EditorialStateRead)
async def editorial_state(
    collection: Collection,
    id: UUID,
    response: Response,
    service: Service,
    context: Context,
) -> EditorialStateRead:
    view = await service.observe(
        id, profile_for(collection), COLLECTIONS[collection][2], context, read_operation(collection)
    )
    # Same editing validator used by GET/PATCH; expectations travel into persistent CAS.
    response.headers["ETag"] = editing_etag(view.catalog)
    return state_read(view)


async def command(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: CatalogEditorialService,
    context: HumanContext,
    if_match: str,
    operation: Op,
    target: State,
) -> EditorialStateRead:
    view = await service.observe(
        id,
        profile_for(collection),
        COLLECTIONS[collection][2],
        context,
        Operation(f"editorial.{operation.value}"),
    )
    require_if_match(if_match, current_etag=editing_etag(view.catalog))
    changed = await service.execute(
        view,
        operation,
        target,
        payload.selected_revision,
        payload.scope,
        payload.justification,
        context,
    )
    response.headers["ETag"] = editing_etag(changed.catalog)
    return state_read(changed)


@router.post(
    "/{collection}/{id}/workspace/validate",
    response_model=WorkspaceValidationRead,
    summary="Validate the current cataloging draft for publication",
)
async def validate_workspace(
    collection: WorkspaceCollection,
    id: UUID,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> WorkspaceValidationRead:
    view = await service.observe(
        id,
        profile_for(collection),
        COLLECTIONS[collection][2],
        context,
        workspace_operation(collection, publish=False),
    )
    require_if_match(if_match, current_etag=editing_etag(view.catalog))
    revision_number = await service.validate_workspace(view, context)
    return WorkspaceValidationRead(revision=revision_number)


@router.post(
    "/{collection}/{id}/workspace/publish",
    response_model=EditorialStateRead,
    summary="Validate and publish the current cataloging draft",
)
async def publish_workspace(
    collection: WorkspaceCollection,
    id: UUID,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    view = await service.observe(
        id,
        profile_for(collection),
        COLLECTIONS[collection][2],
        context,
        workspace_operation(collection, publish=True),
    )
    require_if_match(if_match, current_etag=editing_etag(view.catalog))
    changed = await service.publish_workspace(view, context)
    response.headers["ETag"] = editing_etag(changed.catalog)
    return state_read(changed)


@router.post("/{collection}/{id}/editorial/submit", response_model=EditorialStateRead)
async def submit(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.SUBMIT, State.IN_REVIEW
    )


@router.post("/{collection}/{id}/editorial/approve", response_model=EditorialStateRead)
async def approve(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.APPROVE, State.APPROVED
    )


@router.post("/{collection}/{id}/editorial/publish", response_model=EditorialStateRead)
async def publish(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.PUBLISH, State.PUBLISHED
    )


@router.post("/{collection}/{id}/editorial/return", response_model=EditorialStateRead)
async def return_for_editing(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.RETURN, State.DRAFT
    )


@router.post("/{collection}/{id}/editorial/withdraw", response_model=EditorialStateRead)
async def withdraw(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.WITHDRAW, State.WITHDRAWN
    )


@router.post("/{collection}/{id}/editorial/republish", response_model=EditorialStateRead)
async def republish(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.REPUBLISH, State.PUBLISHED
    )


@router.post("/{collection}/{id}/editorial/archive", response_model=EditorialStateRead)
async def archive(
    collection: EditorialCommandCollection,
    id: UUID,
    payload: TransitionRequest,
    response: Response,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> EditorialStateRead:
    return await command(
        collection, id, payload, response, service, context, if_match, Op.ARCHIVE, State.ARCHIVED
    )


@router.get("/{collection}/{id}/public", response_model=ResourceRead, responses=SEMANTIC_RESPONSES)
async def read_public(
    collection: Collection, id: UUID, service: Service, request: Request, response: Response
) -> Response:
    view = await service.public_view(id, COLLECTIONS[collection][2])
    return read_representation(request, response, view, public=True)


@router.get("/{collection}/{id}/revisions", response_model=RevisionPage)
async def revisions(
    collection: Collection,
    id: UUID,
    service: Service,
    context: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    after_number: Annotated[int, Query(ge=0)] = 0,
) -> RevisionPage:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_REVISION_READ,
    )
    items = await service.history.list_revisions(
        id, context=context, limit=limit, after_number=after_number
    )
    return RevisionPage(items=items, next_after_number=items[-1].revision.number if items else None)


@router.get("/{collection}/{id}/revisions/compare", response_model=SemanticDiff)
async def compare(
    collection: Collection,
    id: UUID,
    service: Service,
    context: Context,
    previous: Positive,
    current: Positive,
) -> SemanticDiff:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_REVISION_READ,
    )
    try:
        return await service.history.compare_revisions(id, previous, current, context=context)
    except LookupError as error:
        raise ResourceNotFoundError("Revisão inexistente.") from error


@router.get("/{collection}/{id}/revisions/{number}", response_model=Revision)
async def revision(
    collection: Collection,
    id: UUID,
    number: Annotated[int, Path(gt=0)],
    service: Service,
    context: Context,
) -> Revision:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_REVISION_READ,
    )
    try:
        selected, _ = await service.history.read_revision(id, number, context=context)
    except LookupError as error:
        raise ResourceNotFoundError("Revisão inexistente.") from error
    return selected


@router.get(
    "/{collection}/{id}/revisions/{number}/snapshot",
    response_class=Response,
    responses={200: {"content": {"text/turtle": {"schema": {"type": "string"}}}}},
)
async def snapshot(
    collection: Collection,
    id: UUID,
    number: Annotated[int, Path(gt=0)],
    service: Service,
    context: Context,
) -> Response:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_REVISION_READ,
    )
    try:
        _, graph = await service.history.read_revision(id, number, context=context)
    except LookupError as error:
        raise ResourceNotFoundError("Revisão inexistente.") from error
    return Response(serialize_document(graph, "turtle"), media_type="text/turtle")


@router.post("/{collection}/{id}/revisions/restore", response_model=Revision, status_code=201)
async def restore(
    collection: Collection,
    id: UUID,
    payload: RestoreRequest,
    service: Service,
    context: Context,
    if_match: IfMatch,
) -> Revision:
    view = await service.observe(
        id,
        profile_for(collection),
        COLLECTIONS[collection][2],
        context,
        Operation.METADATA_REVISION_RESTORE,
    )
    require_if_match(if_match, current_etag=editing_etag(view.catalog))
    if payload.source_revision > view.catalog.revision.number:
        raise ResourceNotFoundError("Revisão inexistente.")
    return await service.restore_content(
        id,
        payload.source_revision,
        view.catalog.revision.number,
        Provenance("catalog", "http:restore", payload.justification),
        context=context,
        expected_editorial_version=view.catalog.editorial_version or 1,
    )


@router.get("/{collection}/{id}/audit", response_model=EventPage)
async def audit(
    collection: Collection,
    id: UUID,
    service: Service,
    context: Context,
    page: Annotated[EventQuery, Query()],
) -> EventPage:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_AUDIT_READ,
    )
    items = await service.history.list_events(
        id, context=context, limit=page.limit, after=page.after
    )
    cursor = (
        EventCursor(occurred_at=items[-1].occurred_at, event_id=items[-1].event_id)
        if items
        else None
    )
    return EventPage(items=items, next_cursor=cursor)


@router.get("/{collection}/{id}/audit/{event_id}", response_model=AuditEvent)
async def audit_event(
    collection: Collection,
    id: UUID,
    event_id: UUID,
    service: Service,
    context: Context,
) -> AuditEvent:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_AUDIT_READ,
    )
    try:
        return await service.history.read_event(id, event_id, context=context)
    except LookupError as error:
        raise ResourceNotFoundError("Evento inexistente.") from error


@router.get("/{collection}/{id}/history", response_model=EditorialHistory)
async def history(
    collection: Collection,
    id: UUID,
    service: Service,
    context: Context,
    page: Annotated[EventQuery, Query()],
) -> EditorialHistory:
    await service.authorize_history(
        id,
        profile_for(collection),
        COLLECTIONS[collection][0],
        context,
        Operation.METADATA_EDITORIAL_HISTORY,
    )
    return await service.history.read_editorial(
        id, context=context, limit=page.limit, after=page.after
    )
