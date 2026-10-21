"""Closed operation policies, independent of HTTP and other domains' storage."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from libris.modules.identity.domain.permissions import Permission as P


class ResourceKind(StrEnum):
    BIBLIOGRAPHIC = "bibliographic"
    AUTHORITY = "authority"
    METADATA = "metadata"
    SYSTEM = "system"


class Operation(StrEnum):
    BIBLIOGRAPHIC_READ_PUBLIC = "bibliographic.read_public"
    BIBLIOGRAPHIC_READ_EDITORIAL = "bibliographic.read_editorial"
    BIBLIOGRAPHIC_CREATE = "bibliographic.create"
    BIBLIOGRAPHIC_UPDATE = "bibliographic.update"
    CATALOG_WORKSPACE_VALIDATE = "catalog_workspace.validate"
    CATALOG_WORKSPACE_PUBLISH = "catalog_workspace.publish"
    BIBLIOGRAPHIC_DELETE = "bibliographic.delete"
    AUTHORITY_READ_PUBLIC = "authority.read_public"
    AUTHORITY_READ_EDITORIAL = "authority.read_editorial"
    AUTHORITY_MANAGE = "authority.manage"
    METADATA_REVISION_READ = "metadata_revision.read"
    METADATA_REVISION_CREATE = "metadata_revision.create"
    METADATA_REVISION_RESTORE = "metadata_revision.restore"
    METADATA_AUDIT_READ = "metadata_audit.read"
    METADATA_EDITORIAL_HISTORY = "metadata.editorial_history"
    EDITORIAL_SUBMIT = "editorial.submit"
    EDITORIAL_APPROVE = "editorial.approve"
    EDITORIAL_RETURN = "editorial.return"
    EDITORIAL_PUBLISH = "editorial.publish"
    EDITORIAL_WITHDRAW = "editorial.withdraw"
    EDITORIAL_REPUBLISH = "editorial.republish"
    EDITORIAL_ARCHIVE = "editorial.archive"
    SYSTEM_ADMIN = "system.admin"


@dataclass(frozen=True)
class OperationPolicy:
    permissions: frozenset[P]
    resources: frozenset[ResourceKind]
    public: bool = False
    technical: bool = False
    published_projection: bool = False


def _policy(kind: ResourceKind, *permissions: P, technical: bool = False) -> OperationPolicy:
    return OperationPolicy(frozenset(permissions), frozenset({kind}), technical=technical)


K = ResourceKind
Op = Operation
POLICIES = MappingProxyType(
    {
        Op.BIBLIOGRAPHIC_READ_PUBLIC: OperationPolicy(
            frozenset(), frozenset({K.BIBLIOGRAPHIC}), public=True, published_projection=True
        ),
        Op.AUTHORITY_READ_PUBLIC: OperationPolicy(
            frozenset(), frozenset({K.AUTHORITY}), public=True, published_projection=True
        ),
        # No ownership/institution contract exists: editorial access requires history capability.
        Op.BIBLIOGRAPHIC_READ_EDITORIAL: _policy(
            K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_READ, P.METADATA_REVISION_READ, technical=True
        ),
        Op.AUTHORITY_READ_EDITORIAL: _policy(
            K.AUTHORITY, P.AUTHORITY_READ, P.METADATA_REVISION_READ, technical=True
        ),
        Op.BIBLIOGRAPHIC_CREATE: _policy(K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_CREATE, technical=True),
        Op.BIBLIOGRAPHIC_UPDATE: _policy(K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_UPDATE, technical=True),
        Op.CATALOG_WORKSPACE_VALIDATE: _policy(K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_UPDATE),
        Op.CATALOG_WORKSPACE_PUBLISH: _policy(K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_UPDATE),
        Op.BIBLIOGRAPHIC_DELETE: _policy(K.BIBLIOGRAPHIC, P.BIBLIOGRAPHIC_DELETE),
        Op.AUTHORITY_MANAGE: _policy(K.AUTHORITY, P.AUTHORITY_MANAGE, technical=True),
        Op.METADATA_REVISION_READ: _policy(K.METADATA, P.METADATA_REVISION_READ, technical=True),
        Op.METADATA_REVISION_CREATE: _policy(
            K.METADATA, P.METADATA_REVISION_CREATE, technical=True
        ),
        Op.METADATA_REVISION_RESTORE: _policy(
            K.METADATA, P.METADATA_REVISION_READ, P.METADATA_REVISION_CREATE
        ),
        Op.METADATA_AUDIT_READ: _policy(K.METADATA, P.METADATA_AUDIT_READ, technical=True),
        Op.METADATA_EDITORIAL_HISTORY: _policy(
            K.METADATA, P.METADATA_REVISION_READ, P.METADATA_AUDIT_READ, technical=True
        ),
        Op.SYSTEM_ADMIN: _policy(K.SYSTEM, P.SYSTEM_ADMIN),
        **{
            op: OperationPolicy(frozenset({permission}), frozenset({K.BIBLIOGRAPHIC, K.AUTHORITY}))
            for op, permission in (
                (Op.EDITORIAL_SUBMIT, P.EDITORIAL_SUBMIT),
                (Op.EDITORIAL_APPROVE, P.EDITORIAL_APPROVE),
                (Op.EDITORIAL_RETURN, P.EDITORIAL_RETURN),
                (Op.EDITORIAL_PUBLISH, P.EDITORIAL_PUBLISH),
                (Op.EDITORIAL_WITHDRAW, P.EDITORIAL_WITHDRAW),
                (Op.EDITORIAL_REPUBLISH, P.EDITORIAL_REPUBLISH),
                (Op.EDITORIAL_ARCHIVE, P.EDITORIAL_ARCHIVE),
            )
        },
    }
)


@dataclass(frozen=True)
class ResourceContext:
    kind: ResourceKind
    # Supplied by the resource-owning service, never by a request DTO.
    state: str | None = None
    public_revision: int | None = None
    selected_revision: int | None = None
    resource_id: UUID | None = None
