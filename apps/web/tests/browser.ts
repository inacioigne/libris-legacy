// Optional real Chromium probe. No browser dependency is added to the application.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";

export class DevTools {
  failures: string[] = [];
  documents: { path: string; status: number }[] = [];
  origins: string[] = [];
  private nextId = 0;
  private pending = new Map<number, { resolve: (value: Record<string, unknown>) => void; reject: (error: Error) => void }>();
  constructor(private socket: WebSocket) {
    socket.addEventListener("message", (event) => {
      const data = JSON.parse(String(event.data)) as Record<string, unknown>;
      if (data.method === "Network.responseReceived") {
        const params = data.params as { type: string; response: { url: string; status: number } };
        if (params.type === "Document") this.documents.push({ path: new URL(params.response.url).pathname, status: params.response.status });
      }
      if (data.method === "Network.requestWillBeSentExtraInfo") {
        const params = data.params as { headers: Record<string, string> };
        const origin = params.headers.Origin ?? params.headers.origin;
        if (origin) this.origins.push(origin);
      }
      if (data.method === "Network.loadingFailed") {
        const params = data.params as { errorText?: string };
        if (params.errorText) this.failures.push(params.errorText);
      }
      if (typeof data.id !== "number") return;
      const pending = this.pending.get(data.id);
      if (!pending) return;
      this.pending.delete(data.id);
      if (data.error) pending.reject(new Error("Browser protocol failed"));
      else pending.resolve(data.result as Record<string, unknown>);
    });
  }
  async send(method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const id = ++this.nextId;
    const result = new Promise<Record<string, unknown>>((resolve, reject) => { this.pending.set(id, { resolve, reject }); });
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }
  async evaluate(expression: string): Promise<unknown> {
    const result = await this.send("Runtime.evaluate", { expression, returnByValue: true });
    assert.equal(result.exceptionDetails, undefined, "Browser JavaScript failed");
    return (result.result as { value?: unknown }).value;
  }
  close() { this.socket.close(); }
}
export async function connect(url: string): Promise<DevTools> {
  const socket = new WebSocket(url);
  await new Promise<void>((resolve, reject) => {
    socket.addEventListener("open", () => resolve(), { once: true });
    socket.addEventListener("error", () => reject(new Error("Browser connection failed")), { once: true });
  });
  return new DevTools(socket);
}
export async function until(browser: DevTools, expression: string) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (await browser.evaluate(expression)) return;
    await delay(100);
  }
  const diagnostic = await browser.evaluate("JSON.stringify({protocol: location.protocol, host: location.hostname, path: location.pathname, auth: new URL(location.href).searchParams.get('auth'), violations: window.cspViolations, returnTo: document.querySelector('input[name=returnTo]')?.value})");
  throw new Error("Browser assertion timed out: " + String(diagnostic) + " failures=" + JSON.stringify(browser.failures) + " documents=" + JSON.stringify(browser.documents) + " origins=" + JSON.stringify(browser.origins));
}
export async function probeBrowser(origin: string, attackUrl: string, real?: {
  username: string; password: string; issuer: string; authorized: boolean;
  expireSession?: () => Promise<void>;
  providerUnavailable?: () => Promise<() => Promise<void>>;
}): Promise<void> {
  const profile = await mkdtemp(join(tmpdir(), "libris-m0407-chrome-"));
  // --no-sandbox only for this disposable local harness on restricted hosts.
  const child = spawn(process.env.TEST_WEB_CHROME_PATH ?? "/usr/bin/google-chrome", [
    "--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-background-networking",
    "--no-first-run", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let browser: DevTools | undefined, control: DevTools | undefined;
  const startup = new Promise<string>((resolve, reject) => {
    let output = "";
    child.stderr.on("data", (chunk: Buffer) => {
      output += chunk.toString();
      const url = output.match(/DevTools listening on (ws:\/\/[^\s]+)/)?.[1];
      if (url) resolve(url);
    });
    child.once("error", () => reject(new Error("Chrome unavailable")));
    child.once("exit", () => reject(new Error("Chrome exited before readiness")));
  });
  try {
    const websocket = await Promise.race([startup, delay(10000).then(() => { throw new Error("Chrome startup timeout"); })]);
    control = await connect(websocket);
    const { targetId } = await control.send("Target.createTarget", { url: "about:blank" });
    const targets = await (await fetch(`http://${new URL(websocket).host}/json/list`)).json() as { id: string; webSocketDebuggerUrl: string }[];
    const target = targets.find((target) => target.id === targetId);
    assert.ok(target);
    browser = await connect(target.webSocketDebuggerUrl);
    await browser.send("Page.enable");
    await browser.send("Network.enable");
    await browser.send("Page.addScriptToEvaluateOnNewDocument", { source: `
      window.cspViolations = [];
      document.addEventListener('securitypolicyviolation', (event) => window.cspViolations.push(event.effectiveDirective));
    ` });
    if (real) {
      // All credentials stay in this disposable browser/profile and CDP memory.
      await browser.send("Page.navigate", { url: origin + "/oidc/callback?state=invalid&code=synthetic" });
      await until(browser, "location.pathname === '/pt/opac' && document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
      await browser.send("Page.navigate", { url: origin + "/en/admin" });
      await until(browser, "document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
      assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), false);
      await browser.evaluate("document.querySelector('form[action=\"/oidc/login\"]').requestSubmit()");
      await until(browser, "!!document.querySelector('#kc-form-login')");
      assert.equal(await browser.evaluate("location.origin"), new URL(real.issuer).origin);
      await browser.evaluate(`document.querySelector('#username').value = ${JSON.stringify(real.username)};
        document.querySelector('#password').value = ${JSON.stringify(real.password)};
        document.querySelector('#kc-form-login').requestSubmit()`);
      await until(browser, "location.pathname === '/en/admin' && document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/logout\"]')");
      assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), real.authorized);
      assert.equal(await browser.evaluate("document.documentElement.outerHTML.includes('access_token') || document.documentElement.outerHTML.includes('refresh_token')"), false);
      assert.deepEqual(await browser.evaluate("window.cspViolations"), []);
      await browser.send("Page.navigate", { url: origin + "/pt/admin" });
      await until(browser, "document.readyState === 'complete' && document.documentElement.lang === 'pt-BR' && !!document.querySelector('form[action=\"/oidc/logout\"]')");
      assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), real.authorized);
      assert.deepEqual(await browser.evaluate("window.cspViolations"), []);
      if (real.expireSession) {
        await real.expireSession();
        await browser.send("Page.navigate", { url: origin + "/en/admin" });
        await until(browser, "document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
        assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), false);
        return;
      }
      if (real.providerUnavailable) {
        const restore = await real.providerUnavailable();
        try {
          await browser.send("Page.navigate", { url: origin + "/en/admin" });
          // An uncertain refresh revokes the session under the existing contract.
          await until(browser, "document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
          assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), false);
        } finally { await restore(); }
        return;
      }
      await browser.evaluate("document.querySelector('form[action=\"/oidc/logout\"]').requestSubmit()");
      await until(browser, "location.pathname === '/realms/libris/protocol/openid-connect/logout'");
      await browser.send("Page.navigate", { url: origin + "/en/admin" });
      await until(browser, "document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
      assert.equal(await browser.evaluate("!!document.querySelector('#capabilities-title')"), false);
      return;
    }
    await browser.send("Page.navigate", { url: origin + "/en/opac" });
    await until(browser, "document.readyState === 'complete' && !!document.querySelector('form[action=\"/oidc/login\"]')");
    // Wait for React hydration, then a Link must navigate without reloading the document.
    await until(browser, "!!Object.keys(document.querySelector('a[href=\"/pt/opac\"]')).find(k => k.startsWith('__reactProps'))");
    assert.deepEqual(await browser.evaluate("window.cspViolations"), []);
    await browser.evaluate("document.querySelector('a[href=\"/pt/opac\"]').click()");
    await until(browser, "location.pathname === '/pt/opac' && document.documentElement.lang === 'pt-BR' && document.querySelector('input[name=returnTo]')?.value === '/pt/opac'");
    assert.equal(await browser.evaluate("performance.getEntriesByType('navigation')[0].name"), origin + "/en/opac");
    // Native form -> provider redirect -> callback -> authenticated SSR, with CSP enforced.
    await browser.evaluate("document.querySelector('form[action=\"/oidc/login\"]').requestSubmit()");
    await until(browser, "location.pathname === '/pt/opac' && document.body.textContent.includes('oidc:sha256:http')");
    assert.deepEqual(await browser.evaluate("window.cspViolations"), []);
    assert.equal(await browser.evaluate("document.documentElement.outerHTML.includes('http-access-secret') || document.documentElement.outerHTML.includes('http-refresh-secret')"), false);
    // Session-bound CSRF form and RP redirect remain functional in a real browser.
    await browser.evaluate("document.querySelector('form[action=\"/oidc/logout\"]').requestSubmit()");
    await until(browser, "location.pathname === '/logout'");
    // CDP evaluate is a privileged debugger API and can bypass CSP. Use a parsed
    // hostile HTML response from the mock, with the exact compiled app CSP instead.
    await browser.send("Page.navigate", { url: attackUrl });
    await until(browser, "document.readyState === 'complete' && window.cspViolations.length > 0");
    assert.equal(await browser.evaluate("window.injectionExecuted === true"), false);
    assert.ok((await browser.evaluate("window.cspViolations") as string[]).includes("script-src-elem"));
  } finally {
    browser?.close(); control?.close();
    if (child.exitCode === null) {
      child.kill("SIGTERM");
      await new Promise<void>((resolve) => child.once("exit", () => resolve()));
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 20, retryDelay: 100 });
  }
}
