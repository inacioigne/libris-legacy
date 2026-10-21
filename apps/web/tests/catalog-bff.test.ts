import assert from "node:assert/strict";
import { test } from "node:test";
import { CatalogAccessError, createWork, getWorkWithMetadata, getWorkForEditor, updateWorkWithMetadata, requireCatalogMutationOrigin } from "../src/server/catalog";
import { AuthService } from "../src/server/auth/service";
import { OpenIdProvider } from "../src/server/auth/oidc";
import { randomId } from "../src/server/auth/security";
import { config, MemoryStore, json } from "./helpers";
import { richWork } from "./work-editor-fixture";
import { publishWorkspace, validateWorkspace } from "../src/server/catalog/editorial";

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
    return new Response(JSON.stringify(richWork), { headers: { "Content-Type": "application/json", ETag: '"catalog-v1-new"' } });
  });
  const current = await getWorkWithMetadata(richWork.internal_id, options);
  assert.equal(current.etag, '"catalog-v1-new"');
  const saved = await updateWorkWithMetadata(richWork.internal_id, { note: [] }, { ...options, ifMatch: '"catalog-v1-old"' });
  assert.equal(requests[1].headers.get("if-match"), '"catalog-v1-old"');
  assert.deepEqual(await requests[1].json(), { note: [] });
  assert.equal(saved.etag, '"catalog-v1-new"');
  assert.doesNotMatch(JSON.stringify(saved), /secret|csrf|Bearer/);
  await createWork({ title: richWork.title }, options);
  assert.equal(requests[2].method, "POST");
});

for (const status of [401, 403, 409, 412, 422]) test(`BFF preserves HTTP ${status} and safe structured diagnostic source`, async t => {
  const options = await fixture();
  const problem = { status, code: "synthetic", issues: [{ message: "Invalid", field_ids: ["/language/0/uri"] }] };
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify(problem), { status, headers: { "Content-Type": "application/problem+json" } }));
  await assert.rejects(updateWorkWithMetadata(richWork.internal_id, { note: null }, { ...options, ifMatch: '"old"' }), error => {
    assert.ok(error instanceof CatalogAccessError); assert.equal(error.status, status); assert.deepEqual(error.detail, problem); return true;
  });
});

test("BFF rejects anonymous and unprivileged sessions before sending editorial requests", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls++; throw new Error("must not reach API"); });
  const options = await fixture([]);
  await assert.rejects(getWorkWithMetadata(richWork.internal_id, { ...options, sessionId: "invalid" }), (e: unknown) => e instanceof CatalogAccessError && e.status === 401);
  await assert.rejects(createWork({ title: richWork.title }, options), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
  await assert.rejects(updateWorkWithMetadata(richWork.internal_id, { note: [] }, options), (e: unknown) => e instanceof CatalogAccessError && e.status === 428);
  assert.equal(calls, 0);
});

test("catalog actions origin protection rejects missing/foreign origin, spoofed host and cross-site fetch", async () => {
  const options = await fixture();
  const valid = { Origin: options.service.config.origin, Host: new URL(options.service.config.origin).host, "Sec-Fetch-Site": "same-origin" };
  await requireCatalogMutationOrigin({ ...options, requestHeaders: new Headers(valid) });
  for (const override of [{ Origin: "" }, { Origin: "https://evil.invalid" }, { Host: "evil.invalid" }, { "Sec-Fetch-Site": "cross-site" }])
    await assert.rejects(requireCatalogMutationOrigin({ ...options, requestHeaders: new Headers({ ...valid, ...override }) }), (e: unknown) => e instanceof CatalogAccessError && e.status === 403);
});

test("workspace validates and publishes Work, Instance and Item through the generated client with exact ETag", async t => {
  const options = await fixture(["bibliographic:read", "bibliographic:update"]);
  const validOrigin = {
    Origin: options.service.config.origin,
    Host: new URL(options.service.config.origin).host,
    "Sec-Fetch-Site": "same-origin",
  };
  const requests: Request[] = [];
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    requests.push(request.clone());
    const validation = request.url.endsWith("/workspace/validate");
    return new Response(JSON.stringify(validation
      ? { ready: true, revision: 4 }
      : { editorial_state: "published", current_revision: 4, published_revision: 4, public_revision: 4 }), {
      headers: { "Content-Type": "application/json", ETag: '"workspace-v2"' },
    });
  });
  const mutationOptions = { ...options, requestHeaders: new Headers(validOrigin) };
  for (const collection of ["works", "instances", "items"] as const) {
    assert.deepEqual(await validateWorkspace(collection, richWork.internal_id, '"workspace-v1"', mutationOptions), { ready: true, revision: 4 });
    const published = await publishWorkspace(collection, richWork.internal_id, '"workspace-v1"', mutationOptions);
    assert.equal(published.state.editorial_state, "published");
    assert.equal(published.etag, '"workspace-v2"');
  }
  assert.deepEqual(requests.map(request => new URL(request.url).pathname.split("/").slice(-4)), [
    ["works", richWork.internal_id, "workspace", "validate"], ["works", richWork.internal_id, "workspace", "publish"],
    ["instances", richWork.internal_id, "workspace", "validate"], ["instances", richWork.internal_id, "workspace", "publish"],
    ["items", richWork.internal_id, "workspace", "validate"], ["items", richWork.internal_id, "workspace", "publish"],
  ]);
  for (const request of requests) {
    assert.equal(request.method, "POST");
    assert.equal(request.headers.get("if-match"), '"workspace-v1"');
    assert.equal(request.headers.get("authorization"), "Bearer synthetic-access-secret");
  }
});


test("editorial metadata belongs to the loaded ETag; no stale state or private provenance crosses the BFF", async t => {
  const options = await fixture();
  let editorialETag = '\"same\"';
  t.mock.method(globalThis, "fetch", async (request: Request) => {
    const path = new URL(request.url).pathname;
    const body = path.endsWith("/editorial")
      ? { editorial_state: "draft", candidate_state: null, current_revision: 3 }
      : path.includes("/revisions/") ? { profile: "urn:libris:profile:monograph:v1:1", actor_id: "private-actor", provenance: { source: "private" } } : richWork;
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json", ETag: path.endsWith("/editorial") ? editorialETag : '\"same\"' } });
  });
  const loaded = await getWorkForEditor(richWork.internal_id, options);
  assert.deepEqual(loaded.metadata, { editorialState: "draft", candidateState: null, revision: 3, profile: "urn:libris:profile:monograph:v1:1" });
  assert.doesNotMatch(JSON.stringify(loaded), /private-actor|provenance/);
  editorialETag = '\"newer\"';
  assert.equal((await getWorkForEditor(richWork.internal_id, options)).metadata, undefined);
});
