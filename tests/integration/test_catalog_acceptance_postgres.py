"""M05.07: real HTTP/PostgreSQL with signed offline OIDC, never a Keycloak claim."""

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from libris.core.config import Settings
from libris.main import create_app
from libris.modules.bibliographic.application.catalog import CatalogService
from libris.modules.bibliographic.infrastructure.rdf import BF
from libris.modules.bibliographic.infrastructure.serialization import (
    parse_representation,
)
from libris.modules.bibliographic.infrastructure.validation import (
    AUTHORITY_PROFILE_ID,
    INSTITUTIONAL_PROFILE_ID,
)
from libris.modules.identity.domain.models import actor_identifier
from libris.modules.identity.infrastructure.oidc import OIDCValidator
from rdflib import URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import RDFS
from test_catalog_http_postgres import COLLECTIONS
from test_editorial_http_postgres import command, seed
from test_editorial_postgres import state

pytestmark = pytest.mark.integration
ISSUER = "http://localhost:19999/realms/offline-test"


class SignedIssuer:
    """Ephemeral key; real signature/issuer/audience/azp validation, no network."""

    def __init__(self) -> None:
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        self.key.update(kid="acceptance", use="sig", alg="RS256")

    def document(self, request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL(ISSUER + "/.well-known/openid-configuration"):
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/keys"})
        assert request.url == httpx.URL(ISSUER + "/keys"), "Unexpected OIDC egress"
        return httpx.Response(200, json={"keys": [self.key]})

    def headers(self, *roles: str, subject: str = "acceptance-human") -> dict[str, str]:
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": subject,
                "aud": "libris-api",
                "azp": "libris-web",
                "iat": now - 1,
                "exp": now + 600,
                "typ": "Bearer",
                "resource_access": {"libris-api": {"roles": list(roles)}},
            },
            self.private,
            algorithm="RS256",
            headers={"kid": "acceptance"},
        )
        return {"Authorization": "Bearer " + token}


@asynccontextmanager
async def signed_client() -> AsyncIterator[tuple[httpx.AsyncClient, CatalogService, SignedIssuer]]:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL exige PostgreSQL isolado migrado.")
    assert url.startswith("postgresql+asyncpg://")
    issuer = SignedIssuer()
    app = create_app(
        Settings(
            _env_file=None,
            app_env="test",
            database_url=url,
            oidc_enabled=True,
            oidc_issuer_url=ISSUER,
            oidc_role_source="client",
            oidc_role_client="libris-api",
            oidc_human_client_id="libris-web",
            oidc_clock_skew_seconds=0,
        )
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.MockTransport(issuer.document)) as oidc:
            app.state.identity_service.validator = OIDCValidator(app.state.settings, oidc)
            # No get_current_actor, authorization or catalog dependency overrides.
            assert app.dependency_overrides == {}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://testserver",
                headers=issuer.headers("libris-admin"),
            ) as client:
                yield client, CatalogService(app.state.database.sessions), issuer


def assert_private_problem(response: httpx.Response, status: int, *secrets: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert "no-store" in response.headers["cache-control"]
    assert "etag" not in response.headers and "location" not in response.headers
    problem = response.json()
    assert problem["status"] == status and problem["contract_version"] == "1.0"
    assert problem["request_id"] == response.headers["x-request-id"]
    assert all(secret not in response.text for secret in secrets)


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_signed_role_matrix_and_editorial_actor(collection: str) -> None:
    async def scenario() -> None:
        async with signed_client() as (client, catalog, issuer):
            first = await seed(client, collection)
            identifier = UUID(first.json()["internal_id"])
            assert identifier.version == 4
            path = first.headers["location"]
            etag = first.headers["etag"]
            authority = collection in {"agents", "subjects"}
            for role, read, edit, audit, submit, approve, publish in [
                (None, False, False, False, False, False, False),
                ("libris-reader", False, False, False, False, False, False),
                ("libris-cataloger", True, not authority, False, True, False, False),
                ("libris-authority-manager", False, authority, False, False, False, False),
                ("libris-reviewer", True, False, True, False, True, False),
                ("libris-publisher", True, False, True, False, False, True),
                ("libris-admin", True, True, True, True, True, True),
            ]:
                client.headers.update(issuer.headers(*([role] if role else [])))
                for suffix, allowed in [
                    ("", read),
                    ("/relationships", read),
                    ("/revisions", read),
                    ("/audit", audit),
                ]:
                    response = await client.get(path + suffix)
                    assert response.status_code == (200 if allowed else 403), response.text
                # Stale precondition checks permission without changing the record.
                response = await client.patch(path, json={}, headers={"If-Match": '"stale"'})
                assert response.status_code == (412 if edit else 403), response.text
                for operation, allowed in [
                    ("submit", submit),
                    ("approve", approve),
                    ("publish", publish),
                ]:
                    response = await command(client, path, operation, etag='"stale"')
                    assert response.status_code == (412 if allowed else 403), response.text
            before = await state(catalog.sessions, identifier)
            assert before.current.number == 1 and len(before.events) == 1
            assert before.current.actor_id == actor_identifier(ISSUER, "acceptance-human")
            # Exercise distinct signed identities for the three authorized transitions.
            for operation, role in [
                ("submit", "libris-cataloger"),
                ("approve", "libris-reviewer"),
                ("publish", "libris-publisher"),
            ]:
                client.headers.update(issuer.headers(role, subject=role))
                response = await command(client, path, operation, etag=etag)
                assert response.status_code == 200, response.text
                etag = response.headers["etag"]
            client.headers.update(issuer.headers("libris-admin"))
            history = (await client.get(path + "/audit")).json()["items"]
            assert {event["actor_id"] for event in history} == {
                actor_identifier(ISSUER, subject)
                for subject in [
                    "acceptance-human",
                    "libris-cataloger",
                    "libris-reviewer",
                    "libris-publisher",
                ]
            }
            public = await client.get(path + "/public")
            assert public.json() == first.json()
            assert public.json()["uri"] == first.json()["uri"]

    asyncio.run(scenario())


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_confidentiality_all_auxiliary_paths_and_formats(collection: str) -> None:
    async def scenario() -> None:
        async with signed_client() as (client, catalog, issuer):
            first = await seed(client, collection)
            path = first.headers["location"]
            identifier = UUID(first.json()["internal_id"])
            field = (
                "variant_name"
                if collection == "agents"
                else ("alternative_label" if collection == "subjects" else "note")
            )
            secret = "UNPUBLISHED-" + str(uuid4())
            value = [{"value": secret}]
            changed = await client.patch(
                path,
                json={field: value if collection in {"agents", "subjects"} else [{"label": value}]},
                headers={"If-Match": first.headers["etag"]},
            )
            assert changed.status_code == 200, changed.text
            assert secret in changed.text
            before = await state(catalog.sessions, identifier)
            event_id = before.events[-1].event_id
            suffixes = [
                "",
                "/relationships",
                "/relationships?include_inverses=true",
                "/editorial",
                "/history",
                "/audit",
                f"/audit/{event_id}",
                "/revisions",
                "/revisions/2",
                "/revisions/2/snapshot",
                "/revisions/compare?previous=1&current=2",
            ]
            # Draft/approved are hidden, as are candidates of a published resource.
            for phase in ("draft", "approved", "candidate"):
                if phase == "approved":
                    for operation in ("submit", "approve"):
                        response = await command(client, path, operation, selected=2)
                        assert response.status_code == 200, response.text
                if phase == "candidate":
                    response = await command(client, path, "publish", selected=2)
                    assert response.status_code == 200, response.text
                    current = await client.get(path)
                    candidate = await client.patch(
                        path,
                        json={
                            field: [{"value": "CANDIDATE-" + secret}]
                            if collection in {"agents", "subjects"}
                            else [{"label": [{"value": "CANDIDATE-" + secret}]}]
                        },
                        headers={"If-Match": current.headers["etag"]},
                    )
                    assert candidate.status_code == 200, candidate.text
                for credential, expected in [(None, 401), ("reader", 403), ("empty", 403)]:
                    client.headers.pop("authorization", None)
                    if credential:
                        client.headers.update(
                            issuer.headers(*(["libris-reader"] if credential == "reader" else []))
                        )
                    for suffix in suffixes:
                        response = await client.get(path + suffix)
                        assert_private_problem(response, expected, secret, first.json()["uri"])
                    listing = await client.get(
                        f"/api/v1/catalog/{collection}", params={"internal_id": str(identifier)}
                    )
                    assert_private_problem(listing, expected, secret)
                    for media in ("application/json", "application/ld+json", "text/turtle"):
                        public = await client.get(path + "/public", headers={"Accept": media})
                        if phase != "candidate":
                            assert_private_problem(public, 404, secret, first.json()["uri"])
                        else:
                            assert public.status_code == 200
                            assert "CANDIDATE-" not in public.text
                            assert "actor_id" not in public.text
                    page = await client.get(
                        f"/api/v1/catalog/public/{collection}",
                        params={"internal_id": str(identifier)},
                    )
                    assert page.status_code == 200
                    assert "CANDIDATE-" not in page.text
                    if phase != "candidate":
                        assert page.json()["items"] == [] and secret not in page.text
                client.headers.update(issuer.headers("libris-admin"))
            # Denied reads produced no writes; only the explicit transitions/patch did.
            after = await state(catalog.sessions, identifier)
            assert after.current.number == 3 and len(after.events) == len(before.events) + 4
            assert after.state.public_revision == 2

    asyncio.run(scenario())


def test_http_shared_authorities_preserve_owners_and_atomic_link_batch() -> None:
    async def scenario() -> None:
        async with signed_client() as (client, catalog, _issuer):
            agent, subject = await seed(client, "agents"), await seed(client, "subjects")
            first, second = await seed(client), await seed(client)

            # Reuse institutional DTO fixtures for authorities; writes and reads are HTTP.
            def target(response: httpx.Response, kind: str) -> dict[str, str]:
                return {
                    "kind": "local",
                    "entity_type": kind,
                    "internal_id": response.json()["internal_id"],
                    "uri": response.json()["uri"],
                }

            patch = {
                "contribution": [
                    {
                        "agent": target(agent, "Agent"),
                        "role": [{"uri": "http://id.loc.gov/vocabulary/relators/aut"}],
                    }
                ],
                "subject": [target(subject, "Subject")],
            }
            originals = []
            for work in [first, second]:
                response = await client.patch(
                    work.headers["location"], json=patch, headers={"If-Match": work.headers["etag"]}
                )
                assert response.status_code == 200, response.text
                originals.append(response)
                graph = parse_representation(
                    (
                        await client.get(
                            work.headers["location"], headers={"Accept": "text/turtle"}
                        )
                    ).text,
                    "turtle",
                )
                assert not list(graph.triples((URIRef(agent.json()["uri"]), None, None)))
                assert not list(graph.triples((URIRef(subject.json()["uri"]), None, None)))
                assert (
                    URIRef(work.json()["uri"]),
                    BF.subject,
                    URIRef(subject.json()["uri"]),
                ) in graph
            path = first.headers["location"]
            before = await state(catalog.sessions, UUID(first.json()["internal_id"]))
            relation = {
                "kind": "direct",
                "source": target(first, "Work"),
                "target": target(second, "Work"),
                "predicate": str(BF.relatedTo),
            }
            current = await client.get(path + "/relationships")
            failed = await client.patch(
                path + "/relationships",
                json={
                    "operations": [
                        {"op": "add", "links": [relation]},
                        {
                            "op": "add",
                            "links": [
                                relation
                                | {
                                    "target": {
                                        "kind": "local",
                                        "entity_type": "Work",
                                        "internal_id": str(uuid4()),
                                    }
                                }
                            ],
                        },
                    ]
                },
                headers={"If-Match": current.headers["etag"]},
            )
            assert failed.status_code == 422, failed.text
            assert await state(catalog.sessions, UUID(first.json()["internal_id"])) == before
            assert (await client.get(path + "/relationships")).json() == current.json()
            # Removing shared links never deletes or revises their authority.
            removed = await client.patch(
                path,
                json={"contribution": [], "subject": []},
                headers={"If-Match": originals[0].headers["etag"]},
            )
            assert removed.status_code == 200, removed.text
            for resource in [agent, subject, originals[1]]:
                recovered = await client.get(
                    resource.headers.get("location") or second.headers["location"]
                )
                assert recovered.json() == resource.json()
                assert recovered.headers["etag"] == resource.headers["etag"]
            # Authority edits do not rewrite the referring Work or its historical snapshot.
            old = await client.get(second.headers["location"] + "/revisions/2/snapshot")
            updated = await client.patch(
                agent.headers["location"],
                json={"authorized_name": [{"value": "Updated authority", "language": "pt"}]},
                headers={"If-Match": agent.headers["etag"]},
            )
            assert updated.status_code == 200, updated.text
            new = await client.get(second.headers["location"] + "/revisions/2/snapshot")
            assert isomorphic(
                parse_representation(old.text, "turtle"), parse_representation(new.text, "turtle")
            )
            graph = parse_representation(
                (
                    await client.get(agent.headers["location"], headers={"Accept": "text/turtle"})
                ).text,
                "turtle",
            )
            assert str(next(graph.objects(URIRef(agent.json()["uri"]), RDFS.label))) == (
                "Updated authority"
            )

    asyncio.run(scenario())


def test_m0706_end_to_end_cataloging_chain_and_published_revision() -> None:
    """Exercise one connected cataloging flow through signed HTTP and PostgreSQL."""

    async def scenario() -> None:
        async with signed_client() as (client, catalog, issuer):
            agent = await seed(client, "agents")
            subject = await seed(client, "subjects")

            def local(response: httpx.Response, entity_type: str) -> dict[str, str]:
                return {
                    "kind": "local",
                    "entity_type": entity_type,
                    "internal_id": response.json()["internal_id"],
                    "uri": response.json()["uri"],
                }

            work_payload = {
                "title": [
                    {
                        "main_title": [
                            {"value": "Fluxo M07.06", "language": "pt"},
                            {"value": "M07.06 flow", "language": "en"},
                        ]
                    },
                    {
                        "kind": "VariantTitle",
                        "main_title": [{"value": "Homologação técnica", "language": "pt"}],
                    },
                ],
                "language": [{"uri": "http://id.loc.gov/vocabulary/languages/por"}],
                "contribution": [
                    {
                        "agent": local(agent, "Agent"),
                        "role": [
                            {"uri": "http://id.loc.gov/vocabulary/relators/aut"},
                            {"uri": "http://id.loc.gov/vocabulary/relators/edt"},
                        ],
                    }
                ],
                "subject": [local(subject, "Subject")],
                "note": [
                    {
                        "label": [
                            {"value": "Nota estruturada", "language": "pt"},
                            {"value": "Structured note", "language": "en"},
                        ],
                        "note_type": ["summary", "bibliography"],
                    }
                ],
            }
            work = await client.post("/api/v1/catalog/works", json=work_payload)
            assert work.status_code == 201, work.text
            instance = await client.post(
                "/api/v1/catalog/instances",
                json={
                    "instance_of": local(work, "Work"),
                    "edition_statement": [
                        {"value": "Primeira edição", "language": "pt"},
                        {"value": "First edition", "language": "en"},
                    ],
                    "identified_by": [{"kind": "Isbn", "value": "9780000000066"}],
                    "provision_activity": [
                        {
                            "agent": [local(agent, "Agent")],
                            "place": [{"uri": "urn:libris:test:manaus"}],
                            "date": [
                                {
                                    "value": "2026",
                                    "datatype": "http://www.w3.org/2001/XMLSchema#gYear",
                                }
                            ],
                        }
                    ],
                },
            )
            assert instance.status_code == 201, instance.text
            item = await client.post(
                "/api/v1/catalog/items",
                json={
                    "item_of": local(instance, "Instance"),
                    "identified_by": [{"kind": "Local", "value": "M0706-ITEM-1"}],
                    "custodial_history": [
                        {"value": "Doação para ensaio técnico", "language": "pt"}
                    ],
                    "note": [{"label": [{"value": "Item sem circulação", "language": "pt"}]}],
                },
            )
            assert item.status_code == 201, item.text

            resources = [(work, "Work"), (instance, "Instance"), (item, "Item")]
            identities = {UUID(response.json()["internal_id"]) for response, _ in resources}
            uris = {response.json()["uri"] for response, _ in resources}
            assert len(identities) == len(uris) == 3
            for response, _ in resources:
                recovered = await client.get(response.headers["location"])
                assert recovered.json() == response.json()
                observed = await state(catalog.sessions, UUID(response.json()["internal_id"]))
                assert observed.current.number == 1
                assert observed.state.current_revision == 1
                assert observed.state.published_revision is None
                hidden = await client.get(response.headers["location"] + "/public")
                assert hidden.status_code == 404

            work_relations = await client.get(
                work.headers["location"] + "/relationships?include_inverses=true"
            )
            instance_relations = await client.get(
                instance.headers["location"] + "/relationships?include_inverses=true"
            )
            assert any(
                link["target"].get("internal_id") == instance.json()["internal_id"]
                for link in work_relations.json()["links"]
            )
            assert any(
                link["target"].get("internal_id") == item.json()["internal_id"]
                for link in instance_relations.json()["links"]
            )

            for response, _ in [(agent, "Agent"), (subject, "Subject"), *resources]:
                path = response.headers["location"]
                selected = (await client.get(path + "/editorial")).json()["current_revision"]
                for operation, role in [
                    ("submit", "libris-cataloger"),
                    ("approve", "libris-reviewer"),
                    ("publish", "libris-publisher"),
                ]:
                    client.headers.update(issuer.headers(role, subject=f"m0706-{role}"))
                    transitioned = await command(client, path, operation, selected=selected)
                    assert transitioned.status_code == 200, transitioned.text
                public = await client.get(path + "/public")
                assert public.status_code == 200 and public.json() == response.json()

            client.headers.update(issuer.headers("libris-cataloger", subject="m0706-editor"))
            current = await client.get(work.headers["location"])
            candidate = await client.patch(
                work.headers["location"],
                headers={"If-Match": current.headers["etag"]},
                json={"note": [{"label": [{"value": "CANDIDATE-M0706"}]}]},
            )
            assert candidate.status_code == 200, candidate.text
            editorial = (await client.get(work.headers["location"] + "/editorial")).json()
            assert editorial["current_revision"] == editorial["candidate_revision"] == 2
            assert editorial["published_revision"] == editorial["public_revision"] == 1
            public = await client.get(work.headers["location"] + "/public")
            assert public.status_code == 200
            assert public.json() == work.json() and "CANDIDATE-M0706" not in public.text
            historical = parse_representation(
                (await client.get(work.headers["location"] + "/revisions/1/snapshot")).text,
                "turtle",
            )
            published = parse_representation(
                (
                    await client.get(
                        work.headers["location"] + "/public",
                        headers={"Accept": "text/turtle"},
                    )
                ).text,
                "turtle",
            )
            assert isomorphic(historical, published)

    asyncio.run(scenario())


@pytest.mark.parametrize("collection", COLLECTIONS)
def test_validation_missing_references_and_no_physical_delete(collection: str) -> None:
    async def scenario() -> None:
        async with signed_client() as (client, catalog, _issuer):
            first = await seed(client, collection)
            path, identifier = first.headers["location"], UUID(first.json()["internal_id"])
            before = await state(catalog.sessions, identifier)
            data = {
                key: value
                for key, value in first.json().items()
                if key not in {"internal_id", "uri"}
            }
            validation = await client.post(
                f"/api/v1/catalog/{collection}/validation",
                json={
                    "kind": "dto",
                    "resource": data,
                    "profile_release": AUTHORITY_PROFILE_ID
                    if collection in {"agents", "subjects"}
                    else INSTITUTIONAL_PROFILE_ID,
                },
            )
            assert validation.status_code == 200, validation.text
            assert validation.json()["conforms"] is True
            field, kind = {
                "works": ("related_to", "Work"),
                "instances": ("instance_of", "Work"),
                "items": ("item_of", "Instance"),
                "agents": ("related_authority", "Agent"),
                "subjects": ("broader", "Subject"),
            }[collection]
            missing = {"kind": "local", "entity_type": kind, "internal_id": str(uuid4())}
            invalid = await client.patch(
                path,
                json={
                    field: missing if collection in {"instances", "items"} else [missing],
                },
                headers={"If-Match": first.headers["etag"]},
            )
            assert invalid.status_code == 422 and invalid.json()["code"] == "reference.invalid"
            removed = await client.delete(path)
            assert removed.status_code == 405
            assert set(removed.headers["allow"].split(", ")) <= {"GET", "PATCH"}
            assert "DELETE" not in removed.headers["allow"]
            # Independent sessions observe no revision/state/event changes from any rejection.
            assert await state(catalog.sessions, identifier) == before
            recovered = await client.get(path)
            assert recovered.json() == first.json()
            assert recovered.headers["etag"] == first.headers["etag"]

    asyncio.run(scenario())
