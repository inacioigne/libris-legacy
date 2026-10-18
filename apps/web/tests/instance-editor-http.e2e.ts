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

test("Instance browser -> real Next actions/BFF -> signed JWT/FastAPI -> disposable PostgreSQL; 412 preserves both edits", {
  skip: !process.env.TEST_ADMIN_API_DATABASE_URL || process.env.TEST_WEB_BROWSER_ENABLED !== "1" ? "Disposable web/API PostgreSQL and Chromium not enabled" : false,
  timeout: 90_000,
}, async () => {
  assert.equal(process.env.TEST_WEB_ISOLATED, "1");
  const database = "instance_editor_" + randomBytes(8).toString("hex");
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
    const token = jwt({ iss: issuer, aud: "libris-api", sub: "synthetic-instance-editor", azp: "libris-web", typ: "Bearer", iat: now, exp: now + 300, resource_access: { "libris-api": { roles: ["libris-cataloger"] } } });
    const session = randomId();
    await new PostgresAuthStore(pool, readConfig(env)).createSession(session, { accessToken: token, subject: "synthetic-instance-editor", csrf: randomId(), accessExpiresAt: Date.now() + 300_000, expiresAt: Date.now() + 300_000 });
    profile = await mkdtemp(join(tmpdir(), "libris-instance-http-"));
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
    const workResponse = await fetch(apiUrl + "/api/v1/catalog/works", { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ title: [{ main_title: [{ value: "Existing Work" }] }] }) });
    assert.equal(workResponse.status, 201);
    const work = await workResponse.json();
    await browser.send("Page.navigate", { url: origin + "/en/admin/catalog/instances/new" });
    await until(browser, "!!document.querySelector('input[name=payload]') && !!Object.keys(document.querySelector('form[aria-busy]')).find(k => k.startsWith('__reactProps'))");
    const fill = async (value: string) => browser!.evaluate(`(() => { const input = document.querySelector('input[name="/title/0/main_title/0/value"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    const click = async (text: string) => browser!.evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(text)}).click()`);
    await click("Find Works by exact UUID or URI");
    await until(browser, "document.body.textContent.includes('Existing Work')");
    await click("Select this Work");
    await click("Add — Titles and variants");
    await fill("Created in browser"); await click("Save instance");
    await until(browser, "location.pathname.split('/').at(-1)?.length === 36 && document.body.textContent.includes('Created in browser')");
    const path = await browser.evaluate("location.pathname") as string;
    const id = path.split("/").at(-1)!;
    const apiPath = apiUrl + "/api/v1/catalog/instances/" + id;
    const created = await fetch(apiPath, { headers }); assert.equal(created.status, 200);
    assert.equal((await fetch(apiPath + "/public")).status, 404);
    assert.equal((await (await fetch(apiPath, { headers })).json()).instance_of.uri, work.uri);
    assert.equal((await fetch(apiUrl + "/api/v1/catalog/works/" + work.internal_id, { headers })).headers.get("etag"), workResponse.headers.get("etag"));
    assert.equal((await fetch(apiPath + "/editorial/publish", { method: "POST", headers: { ...headers, "Content-Type": "application/json", "If-Match": created.headers.get("etag")! }, body: JSON.stringify({ selected_revision: 1, scope: "record" }) })).status, 403);
    assert.equal((await fetch(apiPath)).status, 401);
    const anonymous = await fetch(origin + path + "/edit");
    assert.doesNotMatch(await anonymous.text(), /Created in browser/);
    await browser.send("Page.navigate", { url: origin + path + "/edit" });
    await until(browser, "!!document.querySelector('input[name=ifMatch]') && !!Object.keys(document.querySelector('form[aria-busy]')).find(k => k.startsWith('__reactProps'))");
    await fill("My local revision");
    const first = await fetch(apiPath, { method: "PATCH", headers: { ...headers, "Content-Type": "application/json", "If-Match": created.headers.get("etag")! }, body: JSON.stringify({ note: [{ label: [{ value: "Concurrent persisted note", language: "en" }] }] }) });
    assert.equal(first.status, 200);
    await click("Save instance");
    await until(browser, "document.querySelector('[role=alert]')?.textContent.includes('newer version')");
    assert.equal(await browser.evaluate("document.querySelector('input[name=\"/title/0/main_title/0/value\"]').value"), "My local revision");
    await click("Load current version for comparison");
    await until(browser, "!!document.querySelector('input[name=\"choice-title\"]')");
    await browser.evaluate("document.querySelector('input[name=\"choice-title\"]').click()");
    await click("Apply choices and continue editing");
    await click("Save instance");
    await until(browser, "document.querySelector('[role=status]')?.textContent.includes('No pending changes')");
    const saved = await fetch(apiPath, { headers }), data = await saved.json();
    assert.equal(data.title[0].main_title[0].value, "My local revision");
    assert.equal(data.note[0].label[0].value, "Concurrent persisted note");
    assert.equal(data.internal_id, id);
    assert.equal(data.instance_of.uri, work.uri);
    assert.equal((await (await fetch(apiPath + "/editorial", { headers })).json()).current_revision, 3);
    assert.equal(await browser.evaluate("document.documentElement.outerHTML.includes('Bearer ') || document.documentElement.outerHTML.includes('access_token')"), false);
    assert.equal(logs.includes(token), false);
    await browser.send("Page.reload");
    await until(browser, "document.querySelector('input[name=\"/title/0/main_title/0/value\"]')?.value === 'My local revision'");
    assert.equal(await browser.evaluate("document.querySelector('input[name=\"/note/0/label/0/value\"]').value"), "Concurrent persisted note");
  } finally {
    browser?.close(); control?.close(); await stop(chrome); await stop(web); await stop(api);
    await new Promise<void>(resolve => provider.close(() => resolve()));
    await pool.end();
    await admin.query(`DROP DATABASE ${database}`); await admin.end();
    if (profile) await rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
