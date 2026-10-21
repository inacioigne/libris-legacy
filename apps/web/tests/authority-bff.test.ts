import assert from "node:assert/strict";
import { test } from "node:test";
import { CatalogAccessError } from "../src/server/catalog";
import { changeAuthorityRelation, createAgent, createSubject, getAgentForEditor, getAuthority, getAuthorityRelations, getSubjectForEditor, listAuthorities, updateAgent, updateSubject } from "../src/server/catalog/authorities";
import { authorityReference, referenceKey, sameLink, selectedLink } from "../src/catalog/authorities";
import { AuthService } from "../src/server/auth/service";
import { OpenIdProvider } from "../src/server/auth/oidc";
import { randomId } from "../src/server/auth/security";
import { config, MemoryStore, json } from "./helpers";
import { agent, subject, initialRelations } from "./authority-fixture";

async function fixture(permissions = ["bibliographic:read", "bibliographic:update", "authority:read", "metadata_revision:read"]) {
  const cfg = config(), store = new MemoryStore(), sessionId = randomId();
  const service = new AuthService(cfg, store, new OpenIdProvider(cfg), Date.now, async () => json({ actor_id: "oidc:sha256:synthetic", roles: [], permissions }));
  await store.createSession(sessionId, { accessToken: "synthetic-secret", subject: "synthetic", csrf: randomId(), accessExpiresAt: Date.now() + 300_000, expiresAt: Date.now() + 300_000 });
  return { service, sessionId };
}
const link = selectedLink(initialRelations.relations.source, agent, ["urn:role:author", "urn:role:editor"]);
const id = initialRelations.relations.source.internal_id!;

test("Agent creation uses generated POST, exact payload, server Bearer and manage-only permission", async t => {
  const options = await fixture(["authority:manage"]);
  const payload = { kind: "Person" as const, authorized_name: [{ value: "Alex", language: "pt" }],
    variant_name: [{ value: "Alexandre", language: "pt" }], identified_by: [{ kind: "Local" as const, value: "AUTH-1", source: [{ uri: "urn:source:local" }] }],
    provenance: [{ uri: "urn:source:test" }], external_reference: [{ uri: "urn:external:1" }], related_authority: [{ uri: "urn:agent:2" }] };
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    calls++;
    assert.equal(request.method, "POST");
    assert.equal(new URL(request.url).pathname, "/api/v1/catalog/agents");
    assert.equal(request.headers.get("authorization"), "Bearer synthetic-secret");
    assert.equal(request.cache, "no-store");
    assert.ok(request.headers.get("x-request-id"));
    assert.equal(request.headers.get("if-match"), null);
    assert.deepEqual(await request.json(), payload);
    return new Response(JSON.stringify({ ...agent, ...payload }), { status: 201, headers: { "Content-Type": "application/json" } });
  });
  const saved = await createAgent(payload, options);
  assert.equal(saved.internal_id, agent.internal_id);
  assert.equal(saved.uri, agent.uri);
  assert.doesNotMatch(JSON.stringify(saved), /synthetic-secret|Bearer|csrf/);
  assert.equal(calls, 1);
});

test("Agent creation rejects cataloger and missing session before POST", async t => {
  t.mock.method(globalThis, "fetch", async () => { throw new Error("Unexpected POST"); });
  const payload = { kind: "Person" as const, authorized_name: [{ value: "Alex" }] };
  const options = await fixture();
  await assert.rejects(createAgent(payload, options), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
  await assert.rejects(createAgent(payload, { ...options, sessionId: "missing" }), (e: unknown) => e instanceof CatalogAccessError && e.status === 401);
});

for (const status of [401, 403, 409, 422, 503]) test(`Agent POST preserves HTTP ${status} and never retries`, async t => {
  const options = await fixture(["authority:manage"]);
  let calls = 0;
  const problem = { status, code: "schema.invalid", issues: [{ message: "Invalid name" }] };
  t.mock.method(globalThis, "fetch", async () => {
    calls++;
    return new Response(JSON.stringify(problem), { status, headers: { "Content-Type": "application/problem+json" } });
  });
  await assert.rejects(createAgent({ kind: "Person", authorized_name: [{ value: "Alex" }] }, options), (e: unknown) => e instanceof CatalogAccessError && e.status === status && e.detail !== undefined);
  assert.equal(calls, 1);
});

test("Agent editor reads and updates through the generated endpoints with an exact ETag", async t => {
  const options = await fixture(["authority:manage", "authority:read", "metadata_revision:read"]);
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request.clone());
    const updated = { ...agent, authorized_name: [{ value: request.method === "PATCH" ? "Alex updated" : "Alex" }] };
    return new Response(JSON.stringify(updated), { headers: { "Content-Type": "application/json", ETag: request.method === "PATCH" ? '"r2"' : '"r1"' } });
  });
  const loaded = await getAgentForEditor(agent.internal_id, options);
  assert.equal(loaded.etag, '"r1"');
  const saved = await updateAgent(agent.internal_id, { authorized_name: [{ value: "Alex updated" }] }, { ...options, ifMatch: loaded.etag! });
  assert.equal(saved.agent.authorized_name[0].value, "Alex updated");
  assert.equal(saved.etag, '"r2"');
  assert.equal(requests[1].method, "PATCH");
  assert.equal(requests[1].headers.get("if-match"), '"r1"');
  assert.deepEqual(await requests[1].json(), { authorized_name: [{ value: "Alex updated" }] });
  await assert.rejects(updateAgent(agent.internal_id, {}, options), (error: unknown) => error instanceof CatalogAccessError && error.status === 428);
  assert.equal(requests.length, 2);
});

test("Subject create/read/update use authority permissions and exact ETags", async t => {
  const options = await fixture(["authority:manage", "authority:read", "metadata_revision:read"]), requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => { requests.push(request.clone()); return new Response(JSON.stringify(subject), { status: request.method === "POST" ? 201 : 200, headers: { "Content-Type": "application/json", ETag: request.method === "PATCH" ? '"s2"' : '"s1"' } }); });
  const payload = { preferred_label: [{ value: "Libraries" }], scheme: [{ uri: "urn:scheme:test" }] };
  await createSubject(payload, options);
  const loaded = await getSubjectForEditor(subject.internal_id, options); assert.equal(loaded.etag, '"s1"');
  const saved = await updateSubject(subject.internal_id, { alternative_label: [{ value: "Library science" }] }, { ...options, ifMatch: loaded.etag! });
  assert.equal(saved.etag, '"s2"'); assert.equal(requests[2].headers.get("if-match"), '"s1"');
  assert.deepEqual(await requests[2].json(), { alternative_label: [{ value: "Library science" }] });
});

test("Authority listing uses both generated endpoints, supported exact filters and opaque cursors", async t => {
  const options = await fixture(), requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request); assert.equal(request.cache, "no-store");
    assert.equal(request.headers.get("authorization"), "Bearer synthetic-secret");
    return json({ items: [request.url.includes("agents") ? agent : subject], next_cursor: "opaque" });
  });
  assert.deepEqual((await listAuthorities("Agent", { uri: agent.uri, cursor: "opaque" }, options)).items, [agent]);
  assert.deepEqual((await listAuthorities("Subject", { internal_id: subject.internal_id }, options)).items, [subject]);
  assert.equal(new URL(requests[0].url).searchParams.get("uri"), agent.uri);
  assert.equal(new URL(requests[0].url).searchParams.get("cursor"), "opaque");
  assert.equal(new URL(requests[1].url).pathname, "/api/v1/catalog/subjects");
});

test("Link/unlink preserves exact roles and identity, changes only source and uses returned ETag", async t => {
  const options = await fixture(), requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request.clone());
    return new Response(JSON.stringify(request.url.endsWith("/relationships") ? { ...initialRelations.relations, links: [link] } : agent), { headers: { "Content-Type": "application/json", ETag: '"r2"' } });
  });
  const result = await changeAuthorityRelation("works", id, "add", link, { ...options, ifMatch: '"r1"' });
  assert.equal(result.etag, '"r2"');
  assert.equal(result.labels[agent.uri], "Alex");
  assert.equal(requests[0].headers.get("if-match"), '"r1"');
  assert.deepEqual(await requests[0].json(), { operations: [{ op: "add", links: [link] }] });
  await changeAuthorityRelation("works", id, "remove", link, { ...options, ifMatch: result.etag! });
  const writes = requests.filter(request => request.method === "PATCH");
  assert.equal(writes.length, 2);
  assert.ok(writes.every(request => request.url.endsWith(`/works/${id}/relationships`)));
  assert.deepEqual(await writes[1].json(), { operations: [{ op: "remove", links: [link] }] });
  assert.doesNotMatch(JSON.stringify(result), /synthetic-secret|Bearer|csrf/);
});

for (const status of [400, 401, 403, 404, 409, 412, 422]) test(`Relations BFF retains HTTP ${status}, diagnostics and never retries writes`, async t => {
  const options = await fixture(); let calls = 0;
  const problem = { status, code: "reference.invalid", issues: [{ code: "reference.invalid", message: "Invalid reference" }] };
  t.mock.method(globalThis, "fetch", async () => { calls++; return new Response(JSON.stringify(problem), { status, headers: { "Content-Type": "application/problem+json" } }); });
  await assert.rejects(changeAuthorityRelation("works", id, "add", link, { ...options, ifMatch: '"r1"' }), error => {
    assert.ok(error instanceof CatalogAccessError); assert.equal(error.status, status); assert.deepEqual(error.detail, problem); return true;
  });
  assert.equal(calls, 1);
});

test("BFF denies missing session, missing conjunctive read permission, and authority-manager cannot link", async t => {
  let calls = 0; t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("unexpected request"); });
  const manager = await fixture(["authority:manage", "authority:read", "bibliographic:read"]);
  await assert.rejects(listAuthorities("Agent", {}, manager), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
  await assert.rejects(changeAuthorityRelation("works", id, "add", link, { ...manager, ifMatch: '"r1"' }), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
  await assert.rejects(getAuthority("Subject", subject.internal_id, { ...manager, sessionId: "missing" }), (e: unknown) => e instanceof CatalogAccessError && e.status === 401);
  await assert.rejects(changeAuthorityRelation("works", id, "add", link, await fixture()), (e: unknown) => e instanceof CatalogAccessError && e.status === 428);
  assert.equal(calls, 0);
});

test("External references are never fetched; unavailable local labels do not erase links", async t => {
  const options = await fixture(); const paths: string[] = [];
  const external = { ...link, target: { kind: "external" as const, entity_type: "Agent" as const, uri: "https://external.invalid/agent" } };
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    paths.push(request.url);
    return request.url.endsWith("/relationships") ? new Response(JSON.stringify({ ...initialRelations.relations, links: [link, external] }), { headers: { "Content-Type": "application/json", ETag: '"r1"' } }) : new Response("{}", { status: 403 });
  });
  const result = await getAuthorityRelations("works", id, options);
  assert.deepEqual(result.relations.links, [link, external]); assert.deepEqual(result.labels, {});
  assert.equal(paths.length, 2); assert.ok(paths.every(path => !path.includes("external.invalid")));
});

test("Semantic helpers retain structured identities, compare role sets and enforce entity matrix", () => {
  assert.deepEqual(authorityReference(agent), { kind: "local", entity_type: "Agent", internal_id: agent.internal_id, uri: agent.uri });
  assert.ok(sameLink(link, selectedLink(initialRelations.relations.source, agent, ["urn:role:editor", "urn:role:author"])));
  assert.ok(!sameLink(link, selectedLink(initialRelations.relations.source, agent, ["urn:role:author"])));
  const other = { ...agent, internal_id: "different", uri: "urn:other" };
  assert.ok(!sameLink(link, selectedLink(initialRelations.relations.source, other, ["urn:role:author", "urn:role:editor"])));
  assert.equal(selectedLink(initialRelations.relations.source, subject, []).predicate, "http://id.loc.gov/ontologies/bibframe/subject");
  assert.throws(() => selectedLink({ ...initialRelations.relations.source, entity_type: "Item" }, agent, []));
  assert.throws(() => selectedLink({ ...initialRelations.relations.source, entity_type: "Instance" }, subject, [], "activity"));
  assert.equal(referenceKey({ kind: "unreconciled", entity_type: "Subject", namespace: "import", key: "1", label: "Pending" }), '["import","1"]');
});
