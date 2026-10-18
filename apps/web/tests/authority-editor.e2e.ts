import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { build } from "esbuild";
import { connect, until, type DevTools } from "./browser";

test("Authority panel in Chromium: selection, keyboard, pt/en, roles, duplicates, errors and explicit 412 recovery", {
  skip: process.env.TEST_WEB_BROWSER_ENABLED !== "1" ? "Opt-in Chromium probe not enabled" : false,
  timeout: 60_000,
}, async () => {
  const bundle = await build({ entryPoints: [new URL("./fixtures/authority-browser.tsx", import.meta.url).pathname], bundle: true, write: false, platform: "browser", jsx: "automatic", define: { "process.env.NODE_ENV": '"development"' } });
  const server = createServer((request, response) => {
    if (request.url === "/app.js") { response.setHeader("Content-Type", "text/javascript"); return response.end(bundle.outputFiles[0].text); }
    response.setHeader("Content-Type", "text/html");
    response.end('<!doctype html><html lang="en"><head><title>Work editor test</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  const profile = await mkdtemp(join(tmpdir(), "libris-authority-"));
  const child = spawn(process.env.TEST_WEB_CHROME_PATH ?? "/usr/bin/google-chrome", ["--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking", "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  let control: DevTools | undefined, browser: DevTools | undefined;
  try {
    const socket = await new Promise<string>((resolve, reject) => {
      let output = "";
      const timeout = setTimeout(() => reject(new Error("Chrome startup timeout")), 10_000);
      child.stderr.on("data", chunk => { output += String(chunk); const url = output.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1]; if (url) { clearTimeout(timeout); resolve(url); } });
      child.once("error", reject);
    });
    control = await connect(socket);
    const { targetId } = await control.send("Target.createTarget", { url: "about:blank" });
    const targets = await (await fetch(`http://${new URL(socket).host}/json/list`)).json() as { id: string; webSocketDebuggerUrl: string }[];
    const target = targets.find(item => item.id === targetId); assert.ok(target);
    browser = await connect(target.webSocketDebuggerUrl); await browser.send("Page.enable");
    const b = browser;
    const send = b.send.bind(b);
    b.send = async (method, params) => {
      try { return await send(method, params); }
      catch (error) { throw new Error(`Protocol method ${method}: ${JSON.stringify(params)}`, { cause: error }); }
    };

    async function navigate(query: string) {
      await b.evaluate("document.body.innerHTML = ''");
      await b.send("Page.navigate", { url: origin + query });
      await until(b, "!!document.querySelector('section')");
    }
    async function click(text: string) {
      await b.evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(text)}).click()`);
    }
    async function fill(name: string, value: string) {
      const field = `document.querySelector('input[name="${name}"]')`;
      await b.evaluate(`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(${field}, ${JSON.stringify(value)}); ${field}.dispatchEvent(new Event('input', {bubbles:true}))`);
    }
    for (const locale of ["pt", "en"]) {
      await navigate(`/?locale=${locale}`);
      assert.equal(await b.evaluate("Array.from(document.querySelectorAll('input, select')).every(el => el.labels.length > 0)"), true);
      const tree = await b.send("Accessibility.getFullAXTree");
      const nodes = tree.nodes as { role?: { value: string }; name?: { value: string }; ignored?: boolean }[];
      assert.ok(nodes.filter(node => node.role?.value === "textbox" && !node.ignored).every(node => Boolean(node.name?.value)));
      assert.equal(await b.evaluate("document.body.textContent.includes('urn:external:agent') && document.body.textContent.includes('Pending subject')"), true);
      await click(locale === "pt" ? "Consultar autoridades" : "Browse authorities");
      await until(b, "document.querySelectorAll('button[aria-label^=\"Select: Alex\"],button[aria-label^=\"Selecionar: Alex\"]').length === 2");
      assert.equal(await b.evaluate("document.body.textContent.includes('urn:agent:duplicate-name') && document.body.textContent.includes('https://example.invalid/agents/2')"), true);
    }
    await fill("authority-label", "Missing");
    await until(b, "document.body.textContent.includes('No authority on this page')");
    await fill("authority-label", "Alex");
    await b.evaluate("document.querySelector('button[aria-label^=\"Select: Alex\"]').focus()");
    await b.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", text: "\r", windowsVirtualKeyCode: 13 });
    await b.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
    await until(b, "document.activeElement.textContent.includes('Selected authority')");
    await b.evaluate("Array.from(document.querySelectorAll('input[type=checkbox]'))[1].click(); window.authorityStatus = 412");
    await click("Link authority");
    await until(b, "document.querySelector('[role=alert]')?.textContent.includes('newer version')");
    assert.equal(await b.evaluate("window.authorityWrites.length"), 1);
    assert.equal(await b.evaluate("window.authorityWrites[0].link.role.length"), 2);
    assert.equal(await b.evaluate("document.body.textContent.includes('Selected authority')"), true);
    await click("Load current links for comparison");
    await until(b, "document.body.textContent.includes('Use this version and keep my selection')");
    assert.equal(await b.evaluate("window.authorityWrites.length"), 1);
    await click("Use this version and keep my selection");
    await b.evaluate("window.authorityStatus = 422");
    await click("Link authority");
    await until(b, "window.authorityWrites.length === 2 && !!document.querySelector('[role=alert]')");
    assert.equal(await b.evaluate("window.authorityWrites[1].etag"), '\"fresh\"');
    assert.equal(await b.evaluate("document.body.textContent.includes('Selected authority') && document.body.textContent.includes('urn:external:agent')"), true);
    await b.evaluate("window.authorityStatus = 200");
    await click("Link authority");
    await until(b, "window.authorityCurrent.relations.links.length === 3 && !document.body.textContent.includes('Selected authority')");
    // Repeat selection: same roles/identity is rejected before calling the action.
    await b.evaluate("document.querySelector('button[aria-label^=\"Select: Alex\"]').click()");
    await click("Link authority");
    await until(b, "document.body.textContent.includes('This link already exists')");
    assert.equal(await b.evaluate("window.authorityWrites.length"), 3);
    await b.evaluate("document.querySelector('button[aria-label=\"Unlink: Alex\"]').click()");
    await until(b, "window.authorityCurrent.relations.links.length === 2");
    assert.equal(await b.evaluate("window.authorityWrites[3].link.role.length"), 2);
    // Subject selection uses its identity and a direct subject predicate.
    await b.evaluate("const s=document.querySelector('select'); s.value='Subject'; s.dispatchEvent(new Event('change',{bubbles:true}))");
    await click("Browse authorities");
    await until(b, "!!document.querySelector('button[aria-label^=\"Select: Libraries\"]')");
    await b.evaluate("document.querySelector('button[aria-label^=\"Select: Libraries\"]').click()");
    await click("Link authority");
    await until(b, "window.authorityWrites.length === 5 && !!document.querySelector('button[aria-label=\"Unlink: Libraries\"]')");
    assert.equal(await b.evaluate("window.authorityWrites[4].link.target.entity_type"), "Subject");
    await b.evaluate("document.querySelector('button[aria-label=\"Unlink: Libraries\"]').click()");
    await until(b, "window.authorityCurrent.relations.links.length === 2");
    await b.evaluate("window.authoritySearchStatus = 503");
    await click("Browse authorities");
    await until(b, "!!document.querySelector('fieldset [role=alert]')");
    await b.evaluate("window.authoritySearchStatus = 200");
    await fill("authority-uri", "urn:missing");
    await click("Browse authorities");
    assert.equal(await b.evaluate("!!document.querySelector('fieldset[aria-busy=true]')"), true);
    await until(b, "document.body.textContent.includes('No authority on this page')");
    await navigate("/?readonly=1");
    assert.equal(await b.evaluate("document.querySelectorAll('button[aria-label^=\"Unlink:\"]').length"), 0);
    assert.equal(await b.evaluate("document.querySelector('input[name=authority-label]') === null"), true);
    await navigate("/?denied=1");
    assert.equal(await b.evaluate("document.body.textContent.includes('Authority lookup requires')"), true);
  } finally {
    browser?.close(); control?.close();
    if (child.exitCode === null) { child.kill("SIGTERM"); await new Promise<void>(resolve => child.once("exit", () => resolve())); }
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
