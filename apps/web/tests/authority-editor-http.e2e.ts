import assert from "node:assert/strict";
import { test } from "node:test";
import { spawn, type ChildProcess } from "node:child_process";
import { createServer, type Server } from "node:http";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { Pool } from "pg";
import { migrateDatabase } from "./migrations";
import { jwt, jwk } from "./helpers";
import { readConfig } from "../src/server/auth/config";
import { PostgresAuthStore } from "../src/server/auth/store";
import { randomId } from "../src/server/auth/security";
import { connect, until, type DevTools } from "./browser";

async function listen(server: Server): Promise<number> {
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string"); return address.port;
}
async function port(): Promise<number> {
  const server = createServer(), value = await listen(server);
  await new Promise<void>(resolve => server.close(() => resolve())); return value;
}
async function stop(child?: ChildProcess) {
  if (!child || child.exitCode !== null) return;
  child.kill("SIGTERM"); await new Promise<void>(resolve => child.once("exit", () => resolve()));
}
async function ready(url: string, child: ChildProcess) {
  for (let attempt = 0; attempt < 150; attempt++) {
    try { if ((await fetch(url)).ok) return; } catch { /* startup */ }
    if (child.exitCode !== null) break;
    await delay(100);
  }
  throw new Error("Isolated service unavailable");
}

test("Authorities browser -> real Next actions/BFF -> signed JWT/FastAPI -> disposable PostgreSQL; independent revisions and 412", {
  skip: !process.env.TEST_ADMIN_API_DATABASE_URL || process.env.TEST_WEB_BROWSER_ENABLED !== "1" ? "Disposable web/API PostgreSQL and Chromium not enabled" : false,
  timeout: 90_000,
}, async () => {
  assert.equal(process.env.TEST_WEB_ISOLATED, "1");
  const database = "authority_editor_" + randomBytes(8).toString("hex");
  // This variable is supplied only by the isolated harness. Own a fresh database,
  // so the existing concurrent HTTP probes keep their initially empty databases.
  const admin = new Pool({ connectionString: process.env.TEST_ADMIN_API_DATABASE_URL });
  await admin.query(`CREATE DATABASE ${database}`);
  const connection = new URL(process.env.TEST_ADMIN_API_DATABASE_URL!); connection.pathname = "/" + database;
  const pool = new Pool({ connectionString: connection.href });
  let api: ChildProcess | undefined, web: ChildProcess | undefined, chrome: ChildProcess | undefined;
  let browser: DevTools | undefined, control: DevTools | undefined;
  let profile: string | undefined;
  let logs = "";
  const capture = (child: ChildProcess) => { child.stdout?.on("data", chunk => { logs += String(chunk); }); child.stderr?.on("data", chunk => { logs += String(chunk); }); };
  let issuer = "";
  const provider = createServer((request, response) => {
    response.setHeader("Content-Type", "application/json");
    if (request.url?.endsWith("openid-configuration")) return response.end(JSON.stringify({ issuer, jwks_uri: issuer + "/jwks" }));
    if (request.url === "/jwks") return response.end(JSON.stringify({ keys: [jwk] }));
    response.statusCode = 404; response.end("{}");
  });
  try {
    issuer = `http://127.0.0.1:${await listen(provider)}`;
    const apiPort = await port(), webPort = await port();
    const apiUrl = `http://127.0.0.1:${apiPort}`, origin = `http://127.0.0.1:${webPort}`;
    await migrateDatabase(connection.href);
    const env = { ...process.env, WEB_AUTH_ENV: "local", WEB_ORIGIN: origin, WEB_OIDC_ISSUER: issuer,
      WEB_OIDC_CLIENT_ID: "libris-web", WEB_SESSION_DATABASE_URL: connection.href,
      WEB_SESSION_KEYS: JSON.stringify({ test: randomBytes(32).toString("base64") }), WEB_SESSION_ACTIVE_KEY: "test", API_INTERNAL_URL: apiUrl };
    api = spawn(new URL("../../api/.venv/bin/python", import.meta.url).pathname, ["-m", "uvicorn", "libris.main:app", "--host", "127.0.0.1", "--port", String(apiPort), "--no-access-log"], {
      cwd: new URL("../../api", import.meta.url), env: { ...process.env, APP_ENV: "development", DATABASE_URL: connection.href.replace(/^postgres(?:ql)?:/, "postgresql+asyncpg:"), OIDC_ENABLED: "true", OIDC_ISSUER_URL: issuer, OIDC_AUDIENCE: "libris-api", OIDC_ROLE_SOURCE: "client", OIDC_ROLE_CLIENT: "libris-api" }, stdio: ["ignore", "pipe", "pipe"],
    }); capture(api); await ready(apiUrl + "/health", api);
    web = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "--hostname", "127.0.0.1", "--port", String(webPort)], { cwd: new URL("..", import.meta.url), env, stdio: ["ignore", "pipe", "pipe"] });
    capture(web); await ready(origin + "/en/opac", web);
    const now = Math.floor(Date.now() / 1000);
    const token = jwt({ iss: issuer, aud: "libris-api", sub: "synthetic-authority-editor", azp: "libris-web", typ: "Bearer", iat: now, exp: now + 300, resource_access: { "libris-api": { roles: ["libris-cataloger"] } } });
    const session = randomId();
    await new PostgresAuthStore(pool, readConfig(env)).createSession(session, { accessToken: token, subject: "synthetic-authority-editor", csrf: randomId(), accessExpiresAt: Date.now() + 300_000, expiresAt: Date.now() + 300_000 });
    profile = await mkdtemp(join(tmpdir(), "libris-authority-http-"));
    chrome = spawn(process.env.TEST_WEB_CHROME_PATH ?? "/usr/bin/google-chrome", ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
    const socket = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timeout = setTimeout(() => reject(new Error("Chrome startup timeout")), 10_000);
      chrome!.stderr!.on("data", chunk => { output += String(chunk); const url = output.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1]; if (url) { clearTimeout(timeout); resolve(url); } });
      chrome!.once("error", reject);
    });
    control = await connect(socket);
    const { targetId } = await control.send("Target.createTarget", { url: "about:blank" });
    const targets = await (await fetch(`http://${new URL(socket).host}/json/list`)).json() as { id: string; webSocketDebuggerUrl: string }[];
    const target = targets.find(item => item.id === targetId); assert.ok(target);
    browser = await connect(target.webSocketDebuggerUrl);
    await browser.send("Page.enable"); await browser.send("Network.enable");
    await browser.send("Network.setCookie", { name: "libris-session", value: session, url: origin, httpOnly: true, sameSite: "Lax" });
    const headers = { Authorization: "Bearer " + token };
    const managerToken = jwt({ iss: issuer, aud: "libris-api", sub: "synthetic-manager", azp: "libris-web", typ: "Bearer", iat: now, exp: now + 300, resource_access: { "libris-api": { roles: ["libris-authority-manager"] } } });
    async function create(collection: string, data: object, accessToken = token) {
      const response = await fetch(apiUrl + "/api/v1/catalog/" + collection, { method: "POST", headers: { Authorization: "Bearer " + accessToken, "Content-Type": "application/json" }, body: JSON.stringify(data) });
      assert.equal(response.status, 201); return response.json();
    }
    const agent = await create("agents", { kind: "Person", authorized_name: [{ value: "Shared agent", language: "en" }] }, managerToken);
    const subject = await create("subjects", { preferred_label: [{ value: "Shared subject", language: "en" }], scheme: [{ uri: "urn:scheme:test" }] }, managerToken);
    const work = await create("works", { title: [{ main_title: [{ value: "Authority integration" }] }] });
    const apiPath = apiUrl + "/api/v1/catalog/works/" + work.internal_id;
    const authorityBefore = await pool.query("SELECT * FROM experimental_semantic_revisions WHERE resource_id = ANY($1::uuid[]) ORDER BY resource_id, number", [[agent.internal_id, subject.internal_id]]);
    const authorityETags = await Promise.all([['agents', agent], ['subjects', subject]].map(async ([collection, value]) => (await fetch(apiUrl + '/api/v1/catalog/' + collection + '/' + value.internal_id, { headers })).headers.get('etag')));
    const b = browser;
    const click = async (text: string) => b.evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(text)}).click()`);
    await b.send("Page.navigate", { url: origin + "/en/admin/catalog/works/" + work.internal_id });
    await until(b, "!!document.querySelector('input[name=authority-label]') && !!Object.keys(document.querySelector('input[name=authority-label]')).find(k => k.startsWith('__reactProps'))");
    await click("Browse authorities");
    await until(b, "!!document.querySelector('button[aria-label^=\"Select: Shared agent\"]')");
    await b.evaluate("document.querySelector('button[aria-label^=\"Select: Shared agent\"]').click()");
    await b.evaluate("Array.from(document.querySelectorAll('input[type=checkbox]'))[1].click()");
    const source = await fetch(apiPath, { headers });
    const concurrent = await fetch(apiPath, { method: "PATCH", headers: { ...headers, "Content-Type": "application/json", "If-Match": source.headers.get("etag")! }, body: JSON.stringify({ note: [{ label: [{ value: "Concurrent note", language: "en" }] }] }) });
    assert.equal(concurrent.status, 200);
    await click("Link authority");
    await until(b, "document.querySelector('[role=alert]')?.textContent.includes('newer version')");
    assert.equal(await b.evaluate("document.body.textContent.includes('Selected authority')"), true);
    await click("Load current links for comparison");
    await until(b, "document.body.textContent.includes('Use this version and keep my selection')");
    await click("Use this version and keep my selection");
    await click("Link authority");
    await until(b, "!!document.querySelector('button[aria-label=\"Unlink: Shared agent\"]')");
    const linked = await (await fetch(apiPath, { headers })).json();
    assert.equal(linked.note[0].label[0].value, "Concurrent note");
    assert.deepEqual(new Set(linked.contribution[0].role.map((role: { uri: string }) => role.uri)), new Set(["http://id.loc.gov/vocabulary/relators/aut", "http://id.loc.gov/vocabulary/relators/edt"]));
    await b.evaluate("const select = document.querySelector('select'); select.value = 'Subject'; select.dispatchEvent(new Event('change', {bubbles:true}))");
    await click("Browse authorities");
    await until(b, "!!document.querySelector('button[aria-label^=\"Select: Shared subject\"]')");
    await b.evaluate("document.querySelector('button[aria-label^=\"Select: Shared subject\"]').click()");
    await click("Link authority");
    await until(b, "!!document.querySelector('button[aria-label=\"Unlink: Shared subject\"]')");
    await b.send("Page.reload");
    await until(b, "!!document.querySelector('button[aria-label=\"Unlink: Shared subject\"]') && !!Object.keys(document.querySelector('input[name=authority-label]')).find(k => k.startsWith('__reactProps'))");
    for (const name of ["Shared agent", "Shared subject"]) {
      await b.evaluate(`window.confirm = () => true; document.querySelector('button[aria-label="Unlink: ${name}"]').click()`);
      await until(b, `!document.querySelector('button[aria-label="Unlink: ${name}"]')`);
    }
    const final = await (await fetch(apiPath, { headers })).json();
    assert.deepEqual(final.contribution, []); assert.deepEqual(final.subject, []);
    assert.equal(final.note[0].label[0].value, "Concurrent note");
    assert.deepEqual((await pool.query("SELECT * FROM experimental_semantic_revisions WHERE resource_id = ANY($1::uuid[]) ORDER BY resource_id, number", [[agent.internal_id, subject.internal_id]])).rows, authorityBefore.rows);
    for (const [index, collection, value] of [[0, 'agents', agent], [1, 'subjects', subject]] as const) {
      const response = await fetch(apiUrl + '/api/v1/catalog/' + collection + '/' + value.internal_id, { headers });
      assert.equal(response.headers.get('etag'), authorityETags[index]);
      assert.equal((await fetch(apiUrl + '/api/v1/catalog/' + collection + '/' + value.internal_id, { method: 'PATCH', headers: { ...headers, 'Content-Type': 'application/json', 'If-Match': response.headers.get('etag')! }, body: '{}' })).status, 403);
    }
    // The same panel respects distinct Instance/Item predicates and preserves
    // publication literals while changing only an agent edge.
    const organization = await create("agents", { kind: "Organization", authorized_name: [{ value: "Shared institution" }] }, managerToken);
    const organizationPath = apiUrl + "/api/v1/catalog/agents/" + organization.internal_id;
    const organizationBefore = await fetch(organizationPath, { headers });
    const instance = await create("instances", { instance_of: { uri: work.uri }, provision_activity: [{
      agent: [{ uri: "urn:external:publisher" }], place: [{ uri: "urn:place:test" }], date: [{ value: "2026", datatype: "http://www.w3.org/2001/XMLSchema#gYear" }],
    }] });
    const item = await create("items", { item_of: { uri: instance.uri }, identified_by: [{ kind: "Local", value: "SEMANTIC-TEST" }] });
    for (const [collection, record] of [["instances", instance], ["items", item]] as const) {
      await b.send("Page.navigate", { url: origin + "/en/admin/catalog/" + collection + "/" + record.internal_id });
      await until(b, `location.pathname.endsWith('${record.internal_id}') && !!document.querySelector('input[name=authority-label]') && !!Object.keys(document.querySelector('input[name=authority-label]')).find(k => k.startsWith('__reactProps'))`);
      await click("Browse authorities");
      await until(b, "!!document.querySelector('button[aria-label^=\"Select: Shared institution\"]')");
      if (collection === "items") assert.equal(await b.evaluate("document.querySelector('button[aria-label^=\"Select: Shared agent\"]') === null"), true);
      await b.evaluate("document.querySelector('button[aria-label^=\"Select: Shared institution\"]').click()");
      if (collection === "instances") await b.evaluate("const activities=document.querySelector('select'); activities.selectedIndex=2; activities.dispatchEvent(new Event('change', {bubbles:true}))");
      await click("Link authority");
      await until(b, "!!document.querySelector('button[aria-label=\"Unlink: Shared institution\"]')");
      const saved = await (await fetch(apiUrl + "/api/v1/catalog/" + collection + "/" + record.internal_id, { headers })).json();
      if (collection === "instances") {
        assert.equal(saved.provision_activity.length, 1);
        assert.deepEqual(saved.provision_activity[0].date, instance.provision_activity[0].date);
        assert.deepEqual(saved.provision_activity[0].place, instance.provision_activity[0].place);
        assert.equal(saved.provision_activity[0].agent.length, 2);
      } else assert.equal(saved.held_by[0].uri, organization.uri);
      await b.evaluate("window.confirm = () => true; document.querySelector('button[aria-label=\"Unlink: Shared institution\"]').click()");
      await until(b, "!document.querySelector('button[aria-label=\"Unlink: Shared institution\"]')");
      const removed = await (await fetch(apiUrl + "/api/v1/catalog/" + collection + "/" + record.internal_id, { headers })).json();
      if (collection === "instances") assert.deepEqual(removed.provision_activity, instance.provision_activity);
      else assert.deepEqual(removed.held_by, []);
    }
    assert.equal((await fetch(organizationPath, { headers })).headers.get("etag"), organizationBefore.headers.get("etag"));
    assert.equal((await fetch(apiPath)).status, 401);
    assert.equal((await fetch(apiPath + "/public")).status, 404);
    assert.equal(await b.evaluate("document.documentElement.outerHTML.includes('Bearer ') || document.documentElement.outerHTML.includes('access_token')"), false);
    assert.equal(logs.includes(token) || logs.includes(managerToken), false);
  } finally {
    browser?.close(); control?.close(); await stop(chrome); await stop(web); await stop(api);
    await new Promise<void>(resolve => provider.close(() => resolve()));
    await pool.end();
    await admin.query(`DROP DATABASE ${database}`); await admin.end();
    if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
