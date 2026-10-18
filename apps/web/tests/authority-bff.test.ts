import assert from "node:assert/strict";
import { test } from "node:test";
import { CatalogAccessError } from "../src/server/catalog";
import { changeAuthorityRelation, getAuthority, getAuthorityRelations, listAuthorities } from "../src/server/catalog/authorities";
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
