"""Read package-owned profile artifacts only; callers cannot supply paths or URLs."""

import json
from hashlib import sha256
from importlib.resources import files
from typing import Annotated

from pydantic import Field, TypeAdapter

from libris.modules.bibliographic.domain.profile_contract import (
    IRI,
    Contract,
    ProfileDefinition,
    Version,
)
from libris.modules.bibliographic.domain.profile_registry import (
    ProfileError,
    ProfileRegistry,
    SemanticRegistry,
    ShapeRegistry,
    profile_fingerprint,
)
from libris.modules.bibliographic.domain.profiles import CatalogProfile
from libris.modules.bibliographic.domain.vocabularies import (
    CONTROLLED_VOCABULARY_PROFILE_ID,
)

LEGACY_PROFILES = {
    "urn:libris:profile:monograph:v1": "monograph-v1.json",
    "urn:libris:profile:monograph:v1:1": "monograph-v1.1.json",
    CONTROLLED_VOCABULARY_PROFILE_ID: "monograph-v1.2.json",
    "urn:libris:profile:authority:v1": "authority-v1.json",
}


class ProfileSemanticRelease(Contract):
    """Trusted package manifest association, independent of historical profile format."""

    profile_file: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.json$", strict=True)]
    profile_release: IRI
    semantic_release: Version


def _read(name: str) -> str:
    return files("libris.modules.bibliographic").joinpath("resources", name).read_text("utf-8")


def load_legacy_profile(release_id: str) -> CatalogProfile:
    """v1 view fills absent descriptor metadata only, without claiming extra SHACL rules."""
    try:
        name = LEGACY_PROFILES[release_id]
    except KeyError as error:
        raise ValueError("Perfil legado local desconhecido.") from error
    if name != "monograph-v1.json":
        return CatalogProfile.model_validate_json(_read(name))
    data = json.loads(_read(name))
    data.update(release="1.0", status="candidate")
    for resource in data["resources"]:
        resource["label"] = {"pt": resource["class"].rsplit("/", 1)[-1]}
        for field in resource["fields"]:
            field["name"] = field["property"].rsplit("/", 1)[-1].rsplit("#", 1)[-1]
            field["nodeKind"] = (
                "Literal"
                if field["valueKind"] == "literal"
                else (
                    "BlankNodeOrIRI"
                    if field.get("targetClasses")
                    and not field["property"].endswith(
                        ("instanceOf", "itemOf", "hasInstance", "hasItem")
                    )
                    else "IRI"
                )
            )
            field["minCount"] = int(field["requiredInForm"])
            field["maxCount"] = None if field["repeatable"] else 1
            field["absent"] = "violation" if field["requiredInForm"] else "omit"
            if field.get("datatypes") is True:
                field["datatypes"] = [
                    "http://www.w3.org/2001/XMLSchema#string",
                    "http://www.w3.org/2001/XMLSchema#gYear",
                    "http://www.w3.org/2001/XMLSchema#date",
                ]
            field["validation"] = "Descritor legado; consultar shapes originais para regras RDF."
    return CatalogProfile.model_validate(data)


def load_profile_registry() -> ProfileRegistry:
    """Manifest entries are authored/trusted package data, never user configuration."""
    manifest: list[str] = json.loads(_read("profiles-v2/manifest.json"))
    definitions = [
        ProfileDefinition.model_validate_json(_read(f"profiles-v2/{name}")) for name in manifest
    ]
    associations = TypeAdapter(tuple[ProfileSemanticRelease, ...]).validate_json(
        _read("profiles-v2/semantic-profile-releases.json")
    )
    semantics_by_release: dict[str, SemanticRegistry] = {}
    historical = load_semantic_registry("1.0")
    for association in associations:
        definition = ProfileDefinition.model_validate_json(
            _read(f"profiles-v2/{association.profile_file}")
        )
        if definition.release_id != association.profile_release:
            raise ProfileError("incompatible_semantics", "Associação aponta a outra release.")
        extension = load_semantic_registry(association.semantic_release)
        extension.check_extension_of(historical)
        definitions.append(definition)
        semantics_by_release[definition.release_id] = extension
    fingerprints: dict[str, str] = json.loads(_read("profiles-v2/release-lock.json"))
    if not fingerprints.keys() <= {definition.release_id for definition in definitions}:
        raise ProfileError("missing_release", "Release fixada foi removida do manifest.")
    for definition in definitions:
        expected = fingerprints.get(definition.release_id)
        if (expected is None and definition.status != "draft") or (
            expected is not None and expected != profile_fingerprint(definition)
        ):
            raise ProfileError("immutable_release", "Fingerprint de release ausente ou divergente.")
    registry = ProfileRegistry(
        definitions,
        historical,
        ShapeRegistry.model_validate_json(_read("profiles-v2/shapes-v1.json")),
        used_releases=fingerprints,
        semantics_by_release=semantics_by_release,
    )
    for release_id in fingerprints:
        if not set(registry.resolve(release_id).lineage) <= fingerprints.keys():
            raise ProfileError("unlocked_dependency", "Base de release fixada exige fingerprint.")
    return registry


SEMANTIC_RELEASE_FILES = {
    "1.0": "profiles-v2/semantics-v1.json",
    "2.0": "profiles-v2/semantics-v2.json",
}


def load_semantic_registry(release: str) -> SemanticRegistry:
    """Only pinned local releases; no path, ontology or context supplied by clients."""
    try:
        name = SEMANTIC_RELEASE_FILES[release]
    except KeyError as error:
        raise ProfileError("unknown_semantics", "Release semântica desconhecida.") from error
    document = _read(name)
    locks: dict[str, str] = json.loads(_read("profiles-v2/semantic-release-lock.json"))
    if locks.get(release) != sha256(document.encode("utf-8")).hexdigest():
        raise ProfileError("immutable_semantics", "Conteúdo da release semântica divergente.")
    registry = SemanticRegistry.model_validate_json(document)
    if registry.version != release:
        raise ProfileError("incompatible_semantics", "Identidade da release semântica divergente.")
    return registry
