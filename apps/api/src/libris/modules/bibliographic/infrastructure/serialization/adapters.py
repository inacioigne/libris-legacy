"""Create and controlled partial Update over complete owned snapshots."""

from rdflib import Graph, URIRef
from rdflib.namespace import RDF

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
from libris.modules.bibliographic.application.diagnostics import CatalogValidationRejected
from libris.modules.bibliographic.application.validation import validate_catalog
from libris.modules.bibliographic.domain.errors import CatalogCode, CatalogError
from libris.modules.bibliographic.domain.models import ResourceIdentity
from libris.modules.bibliographic.domain.profile_contract import ResolvedProfile
from libris.modules.bibliographic.infrastructure.ownership import check_snapshot, owned_nodes
from libris.modules.bibliographic.infrastructure.profiles import (
    load_legacy_profile,
    load_profile_registry,
    load_semantic_registry,
)
from libris.modules.bibliographic.infrastructure.rdf import RdfDocumentError, new_graph

from .mapping import ReferenceMapping, ResourceRead, resource_to_graph
from .projection import GraphProjection

ResourceCreate = WorkCreate | InstanceCreate | ItemCreate | AgentCreate | SubjectCreate
ResourceUpdate = WorkUpdate | InstanceUpdate | ItemUpdate | AgentUpdate | SubjectUpdate
CREATE_READ: dict[type[ResourceCreate], type[ResourceRead]] = {
    WorkCreate: WorkRead,
    InstanceCreate: InstanceRead,
    ItemCreate: ItemRead,
    AgentCreate: AgentRead,
    SubjectCreate: SubjectRead,
}
READ_UPDATE: dict[type[ResourceRead], type[ResourceUpdate]] = {
    WorkRead: WorkUpdate,
    InstanceRead: InstanceUpdate,
    ItemRead: ItemUpdate,
    AgentRead: AgentUpdate,
    SubjectRead: SubjectUpdate,
}


def _identity(identity: ResourceIdentity, references: ReferenceMapping | None) -> None:
    # UUID/URI association cannot be proved from RDF. Check supplied evidence.
    if references is not None:
        for key in (identity.internal_id, identity.uri):
            if key in references and references[key].identity != identity:
                raise RdfDocumentError("Identidade raiz diverge da identidade armazenada.")


def _validate(graph: Graph, identity: ResourceIdentity, profile: str | ResolvedProfile) -> None:
    kind = check_snapshot(graph, URIRef(identity.uri))
    descriptor = load_legacy_profile(profile) if isinstance(profile, str) else profile
    if str(kind) not in {resource.rdf_class for resource in descriptor.resources}:
        raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Tipo não suportado pelo perfil.")
    if isinstance(profile, ResolvedProfile):
        entity = next(r.entity for r in profile.resources if r.rdf_class == str(kind))
        if entity not in profile.applies_to:
            raise CatalogError(CatalogCode.PROFILE_INCOMPATIBLE, "Perfil inaplicável à entidade.")
    result = validate_catalog(graph, profile)
    if result.state == "failed":
        raise CatalogError(result.issues[0].code, "Validação indisponível.")
    if result.conforms is not True:
        raise CatalogValidationRejected(result)


def _authorize(
    graph: Graph, root: URIRef, predicates: set[URIRef], profile: str | ResolvedProfile
) -> None:
    # Preserving an old statement is distinct from authorizing a newly written one.
    semantics = (
        load_semantic_registry("1.0")
        if isinstance(profile, str)
        else load_profile_registry().semantic_registry_for(profile.release_id)
    )
    classes = {str(value) for value in graph.objects(root, RDF.type)}
    descriptor = load_legacy_profile(profile) if isinstance(profile, str) else profile
    allowed = {
        field.property
        for resource in descriptor.resources
        if resource.rdf_class in classes
        for field in resource.fields
        if not field.derived
    }
    for predicate in predicates:
        definitions = [p for p in semantics.properties if p.iri == str(predicate)]
        if (
            str(predicate) not in allowed
            or len(definitions) != 1
            or definitions[0].status != "supported"
            or not (classes & set(definitions[0].sources))
            or definitions[0].derived
        ):
            raise CatalogError(
                CatalogCode.PROFILE_POLICY, "Propriedade não autorizada para escrita."
            )
        namespace = semantics.namespace_for(str(predicate))
        if namespace.status != "supported" or namespace.approval == "pending":
            raise CatalogError(CatalogCode.PROFILE_POLICY, "Namespace não autorizado para escrita.")


def prepare_create_graph(
    resource: ResourceCreate,
    identity: ResourceIdentity,
    *,
    profile: str | ResolvedProfile,
    references: ReferenceMapping | None = None,
) -> Graph:
    """Prepara estrutura, identidade e política; SHACL é executado pelo chamador."""
    model = CREATE_READ.get(type(resource))
    if model is None:
        raise RdfDocumentError("A conversão exige um dos cinco contratos Create.")
    _identity(identity, references)
    resource = type(resource).model_validate(resource.model_dump())
    read = model.model_validate(
        resource.model_dump()
        | {
            "internal_id": identity.internal_id,
            "uri": identity.uri,
        }
    )
    graph = resource_to_graph(read, references=references)
    _authorize(
        graph,
        URIRef(identity.uri),
        {
            p
            for p in graph.predicates(URIRef(identity.uri))
            if isinstance(p, URIRef) and p != RDF.type
        },
        profile,
    )
    return graph


def create_to_graph(
    resource: ResourceCreate,
    identity: ResourceIdentity,
    *,
    profile: str | ResolvedProfile,
    references: ReferenceMapping | None = None,
) -> Graph:
    graph = prepare_create_graph(resource, identity, profile=profile, references=references)
    _validate(graph, identity, profile)
    return graph


def apply_update(
    graph: Graph,
    update: ResourceUpdate,
    identity: ResourceIdentity,
    *,
    profile: str | ResolvedProfile,
    references: ReferenceMapping | None = None,
) -> Graph:
    """Copy, replace present fields, preserve independent RDF, then validate.

    Exclusive auxiliaries with non-projectable statements cannot be replaced by
    a DTO safely. Reject that edit instead of silently dropping those statements.
    """
    _identity(identity, references)
    projection = GraphProjection(graph, identity)
    if type(update) is not READ_UPDATE[type(projection.resource)]:
        raise RdfDocumentError("Update incompatível com o tipo armazenado.")
    update = type(update).model_validate(update.model_dump(exclude_unset=True))
    patch = update.model_dump(exclude_unset=True)
    data = projection.resource.model_dump()
    for name, value in patch.items():
        data[name] = [] if value is None else value
    complete = type(projection.resource).model_validate(data)
    replacement = resource_to_graph(complete, references=references)
    predicates = {projection.fields[name] for name in patch}
    _authorize(graph, projection.root, predicates, profile)
    result = new_graph()
    for prefix, namespace in graph.namespaces():
        result.bind(prefix, namespace)
    for triple in graph:
        result.add(triple)
    previous_nodes = owned_nodes(graph, projection.root)
    for predicate in predicates:
        result.remove((projection.root, predicate, None))
    orphaned = previous_nodes - owned_nodes(result, projection.root)
    for node in orphaned:
        if isinstance(node, URIRef):
            raise RdfDocumentError(
                "Substituição de auxiliar apagaria URI não representável no DTO."
            )
        if any(triple not in projection.consumed for triple in graph.triples((node, None, None))):
            raise RdfDocumentError(
                "Substituição de auxiliar apagaria conteúdo fora da projeção DTO."
            )
        result.remove((node, None, None))
    # v1 DTO structures are one level deep; only copy selected auxiliary nodes.
    selected = new_graph()
    for predicate in predicates:
        for triple in replacement.triples((projection.root, predicate, None)):
            selected.add(triple)
    for node in owned_nodes(replacement, projection.root) - {projection.root}:
        if any((projection.root, predicate, node) in selected for predicate in predicates):
            for triple in replacement.triples((node, None, None)):
                selected.add(triple)
    for triple in selected:
        result.add(triple)
    _validate(result, identity, profile)
    return result
