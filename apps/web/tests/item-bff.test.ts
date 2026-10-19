import assert from "node:assert/strict";
import { test } from "node:test";
import { CatalogAccessError, requireCatalogMutationOrigin } from "../src/server/catalog";
import { createItem, getItem, getItemForEditor, updateItem, listItems, validateItem } from "../src/server/catalog/items";
import { AuthService } from "../src/server/auth/service";
import { OpenIdProvider } from "../src/server/auth/oidc";
import { randomId } from "../src/server/auth/security";
import { config, MemoryStore, json } from "./helpers";
import { richItem } from "./item-editor-fixture";
import { relatedChildren, resolveItemInstance } from "../src/server/catalog/related";
import { richInstance } from "./instance-editor-fixture";

test("stored inverse relations identify children without scanning or inferring URI suffixes", async t => {
  const options = await fixture();
  const seen: string[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    seen.push(request.url);
    return json({ source: { internal_id: richInstance.internal_id }, links: [
      { predicate: "http://id.loc.gov/ontologies/bibframe/hasItem", target: { kind: "local", entity_type: "Item", internal_id: richItem.internal_id, uri: richItem.uri } },
      { predicate: "http://id.loc.gov/ontologies/bibframe/instanceOf", target: { kind: "local", entity_type: "Work", internal_id: "other", uri: "urn:work" } },
    ] });
  });
  const children = await relatedChildren("instances", richInstance.internal_id, options);
  assert.equal(children.length, 1);
  assert.equal(children[0].uri, richItem.uri);
  assert.equal(new URL(seen[0]).searchParams.get("include_inverses"), "true");
  assert.equal(seen.length, 1);
});

test("Item parent resolution uses exact Instance URI and verifies local UUID/URI pairs", async t => {
  const options = await fixture();
  let requested = "";
  t.mock.method(globalThis, "fetch", async (request: Request) => { requested = request.url; return json(request.url.includes("?") ? { items: [richInstance] } : richInstance); });
  assert.deepEqual(await resolveItemInstance({ uri: richInstance.uri }, options), richInstance);
  assert.equal(new URL(requested).searchParams.get("uri"), richInstance.uri);
  assert.equal(await resolveItemInstance({ kind: "local", entity_type: "Instance", internal_id: richInstance.internal_id, uri: "urn:wrong" }, options), undefined);
});

async function fixture(permissions = ["bibliographic:read", "bibliographic:create", "bibliographic:update"]) {
  const cfg = config(), store = new MemoryStore(), sessionId = randomId();
  const service = new AuthService(cfg, store, new OpenIdProvider(cfg), Date.now, async () => json({ actor_id: "oidc:sha256:synthetic", roles: ["libris-admin"], permissions }));
  await store.createSession(sessionId, { accessToken: "synthetic-access-secret", subject: "synthetic", csrf: randomId(), accessExpiresAt: Date.now() + 300_000, expiresAt: Date.now() + 300_000 });
  return { service, sessionId };
}

test("BFF uses generated client, server Bearer, exact If-Match and PATCH response's ETag", async t => {
  const options = await fixture();
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request.clone());
    assert.equal(request.headers.get("authorization"), "Bearer synthetic-access-secret");
    assert.equal(request.headers.get("cookie"), null);
    assert.equal(request.cache, "no-store");
    return new Response(JSON.stringify(richItem), { headers: { "Content-Type": "application/json", ETag: '"catalog-v1-new"' } });
  });
  const current = await getItem(richItem.internal_id, options);
  assert.equal(current.etag, '"catalog-v1-new"');
  const saved = await updateItem(richItem.internal_id, { note: [] }, { ...options, ifMatch: '"catalog-v1-old"' });
  assert.equal(requests[1].headers.get("if-match"), '"catalog-v1-old"');
  assert.deepEqual(await requests[1].json(), { note: [] });
  assert.equal(saved.etag, '"catalog-v1-new"');
  assert.doesNotMatch(JSON.stringify(saved), /secret|csrf|Bearer/);
  await createItem({ item_of: richItem.item_of, identified_by: richItem.identified_by }, options);
  assert.equal(requests[2].method, "POST");
});

for (const status of [400, 401, 403, 404, 409, 412, 422]) test(`BFF preserves HTTP ${status} and safe structured diagnostic source`, async t => {
  const options = await fixture();
  const problem = { status, code: "synthetic", issues: [{ message: "Invalid", field_ids: ["/language/0/uri"] }] };
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify(problem), { status, headers: { "Content-Type": "application/problem+json" } }));
  await assert.rejects(updateItem(richItem.internal_id, { note: null }, { ...options, ifMatch: '"old"' }), error => {
    assert.ok(error instanceof CatalogAccessError); assert.equal(error.status, status); assert.deepEqual(error.detail, problem); return true;
  });
});

test("BFF rejects anonymous and unprivileged sessions before sending editorial requests", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("must not reach API"); });
  const options = await fixture([]);
  await assert.rejects(getItem(richItem.internal_id, { ...options, sessionId: "invalid" }), (e: unknown) => e instanceof CatalogAccessError && e.status === 401);
  await assert.rejects(createItem({ item_of: richItem.item_of, identified_by: richItem.identified_by }, options), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
  await assert.rejects(updateItem(richItem.internal_id, { note: [] }, options), (e: unknown) => e instanceof CatalogAccessError && e.status === 428);
  assert.equal(calls, 0);
});

test("catalog actions origin protection rejects missing/foreign origin, spoofed host and cross-site fetch", async () => {
  const options = await fixture();
  const valid = { Origin: options.service.config.origin, Host: new URL(options.service.config.origin).host, "Sec-Fetch-Site": "same-origin" };
  await requireCatalogMutationOrigin({ ...options, requestHeaders: new Headers(valid) });
  for (const override of [{ Origin: "" }, { Origin: "https://evil.invalid" }, { Host: "evil.invalid" }, { "Sec-Fetch-Site": "cross-site" }])
    await assert.rejects(requireCatalogMutationOrigin({ ...options, requestHeaders: new Headers({ ...valid, ...override }) }), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
});


test("editorial metadata belongs to the loaded ETag; no stale state or private actor crosses the BFF", async t => {
  const options = await fixture();
  let editorialETag = '\"same\"';
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    const body = path.endsWith("/editorial")
      ? { editorial_state: "draft", candidate_state: null, current_revision: 3 }
      : path.includes("/revisions/") ? { number: 3, created_at: "2026-10-18T00:00:00Z", profile: "urn:libris:profile:monograph:v1:1", actor_id: "private-actor", provenance: { source: "catalog", process: "http:patch" } } : richItem;
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json", ETag: path.endsWith("/editorial") ? editorialETag : '\"same\"' } });
  });
  const loaded = await getItemForEditor(richItem.internal_id, options);
  assert.deepEqual(loaded.metadata, { editorialState: "draft", candidateState: null, revision: 3, profile: "urn:libris:profile:monograph:v1:1", createdAt: "2026-10-18T00:00:00Z", provenance: { source: "catalog", process: "http:patch" } });
  assert.doesNotMatch(JSON.stringify(loaded), /private-actor/);
  editorialETag = '\"newer\"';
  assert.equal((await getItemForEditor(richItem.internal_id, options)).metadata, undefined);
});

test("Item list uses supported filters and prevalidation never calls a persistence endpoint", async t => {
  const options = await fixture();
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request.clone());
    return json(request.url.includes("validation") ? { conforms: true, issues: [] } : { items: [richItem], next_cursor: null });
  });
  await listItems({ limit: 10, editorial_state: "draft", cursor: "opaque", uri: richItem.uri }, options);
  const url = new URL(requests[0].url);
  assert.equal(url.pathname, "/api/v1/catalog/items");
  assert.equal(url.searchParams.get("cursor"), "opaque");
  assert.equal(url.searchParams.get("editorial_state"), "draft");
  await validateItem({ item_of: richItem.item_of, identified_by: richItem.identified_by }, options);
  assert.equal(new URL(requests[1].url).pathname, "/api/v1/catalog/items/validation");
  assert.equal((await requests[1].json()).profile_release, "urn:libris:profile:monograph:v1:2");
});
