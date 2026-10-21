import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { build } from "esbuild";
import { connect, until, type DevTools } from "./browser";

// Real React and Chromium; only actions/persistence are simulated in this probe.
// No app route, authentication bypass or new dependency is installed for tests.
test("Item editor in Chromium: pt/en, accessibility, validation, repeaters, 412 reconciliation and reload", {
  skip: process.env.TEST_ITEM_EDITOR_BROWSER !== "1" && process.env.TEST_WEB_BROWSER_ENABLED !== "1" ? "Opt-in Chromium component probe not enabled" : false,
  timeout: 60_000,
}, async () => {
  const bundle = await build({ entryPoints: [new URL("./fixtures/item-editor-browser.tsx", import.meta.url).pathname], bundle: true, write: false, platform: "browser", jsx: "automatic", define: { "process.env.NODE_ENV": '"development"' } });
  const server = createServer((request, response) => {
    if (request.url === "/app.js") { response.setHeader("Content-Type", "text/javascript"); return response.end(bundle.outputFiles[0].text); }
    response.setHeader("Content-Type", "text/html");
    response.end('<!doctype html><html lang="en"><head><title>Item editor test</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>');
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address(); assert.ok(address && typeof address !== "string");
  const origin = `http://127.0.0.1:${address.port}`;
  const profile = await mkdtemp(join(tmpdir(), "libris-item-editor-"));
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
    let b = browser;
    let activeTarget = targetId;
    async function navigate(query: string) {
      // A fresh tab avoids accepting a native unsaved-navigation dialog implicitly.
      await control!.send("Target.closeTarget", { targetId: activeTarget });
      b.close();
      const created = await control!.send("Target.createTarget", { url: "about:blank" });
      activeTarget = created.targetId;
      const tabs = await (await fetch(`http://${new URL(socket).host}/json/list`)).json() as { id: string; webSocketDebuggerUrl: string }[];
      const tab = tabs.find(item => item.id === activeTarget); assert.ok(tab);
      browser = await connect(tab.webSocketDebuggerUrl); b = browser;
      await b.send("Page.enable");
      await b.send("Page.navigate", { url: origin + query });
      await until(b, "!!document.querySelector('input[name=payload]')");
    }
    const field = (name: string) => `document.querySelector('input[name="${name}"]')`;
    async function fill(name: string, value: string) {
      await b.evaluate(`Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(${field(name)}, ${JSON.stringify(value)}); ${field(name)}.dispatchEvent(new Event('input', {bubbles:true}))`);
    }
    async function clickText(text: string) {
      await b.evaluate(`Array.from(document.querySelectorAll('button')).find(button => button.textContent === ${JSON.stringify(text)}).click()`);
    }
    for (const locale of ["en", "pt"]) {
      await navigate(`/?locale=${locale}`);
      assert.equal(await b.evaluate("Array.from(document.querySelectorAll('input:not([type=hidden]):not([type=radio]), select')).every(el => el.labels.length > 0)"), true);
      assert.equal(await b.evaluate("new Set(Array.from(document.querySelectorAll('[id]'), el => el.id)).size === document.querySelectorAll('[id]').length"), true);
      assert.equal(await b.evaluate("Array.from(document.querySelectorAll('input:not([type=hidden]):not([type=radio])')).every(el => el.getAttribute('aria-describedby').split(' ').every(id => document.getElementById(id)))"), true);
      const tree = await b.send("Accessibility.getFullAXTree");
      const nodes = tree.nodes as { role?: { value: string }; name?: { value: string }; ignored?: boolean }[];
      assert.ok(nodes.filter(node => node.role?.value === "textbox" && !node.ignored).every(node => Boolean(node.name?.value)));
      assert.ok(nodes.some(node => node.role?.value === "button" && node.name?.value.includes(locale === "en" ? "Save Item as draft" : "Salvar item como rascunho")));
    }
    await navigate("/?locale=en");
    await b.evaluate(`${field('/custodial_history/0/value')}.focus()`);
    await b.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await b.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    assert.equal(await b.evaluate("document.activeElement.name"), "/custodial_history/0/language");
    await clickText("Add — Custodial history");
    await until(b, "document.activeElement.name === '/custodial_history/2/value'");
    await fill("/custodial_history/2/value", "Temporary history");
    await clickText("Remove — Custodial history 3");
    await fill("/custodial_history/0/value", "Local custody");
    await clickText("Save Item as draft");
    assert.equal(await b.evaluate("document.querySelector('form[aria-busy]').getAttribute('aria-busy')"), "true");
    await until(b, "document.querySelector('[role=alert]')?.textContent.includes('newer version')");
    assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Local custody");
    assert.equal(await b.evaluate("window.itemSubmissions.length"), 1);
    assert.equal(await b.evaluate("window.currentItem.custodial_history[0].value"), "Remote custody");
    assert.equal(await b.evaluate("document.querySelector('button[type=submit]').disabled"), true);
    await clickText("Load current version for comparison");
    await until(b, "!!document.querySelector('input[name=\"choice-custodial_history\"]')");
    assert.equal(await b.evaluate("document.querySelector('input[name=ifMatch]').value"), '"original"');
    assert.equal(await b.evaluate("document.body.textContent.includes('Remote custody') && document.body.textContent.includes('Local custody')"), true);
    await b.evaluate("document.querySelector('input[name=\"choice-custodial_history\"]').click()");
    await clickText("Apply choices and continue editing");
    await until(b, "document.querySelector('input[name=ifMatch]').value === '\"current\"'");
    assert.equal(await b.evaluate(`${field('/note/0/label/0/value')}.value`), "Remote note");
    assert.equal(await b.evaluate("window.itemSubmissions.length"), 1);
    await b.evaluate("window.nextItemStatus = 422");
    await clickText("Save Item as draft");
    await until(b, "document.activeElement.name === '/identified_by/0/value'");
    assert.equal(await b.evaluate(`${field('/identified_by/0/value')}.getAttribute('aria-invalid')`), "true");
    assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Local custody");
    for (const status of [401, 403, 409]) {
      await b.evaluate(`window.nextItemStatus = ${status}`);
      await clickText("Save Item as draft");
      await until(b, "!document.querySelector('button[type=submit]').disabled");
      assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Local custody");
    }
    await b.evaluate("window.nextItemStatus = 200");
    await clickText("Save Item as draft");
    await until(b, "document.querySelector('input[name=ifMatch]').value === '\"saved\"'");
    assert.equal(await b.evaluate("window.currentItem.note[0].label[0].value"), "Remote note");
    assert.deepEqual(await b.evaluate("Object.keys(window.itemSubmissions.at(-1).payload)"), ["custodial_history"]);
    assert.equal(await b.evaluate("window.itemSubmissions.at(-1).etag"), '"current"');
    await clickText("Load current version for comparison");
    await until(b, "!!document.querySelector('[aria-label=\"Compare local changes and current version\"]')");
    await clickText("Apply choices and continue editing");
    assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Local custody");
    // Explicit discard confirmation: rejecting it keeps both comparison and input.
    await fill("/custodial_history/0/value", "Unsaved again");
    await clickText("Load current version for comparison");
    await until(b, "document.querySelectorAll('input[name=\"choice-custodial_history\"]').length === 2");
    await b.evaluate("document.querySelectorAll('input[name=\"choice-custodial_history\"]')[1].click()");
    await clickText("Apply choices and continue editing");
    assert.equal(await b.evaluate("window.itemConfirms"), 1);
    assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Unsaved again");
    await b.evaluate("window.itemConfirmResult = true");
    await clickText("Apply choices and continue editing");
    await until(b, `${field('/custodial_history/0/value')}.value === 'Local custody'`);
    await fill("/custodial_history/0/value", "Keep when moved");
    await b.evaluate("window.currentItem.item_of = { uri: 'urn:other-instance' }");
    await clickText("Load current version for comparison");
    await until(b, "document.querySelector('[role=alert]')?.textContent.includes('linked Instance changed')");
    assert.equal(await b.evaluate(`${field('/custodial_history/0/value')}.value`), "Keep when moved");
    assert.equal(await b.evaluate("document.querySelector('button[type=submit]').disabled"), true);
    await navigate("/?locale=pt&create=1");
    await clickText("Salvar item como rascunho");
    assert.equal(await b.evaluate("window.itemSubmissions.length"), 0);
    await clickText("Adicionar — Identificadores externos");
    await fill("/identified_by/0/value", "NEW-ITEM");
    await clickText("Validar antes de criar");
    await until(b, "document.body.textContent.includes('Pré-validação concluída')");
    await b.evaluate("window.nextItemStatus = 200");
    await clickText("Salvar item como rascunho");
    await until(b, "window.itemSubmissions.length === 1");
    assert.equal(await b.evaluate("window.itemSubmissions[0].payload.identified_by[0].value"), "NEW-ITEM");
    assert.equal(await b.evaluate("window.itemSubmissions[0].payload.item_of.kind"), "local");
    assert.equal(await b.evaluate("window.itemSubmissions[0].payload.item_of.entity_type"), "Instance");
  } finally {
    browser?.close(); control?.close();
    if (child.exitCode === null) { child.kill("SIGTERM"); await new Promise<void>(resolve => child.once("exit", () => resolve())); }
    await new Promise<void>(resolve => server.close(() => resolve()));
    await rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
});
