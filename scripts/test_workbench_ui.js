/* End-to-end checks for the loopback import workbench. No existing data is used. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const artifacts = path.join(root, "artifacts");
const report = { startedAt: new Date().toISOString(), nodeVersion: process.version, checks: [], pageErrors: [], consoleErrors: [], requests: [], unexpectedHTTP: [] };

function browserPath() {
  if (process.env.BROWSER_PATH) {
    assert(fs.existsSync(process.env.BROWSER_PATH), `BROWSER_PATH not found: ${process.env.BROWSER_PATH}`);
    return process.env.BROWSER_PATH;
  }
  return ["C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe")]
    .filter(Boolean).find(candidate => fs.existsSync(candidate));
}

function pythonPath() {
  const binary = process.platform === "win32" ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python");
  assert(fs.existsSync(binary), "Create .venv and install requirements before browser checks");
  return binary;
}

function startServer(storage) {
  const code = ["import json, sys", "from pathlib import Path", "from workbench.server import create_server",
    "server = create_server(port=0, storage=Path(sys.argv[1]))", "print(json.dumps({'port': server.server_address[1]}), flush=True)",
    "try:", "    server.serve_forever(poll_interval=0.1)", "finally:", "    server.server_close()"].join("\n");
  const child = spawn(pythonPath(), ["-X", "utf8", "-u", "-c", code, storage], { cwd: root, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let stdout = "", stderr = "";
  child.stderr.on("data", value => { stderr += value.toString(); });
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => { child.kill(); reject(new Error(`Local test server did not start: ${stderr}`)); }, 15000);
    child.once("error", error => { clearTimeout(timeout); reject(error); });
    child.once("exit", code => { clearTimeout(timeout); reject(new Error(`Local server exited (${code}): ${stderr}`)); });
    child.stdout.on("data", value => {
      stdout += value.toString();
      if (!stdout.includes("\n")) return;
      try {
        const { port } = JSON.parse(stdout.split(/\r?\n/, 1)[0]);
        assert(Number.isInteger(port) && port > 0);
        clearTimeout(timeout);
        resolve({ child, origin: `http://127.0.0.1:${port}`, stderr: () => stderr });
      } catch (error) { clearTimeout(timeout); child.kill(); reject(error); }
    });
  });
}

const file = (name, content) => ({ name, mimeType: "application/octet-stream", buffer: Buffer.isBuffer(content) ? content : Buffer.from(content, "utf8") });
const csv = rows => rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(",")).join("\n") + "\n";
const cp1251 = value => Buffer.from([...value].map(char => {
  const code = char.codePointAt(0);
  if (code < 128) return code;
  if (code >= 0x410 && code <= 0x44f) return code - 0x410 + 0xc0;
  if (code === 0x401) return 0xa8;
  if (code === 0x451) return 0xb8;
  throw new Error("Test CP1251 encoder supports ASCII and Russian letters only");
}));

async function main() {
  fs.mkdirSync(artifacts, { recursive: true });
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "workbench-ui-"));
  let server, browser, page;
  const expectedHTTP = new Set();
  try {
    server = await startServer(path.join(temporary, "store"));
    browser = await chromium.launch({ headless: true, executablePath: browserPath() });
    report.browserVersion = browser.version();
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, reducedMotion: "reduce" });
    await context.route("**/*", async route => {
      const url = route.request().url();
      report.requests.push(url);
      if (new URL(url).origin !== server.origin) await route.abort();
      else await route.continue();
    });
    page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on("pageerror", error => report.pageErrors.push(error.message));
    page.on("console", message => {
      if (message.type() !== "error") return;
      const resourceStatus = message.text().match(/server responded with a status of (\d+)/i)?.[1];
      const key = `${resourceStatus} ${message.location().url}`;
      if (!expectedHTTP.has(key)) report.consoleErrors.push({ message: message.text(), url: message.location().url });
    });
    page.on("response", response => {
      if (response.status() >= 400 && !expectedHTTP.has(`${response.status()} ${response.url()}`)) {
        report.unexpectedHTTP.push({ url: response.url(), status: response.status() });
      }
    });
    const check = async (name, action) => {
      const started = Date.now();
      try {
        await action();
        report.checks.push({ name, status: "passed", milliseconds: Date.now() - started });
        console.log(`PASS ${name}`);
      } catch (error) {
        report.checks.push({ name, status: "failed", message: error.message, milliseconds: Date.now() - started });
        throw error;
      }
    };
    const idle = () => page.waitForFunction(() => !document.querySelector("#file-input").disabled);
    const graph = () => page.frameLocator("#result-frame");
    const graphData = () => graph().locator("#graph-data").evaluate(element => JSON.parse(element.textContent));
    const source = index => page.locator(`[data-source="${index}"]`);
    const configure = async (currency = "USD", decimals = "2") => {
      await page.locator("#currency").fill(currency);
      await page.locator("#decimals").fill(decimals);
    };
    const load = async files => {
      await page.locator("#new-import").click();
      await page.locator("#file-input").setInputFiles(files);
      await idle();
      assert.equal(await page.locator(".file-row").count(), files.length);
      await page.locator("#to-mapping").click();
      assert(await page.locator("#step-mapping").isVisible());
    };
    const preview = async () => {
      await page.locator("#validate").click();
      await idle();
      const error = await page.locator("#error").isVisible() ? await page.locator("#error").innerText() : "";
      assert(await page.locator("#step-review").isVisible(), `Preview did not succeed: ${error}`);
    };
    const execute = async () => {
      const acceptedPromise = page.waitForResponse(response => response.url() === `${server.origin}/api/runs` && response.request().method() === "POST");
      await page.locator("#start-run").click();
      const acceptedResponse = await acceptedPromise;
      assert.equal(acceptedResponse.status(), 202);
      const run = await acceptedResponse.json();
      await page.waitForFunction(() => document.querySelector("#run-status").textContent === "Готово", null, { timeout: 20000 });
      await graph().locator("#node-list [data-node-id]").first().waitFor({ state: "visible", timeout: 20000 });
      // Chromium may throttle ResizeObserver in an offscreen iframe. Bring the
      // actual result into view before waiting for its measured auto-height.
      await page.locator("#result-frame").scrollIntoViewIfNeeded();
      await page.waitForFunction(() => document.querySelector("#result-frame").style.height !== "");
      await idle();
      return run.id;
    };
    const search = async id => {
      await graph().locator("#gid").fill(id);
      await graph().locator("#gid").press("Enter");
      assert.equal((await graph().locator("#details-heading").innerText()).replace(/^GID\s*/i, "").trim(), id);
    };
    const noOverflow = async () => {
      await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const widths = await page.evaluate(() => ({ content: document.documentElement.scrollWidth, viewport: innerWidth }));
      assert(widths.content <= widths.viewport + 1, `Workbench horizontal overflow: ${JSON.stringify(widths)}`);
      if (await page.locator("#graph-container").isVisible()) {
        const handle = await page.locator("#result-frame").elementHandle();
        const rendered = await handle.contentFrame();
        // ResizeObserver -> postMessage is asynchronous, especially after a
        // viewport change. Wait for the measured invariant, not a fixed sleep.
        await rendered.waitForFunction(() => document.documentElement.scrollHeight <= innerHeight + 2, null, { timeout: 3000 });
        await handle.dispose();
        const frameWidths = await graph().locator("html").evaluate(element => ({ content: element.scrollWidth, viewport: innerWidth }));
        assert(frameWidths.content <= frameWidths.viewport + 1, `Viewer horizontal overflow: ${JSON.stringify(frameWidths)}`);
        const frameHeights = await graph().locator("html").evaluate(element => ({ content: element.scrollHeight, viewport: innerHeight }));
        assert(frameHeights.content <= frameHeights.viewport + 2, `Viewer requires nested vertical scrolling: ${JSON.stringify(frameHeights)}`);
      }
    };
    const download = async name => {
      const event = page.waitForEvent("download");
      await page.locator(`[data-download="${name}"]`).click();
      const result = await event;
      assert.equal(result.suggestedFilename(), name);
      assert.equal(await result.failure(), null);
      const destination = path.join(temporary, name);
      await result.saveAs(destination);
      await idle();
      return fs.readFileSync(destination, "utf8").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
    };
    const verifyReachableDetails = async (label, screenshotName) => {
      const details = await graph().locator(".details").evaluate(element => {
        const rectangle = element.getBoundingClientRect();
        return { top: rectangle.top + scrollY, bottom: rectangle.bottom + scrollY, frameHeight: innerHeight };
      });
      assert(details.top >= 0 && details.bottom <= details.frameHeight + 2, `Details escape the iframe viewport: ${JSON.stringify(details)}`);
      await graph().locator("#details-heading").scrollIntoViewIfNeeded();
      await graph().locator("#details-content").scrollIntoViewIfNeeded();
      await graph().locator("html").evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({ path: path.join(artifacts, screenshotName), fullPage: false });
      await graph().locator(".page-footer").scrollIntoViewIfNeeded();
      const footer = await graph().locator(".page-footer").boundingBox();
      assert(footer && footer.y >= -2 && footer.y + footer.height <= page.viewportSize().height + 2,
        `Graph footer is not reachable by page scrolling: ${JSON.stringify(footer)}`);
      assert(await page.evaluate(() => scrollY > 0), "Expected outer document scrolling to reach the graph footer");
      await graph().locator("html").evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      await page.screenshot({ path: path.join(artifacts, `workbench-result-footer-${label}.png`), fullPage: false });
      report.visualReachability ??= [];
      report.visualReachability.push({ label, details, footer, viewport: page.viewportSize() });
    };

    let firstRun;
    await check("Local startup, semantic controls and keyboard entry", async () => {
      await page.goto(server.origin, { waitUntil: "networkidle" });
      await idle();
      assert(await page.getByRole("heading", { name: "Начните с ваших файлов" }).isVisible());
      assert.match(await page.locator("#upload-limits").innerText(), /20.*100.*000/);
      await page.keyboard.press("Tab");
      assert.equal(await page.evaluate(() => document.activeElement.className), "skip");
      await page.keyboard.press("Enter");
      assert.equal(await page.evaluate(() => document.activeElement.id), "main");
      await noOverflow();
      await page.screenshot({ path: path.join(artifacts, "workbench-files-desktop.png"), fullPage: true });
    });

    await check("CSV mapping error is actionable and keeps focus", async () => {
      await load([file("transfers.csv", "src,dst,amount,date,currency\n001,A-2,12.50,2026-09-01,USD\nA-2,B,3.25,2026-09-02,USD\n001,B,0.25,2026-09-02,USD\n")]);
      assert.equal(await source(0).locator('[data-field="src"]').inputValue(), "src");
      assert.match(await source(0).locator(".preview-table").innerText(), /001/);
      await source(0).locator('[data-field="amount"]').selectOption("");
      expectedHTTP.add(`422 ${server.origin}/api/preview`);
      await page.locator("#validate").click();
      await page.locator("#error").waitFor({ state: "visible" });
      await idle();
      assert.match(await page.locator("#error").innerText(), /amount|Сумм|сопостав|обязательн/i);
      assert.equal(await page.evaluate(() => document.activeElement.id), "error");
      assert(!(await page.locator("#step-review").isVisible()));
      await source(0).locator('[data-field="amount"]').selectOption("amount");
      await configure();
      await page.locator("#top-n").fill("10");
      await page.locator("#back-files").click();
      await page.locator("#to-mapping").click();
      assert.equal(await source(0).locator('[data-field="amount"]').inputValue(), "amount");
      assert.equal(await page.locator("#currency").inputValue(), "USD");
      await page.screenshot({ path: path.join(artifacts, "workbench-mapping-desktop.png"), fullPage: true });
    });

    await check("Empty monetary precision is rejected rather than silently becoming zero", async () => {
      await page.locator("#decimals").fill("");
      await page.locator("#validate").click();
      await page.locator("#error").waitFor({ state: "visible" });
      await idle();
      assert.match(await page.locator("#error").innerText(), /Укажите.*знаков/);
      assert(!(await page.locator("#step-review").isVisible()));
      await page.locator("#decimals").fill("2");
    });

    await check("USD decimals, unknown observations and preview before execution", async () => {
      await preview();
      const summary = await page.locator("#review-summary").innerText();
      assert.match(summary, /USD/);
      assert.match(summary, /top-3/);
      assert.match(summary, /16[.,]00/);
      assert.match(summary, /Неизвест|неизвест|отсутств|Недоступ/i);
      assert.equal(await page.locator("[data-history]").count(), 0, "Preview must not create a saved run");
      firstRun = await execute();
      const data = await graphData();
      assert.equal(data.nodes.length, 3);
      assert.equal(data.edges.length, 3);
      assert.equal(data.meta.currency, "USD");
      assert.equal(data.meta.moneyScale, 2);
      assert.equal(data.edges.reduce((total, edge) => total + BigInt(edge.v), 0n), 1600n);
      assert(data.nodes.every(node => node.seed === null && node.depth === null));
      assert(data.nodes.every(node => !["terminal", "transit"].includes(node.role)));
      assert(data.nodes.some(node => node.id === "001"));
    });

    await check("Sandboxed graph executes with nonce; search and exact money work", async () => {
      assert.equal(await page.locator("#result-frame").getAttribute("sandbox"), "allow-scripts allow-downloads allow-forms");
      await search("001");
      await graph().locator("#view-table").click();
      const rows = await graph().locator("#connection-rows").innerText();
      assert.match(rows, /12[.,]50/);
      assert.match(rows, /0[.,]25/);
      const scriptNonce = await graph().locator("script:not([type])").evaluate(element => element.nonce);
      assert(scriptNonce.length >= 24);
      await graph().locator("#view-diagram").click();
      await noOverflow();
      await page.locator("#result-frame").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(artifacts, "workbench-desktop.png"), fullPage: true });
      await verifyReachableDetails("desktop", "workbench-result-details.png");
    });

    await check("Iframe height follows its content and rejects messages from another source", async () => {
      const before = await page.locator("#result-frame").evaluate(frame => frame.style.height);
      assert(parseInt(before, 10) > 320);
      await page.evaluate(() => window.postMessage({ type: "potoki:height", height: 19000 }, "*"));
      await noOverflow();
      assert.equal(await page.locator("#result-frame").evaluate(frame => frame.style.height), before);
    });

    await check("All six downloads match the saved analysis artifacts", async () => {
      for (const name of ["nodes_roles.csv", "clusters.csv", "top_nodes.csv", "report.json", "result.json", "graph_view.html"]) {
        const actual = await download(name);
        const expected = fs.readFileSync(path.join(temporary, "store", "runs", firstRun, "artifacts", name), "utf8").replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
        assert.equal(actual, expected, `${name} differs from completed run`);
        assert(actual.trim().length > 0, `${name} must not be empty`);
        if (name === "clusters.csv") assert.match(actual, /currency/);
      }
    });

    await check("Completed history reopens after page reload", async () => {
      await page.reload({ waitUntil: "networkidle" });
      await idle();
      assert.equal(await page.locator("[data-history]").count(), 1);
      await page.locator(`[data-history="${firstRun}"]`).click();
      await graph().locator("#node-list [data-node-id]").first().waitFor({ state: "visible" });
      await idle();
      assert.equal(await page.locator("#run-status").innerText(), "Готово");
      await search("001");
    });

    await check("375px workbench and viewer have no page overflow; keyboard focus works", async () => {
      await page.setViewportSize({ width: 375, height: 812 });
      await page.locator("#result-frame").scrollIntoViewIfNeeded();
      await noOverflow();
      await graph().locator("#gid").focus();
      const focusStyle = await graph().locator("#gid").evaluate(element => ({ focused: document.activeElement === element, width: getComputedStyle(element).outlineWidth }));
      assert(focusStyle.focused);
      assert(parseFloat(focusStyle.width) > 0);
      await graph().locator("#gid").fill("A-2");
      await graph().locator("#gid").press("Enter");
      await noOverflow();
      await page.locator("#result-frame").scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(artifacts, "workbench-mobile.png"), fullPage: true });
      await verifyReachableDetails("mobile", "workbench-result-details-mobile.png");
      await page.locator("#new-import").click();
      await noOverflow();
      await page.screenshot({ path: path.join(artifacts, "workbench-files-mobile.png"), fullPage: true });
      await load([file("mobile-preview.csv", "src,dst,amount\n001,B,1.25\n")]);
      await configure();
      await noOverflow();
      await page.screenshot({ path: path.join(artifacts, "workbench-mapping-mobile.png"), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
    });

    await check("CP1251 semicolon input and decimal comma are explicitly configured", async () => {
      expectedHTTP.add(`422 ${server.origin}/api/inspect`);
      await load([file("transfers-cp1251.csv", cp1251("отправитель;получатель;сумма\nА;Б;12,50\n"))]);
      await source(0).locator('[data-option="encoding"]').selectOption("cp1251");
      await source(0).locator('[data-option="delimiter"]').selectOption(";");
      await source(0).locator('[data-option="decimal"]').selectOption(",");
      await source(0).locator("[data-reinspect]").click();
      await idle();
      assert.match(await source(0).locator(".preview-table").innerText(), /отправитель/);
      for (const [fieldName, column] of [["src", "отправитель"], ["dst", "получатель"], ["amount", "сумма"]]) {
        await source(0).locator(`[data-field="${fieldName}"]`).selectOption(column);
      }
      await configure("RUB");
      await preview();
      await execute();
      const data = await graphData();
      assert.equal(data.meta.currency, "RUB");
      assert.deepEqual(data.nodes.map(node => node.id).sort(), ["А", "Б"]);
      assert.equal(data.edges[0].v, "1250");
    });

    await check("Nodes plus edges preserve isolated nodes and unknown transaction counts", async () => {
      await load([file("nodes.csv", "gid\n001\nA-2\nisolated\n"), file("edges.csv", "src,dst,amount\n001,A-2,2.50\n")]);
      await configure("EUR");
      await preview();
      await execute();
      const data = await graphData();
      assert.equal(data.nodes.length, 3);
      assert.equal(data.edges.length, 1);
      assert.equal(data.meta.components, 2);
      assert.equal(data.edges[0].count, null);
      await search("001");
      await graph().locator("#view-table").click();
      assert.match(await graph().locator("#connection-rows tr").first().locator("td").nth(3).innerText(), /—|неизвестно/);
      await search("isolated");
      assert.match(await graph().locator("#connections-empty").innerText(), /нет|Нет/);
    });

    await check("Three consistent tables agree and transaction counts are derived from real rows", async () => {
      await load([file("nodes.csv", "gid\n001\nA-2\nisolated\n"), file("edges.csv", "src,dst,amount\n001,A-2,2.50\n"),
        file("transfers.json", JSON.stringify([{ src: "001", dst: "A-2", amount: "1.25" }, { src: "001", dst: "A-2", amount: "1.25" }]))]);
      await configure("EUR");
      await preview();
      assert.match(await page.locator("#review-summary").innerText(), /совпад|дубл/i);
      await execute();
      const data = await graphData();
      assert.equal(BigInt(data.edges[0].count), 2n);
      assert.equal(data.edges[0].v, "250");
      assert.equal(data.meta.components, 2);
    });

    await check("Untrusted identifiers stay text; CSV formula-looking identifiers are escaped", async () => {
      const hostileId = '<img src="https://example.invalid/leak" onerror="window.__workbenchXSS=1">';
      await load([file("transfers-xss.csv", csv([["src", "dst", "amount"], ["001", hostileId, "12.50"], [hostileId, "=1+1", "1.00"]]))]);
      assert.equal(await source(0).locator("img").count(), 0);
      assert.equal(await page.evaluate(() => window.__workbenchXSS), undefined);
      await configure();
      await preview();
      await execute();
      await search(hostileId);
      assert.equal(await graph().locator("img").count(), 0);
      assert.equal(await graph().locator("html").evaluate(() => window.__workbenchXSS), undefined);
      await search("=1+1");
      assert((await download("nodes_roles.csv")).includes("'=1+1"));
      assert.equal(await page.locator("[data-history]").count(), 5);
    });

    await check("Transaction counts above JavaScript safe integers remain exact in the table", async () => {
      await load([file("edges-precision.csv", "src,dst,amount,n_tx\n001,A-2,2.50,9007199254740993\n")]);
      await configure("EUR");
      await preview();
      await execute();
      assert.equal((await graphData()).edges[0].count, "9007199254740993");
      await search("001");
      await graph().locator("#view-table").click();
      const text = await graph().locator("#connection-rows tr").first().locator("td").nth(3).innerText();
      assert.equal(text.replace(/\D/g, ""), "9007199254740993");
    });

    await check("XLSX formula on the first sheet is rejected; an explicit valid second sheet recovers", async () => {
      const destination = path.join(temporary, "two-sheets.xlsx");
      const code = ["import sys", "from openpyxl import Workbook", "wb = Workbook()", "first = wb.active", "first.title = 'Formulas'",
        "first.append(['src', 'dst', 'amount'])", "first.append(['001', 'B', '=1+1'])", "second = wb.create_sheet('Valid data')",
        "second.append(['src', 'dst', 'amount'])", "second.append(['001', 'B', '1.25'])", "wb.save(sys.argv[1])"].join("\n");
      const generated = spawnSync(pythonPath(), ["-X", "utf8", "-c", code, destination], { cwd: root, windowsHide: true, encoding: "utf8" });
      assert.equal(generated.status, 0, generated.stderr);
      await load([file("transfers.xlsx", fs.readFileSync(destination))]);
      assert.match(await source(0).locator(".error-box").innerText(), /формул/i);
      assert.equal(await source(0).locator(".mapping-grid select").count(), 0);
      await source(0).locator('[data-option="sheet"]').fill("Valid data");
      await source(0).locator("[data-reinspect]").click();
      await idle();
      assert.match(await source(0).locator(".preview-table").innerText(), /001/);
      assert.equal(await source(0).locator('[data-field="amount"]').inputValue(), "amount");
      await configure("USD");
      await preview();
      await execute();
      const data = await graphData();
      assert.equal(data.nodes.length, 2);
      assert.equal(data.edges[0].v, "125");
    });

    await check("No unexpected JavaScript, CSP, HTTP failures or external requests", async () => {
      assert.deepEqual(report.pageErrors, []);
      assert.deepEqual(report.consoleErrors, []);
      assert.deepEqual(report.unexpectedHTTP, []);
      assert(report.requests.length > 10, "Expected real local HTTP requests");
      assert(report.requests.every(url => new URL(url).origin === server.origin), "A request attempted to leave the loopback service");
      assert.equal(server.stderr(), "");
    });
    report.status = "passed";
  } catch (error) {
    report.status = "failed";
    report.error = error.stack || error.message;
    if (page) await page.screenshot({ path: path.join(artifacts, "workbench-failure.png"), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    if (browser) await browser.close();
    if (server?.child && server.child.exitCode === null) {
      await new Promise(resolve => { server.child.once("exit", resolve); server.child.kill(); setTimeout(resolve, 3000); });
    }
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(artifacts, "workbench-ui-test-report.json"), JSON.stringify(report, null, 2) + "\n");
    // Only remove the test-owned directory that this process just created.
    assert(path.dirname(temporary) === path.resolve(os.tmpdir()) && path.basename(temporary).startsWith("workbench-ui-"));
    fs.rmSync(temporary, { recursive: true, force: true, maxRetries:5, retryDelay:200 });
  }
}

main().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; });
