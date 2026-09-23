/* Functional checks against the generated, offline HTML. Run: npm run test:ui. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const artifacts = path.join(root, "artifacts");
const output = path.join(root, "outputs");
const report = { startedAt: new Date().toISOString(), nodeVersion: process.version, checks: [], pageErrors: [], consoleErrors: [], requests: [] };
const normalizeCSV = (value) => value.replace(/^\uFEFF/, "").replace(/\r\n/g, "\n");
const numericText = (value) => Number(value.replace(/[^0-9]/g, ""));

function browserPath() {
  if (process.env.BROWSER_PATH) {
    assert(fs.existsSync(process.env.BROWSER_PATH), `BROWSER_PATH not found: ${process.env.BROWSER_PATH}`);
    return process.env.BROWSER_PATH;
  }
  const candidates = [
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, "Google", "Chrome", "Application", "chrome.exe"),
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate));
}

async function main() {
  fs.mkdirSync(artifacts, { recursive: true });
  const htmlPath = path.join(output, "graph_view.html");
  assert(fs.existsSync(htmlPath), "Run the Python pipeline before the UI checks: outputs/graph_view.html is missing");
  const browser = await chromium.launch({ headless: true, executablePath: browserPath() });
  report.browserVersion = browser.version();
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true, reducedMotion: "reduce" });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  page.on("pageerror", (error) => report.pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") report.consoleErrors.push(message.text()); });
  await context.route("**/*", async (route) => {
    report.requests.push(route.request().url());
    await route.abort();
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
  const queueIds = () => page.locator("#node-list [data-node-id]").evaluateAll((elements) => elements.map((element) => element.dataset.nodeId));
  const showNode = async (id) => {
    await page.locator("#gid").fill(String(id));
    await page.locator("#gid").press("Enter");
    await page.waitForFunction((gid) => {
      const text = document.querySelector("#details-heading").textContent;
      return text.replace(/GID\s*/i, "").trim() === gid;
    }, String(id));
  };
  const noHorizontalOverflow = async () => {
    const geometry = await page.evaluate(() => ({ width: window.innerWidth, content: document.documentElement.scrollWidth }));
    assert(geometry.content <= geometry.width + 1, `Page overflow: content=${geometry.content}, viewport=${geometry.width}`);
  };

  try {
    // setContent deliberately avoids navigating to local file:// URLs.
    await page.setContent(fs.readFileSync(htmlPath, "utf8"), { waitUntil: "load" });
    const data = await page.locator("#graph-data").evaluate((element) => JSON.parse(element.textContent));
    const byId = new Map(data.nodes.map((node) => [String(node.id), node]));
    const highestPriority = Math.max(...data.nodes.map((node) => node.priority));
    report.input = { nodes: data.nodes.length, edges: data.edges.length, components: data.meta.components, demo: data.meta.demo };

    await check("Initial counts and highest-priority selection", async () => {
      assert.equal(numericText(await page.locator("#total-nodes").innerText()), data.nodes.length);
      assert.equal(numericText(await page.locator("#total-edges").innerText()), data.edges.length);
      assert.equal(numericText(await page.locator("#total-components").innerText()), data.meta.components);
      const ids = await queueIds();
      assert(ids.length > 0);
      assert.equal(byId.get(ids[0]).priority, highestPriority);
      assert.equal((await page.locator("#details-heading").innerText()).replace(/GID\s*/i, "").trim(), ids[0]);
      assert.equal(await page.locator("#scope-node").getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#scope-all").getAttribute("aria-pressed"), "false");
      assert.equal(await page.locator("#source-notice strong").innerText(), data.meta.demo ? "Демонстрационные данные" : "Проверьте источник");
      assert.match(await page.locator("#source-text").innerText(), data.meta.demo ? /синтетический/i : /происхождение/i);
    });

    await check("Exact GID search across all nodes", async () => {
      const node = data.nodes[data.nodes.length - 1];
      await showNode(node.id);
      await page.locator("#details-content").getByText("Почему присвоена роль", { exact: true }).click();
      assert(await page.locator("#details-content").getByText(node.evidence, { exact: true }).isVisible());
      assert((await page.locator("#details-content").innerText()).includes(node.evidence));
    });

    await check("Missing and invalid GIDs are explained", async () => {
      let missing = 9223372036854775807n;
      while (byId.has(missing.toString())) missing -= 1n;
      const previous = await page.locator("#details-heading").innerText();
      await page.locator("#gid").fill(missing.toString());
      await page.locator("#gid").press("Enter");
      assert.match(await page.locator("#search-message").innerText(), /не найден|нет.*(узл|gid)|отсутствует/i);
      assert.equal(await page.locator("#details-heading").innerText(), previous);
      await page.locator("#gid").fill("not-a-gid");
      await page.locator("#gid").press("Enter");
      assert((await page.locator("#search-message").innerText()).trim().length > 0);
      assert.equal(await page.locator("#details-heading").innerText(), previous);
      assert.equal(await page.locator("#gid").getAttribute("aria-invalid"), "true");
      await page.locator("#gid").fill("");
      await page.locator("#gid").press("Enter");
      assert.match(await page.locator("#search-message").innerText(), /введите/i);
      assert.equal(await page.locator("#details-heading").innerText(), previous);
      assert.equal(await page.locator("#gid").getAttribute("aria-invalid"), "true");
    });

    await check("Role, component and cluster filters; reset", async () => {
      const sample = data.nodes.find((node) => node.role !== "peripheral") || data.nodes[0];
      for (const [selector, field] of [["#role-filter", "role"], ["#component-filter", "component"], ["#cluster-filter", "cluster"]]) {
        await page.locator("#reset-filters").click();
        await page.locator(selector).selectOption(String(sample[field]));
        const ids = await queueIds();
        assert(ids.length > 0);
        assert(ids.every((id) => String(byId.get(id)[field]) === String(sample[field])), `Incorrect ${field} filter`);
      }
      await page.locator("#reset-filters").click();
      for (const selector of ["#role-filter", "#component-filter", "#cluster-filter"]) {
        assert.equal(await page.locator(selector).inputValue(), "all");
      }
    });

    await check("Global GID search clears incompatible filters", async () => {
      const sample = data.nodes[0];
      const outside = data.nodes.find((node) => node.component !== sample.component);
      assert(outside, "Dataset must have more than one component");
      await page.locator("#role-filter").selectOption(sample.role);
      await page.locator("#component-filter").selectOption(String(sample.component));
      await page.locator("#cluster-filter").selectOption(String(sample.cluster));
      await showNode(outside.id);
      for (const selector of ["#role-filter", "#component-filter", "#cluster-filter"]) {
        assert.equal(await page.locator(selector).inputValue(), "all");
      }
      assert.match(await page.locator("#search-message").innerText(), /фильтры сброшены/i);
      assert.equal(await page.locator("#gid").getAttribute("aria-invalid"), "false");
    });

    await check("Empty filter result and recovery", async () => {
      const roles = [...new Set(data.nodes.map((node) => node.role))];
      const components = [...new Set(data.nodes.map((node) => node.component))];
      const pair = components.flatMap((component) => roles.map((role) => ({ component, role })))
        .find(({ component, role }) => !data.nodes.some((node) => node.component === component && node.role === role));
      assert(pair, "Dataset must include an empty component/role combination for this scenario");
      await page.locator("#component-filter").selectOption(String(pair.component));
      await page.locator("#role-filter").selectOption(pair.role);
      assert.equal((await queueIds()).length, 0);
      assert.match(await page.locator("#node-list").innerText(), /нет|не найден|не соответств/i);
      await page.locator("#reset-filters").click();
      assert((await queueIds()).length > 0);
    });

    await check("Queue pagination", async () => {
      const firstPage = await queueIds();
      assert(await page.locator("#nodes-next").isEnabled());
      await page.locator("#nodes-next").click();
      const secondPage = await queueIds();
      assert(secondPage.length > 0);
      assert(secondPage.every((id) => !firstPage.includes(id)));
      await page.locator("#nodes-prev").click();
      assert.deepEqual(await queueIds(), firstPage);
    });

    await check("Keyboard node selection preserves useful focus", async () => {
      const row = page.locator("#node-list [data-node-id]").nth(1);
      const selectedId = await row.getAttribute("data-node-id");
      await row.focus();
      await row.press("Enter");
      assert.equal((await page.locator("#details-heading").innerText()).replace(/GID\s*/i, "").trim(), selectedId);
      assert(await page.evaluate((id) => document.activeElement?.dataset.nodeId === id
        && document.getElementById("node-list").contains(document.activeElement), selectedId));

      const connected = data.nodes.find((node) => node.inDegree + node.outDegree > 0);
      await showNode(connected.id);
      await page.locator("#scope-node").click();
      const diagramNodes = page.locator("#neighborhood [data-node-id]");
      const ids = await diagramNodes.evaluateAll((nodes) => nodes.map((node) => node.dataset.nodeId));
      const index = ids.findIndex((id) => id !== connected.id);
      assert(index >= 0, "Expected a keyboard-accessible SVG neighbor");
      const neighbor = ids[index];
      await diagramNodes.nth(index).focus();
      await diagramNodes.nth(index).press("Enter");
      assert.equal((await page.locator("#details-heading").innerText()).replace(/GID\s*/i, "").trim(), neighbor);
      assert(await page.evaluate((id) => document.activeElement?.dataset.nodeId === id
        && document.getElementById("neighborhood").contains(document.activeElement), neighbor));
    });

    await check("All connections are paginated; neighbor navigation", async () => {
      const hub = data.nodes.reduce((best, node) => node.inDegree + node.outDegree > best.inDegree + best.outDegree ? node : best);
      await showNode(hub.id);
      await page.locator("#scope-all").click();
      await page.locator("#view-table").click();
      assert(await page.locator("#connections-view").isVisible());
      assert.equal(await page.locator("#view-table").getAttribute("aria-pressed"), "true");
      assert.equal(await page.locator("#view-diagram").getAttribute("aria-pressed"), "false");
      assert.equal(await page.locator("#scope-node").getAttribute("aria-pressed"), "true");
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(artifacts, "ui-table.png"), fullPage: true });
      const expected = data.edges.filter((edge) => edge.s === hub.id || edge.t === hub.id);
      const observed = [];
      let pages = 0;
      do {
        const rows = await page.locator("#connection-rows tr").evaluateAll((elements) => elements.map((row) => ({
          id: row.querySelector("[data-node-id]")?.dataset.nodeId,
          direction: row.cells[1]?.textContent,
          amount: row.cells[2]?.textContent,
          count: row.cells[3]?.textContent,
        })));
        assert(rows.length > 0);
        rows.forEach((row) => {
          assert(byId.has(row.id), `Unknown neighbor ${row.id}`);
          assert.match(row.direction, /Входящий|Исходящий/);
          const incoming = row.direction.includes("Входящий");
          const edge = expected.find((candidate) => candidate.s === (incoming ? row.id : hub.id)
            && candidate.t === (incoming ? hub.id : row.id));
          assert(edge, `Direction mismatch for neighbor ${row.id}`);
          assert.equal(numericText(row.amount), edge.v, `Incorrect amount for ${edge.s} -> ${edge.t}`);
          assert.equal(numericText(row.count), edge.count, `Incorrect transaction count for ${edge.s} -> ${edge.t}`);
          observed.push(`${edge.s}->${edge.t}`);
        });
        pages += 1;
        assert(pages <= expected.length + 1, "Connection pagination failed to terminate");
        if (!(await page.locator("#edges-next").isEnabled())) break;
        await page.locator("#edges-next").click();
      } while (true);
      assert.equal(observed.length, expected.length);
      assert.deepEqual(observed.slice().sort(), expected.map((edge) => `${edge.s}->${edge.t}`).sort());
      const link = page.locator("#connection-rows [data-node-id]").first();
      const neighbor = await link.getAttribute("data-node-id");
      await link.click();
      assert.equal((await page.locator("#details-heading").innerText()).replace(/GID\s*/i, "").trim(), neighbor);
      await page.locator("#view-diagram").click();
      assert.equal(await page.locator("#view-diagram").getAttribute("aria-pressed"), "true");
    });

    await check("Fourth-hop boundary is explained for 444 nodes", async () => {
      const boundary = data.nodes.filter((node) => node.depth === 4);
      assert.equal(boundary.length, 444);
      assert(boundary.every((node) => node.outDegree === 0 && node.role !== "terminal"));
      await showNode(boundary[0].id);
      const warning = page.locator("#details-content .boundary-note");
      assert(await warning.isVisible());
      assert.match(await warning.innerText(), /граница выгрузки/i);
      assert.match(await warning.innerText(), /не означает/i);
    });

    await check("Full network, color mode, zoom and fit", async () => {
      await page.locator("#scope-all").click();
      assert(await page.locator("#network").isVisible());
      assert(!(await page.locator("#neighborhood").isVisible()), "Neighborhood must not show below the network canvas");
      assert.equal(await page.locator("#scope-all").getAttribute("aria-pressed"), "true");
      const before = await page.locator("#zoom-value").innerText();
      await page.locator("#zoom-in").click();
      assert.notEqual(await page.locator("#zoom-value").innerText(), before);
      const buttonZoom = numericText(await page.locator("#zoom-value").innerText());
      await page.locator("#network").focus();
      await page.locator("#network").press("=");
      const keyboardZoom = numericText(await page.locator("#zoom-value").innerText());
      assert(keyboardZoom > buttonZoom);
      await page.locator("#network").press("-");
      assert(numericText(await page.locator("#zoom-value").innerText()) < keyboardZoom);
      await page.locator("#network").press("Home");
      assert.equal(await page.locator("#zoom-value").innerText(), "100%");
      await page.locator("#mode").selectOption("cluster");
      assert.equal(await page.locator("#scope-all").getAttribute("aria-pressed"), "true");
      await page.locator("#fit").click();
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(artifacts, "ui-network.png"), fullPage: true });
      await page.locator("#mode").selectOption("role");
      await page.locator("#scope-node").click();
      assert(await page.locator("#neighborhood").isVisible());
    });

    await check("Help dialog Escape and focus restoration", async () => {
      await page.locator("#help-open").click();
      assert(await page.locator("#help-dialog").isVisible());
      assert(await page.locator("#help-dialog").evaluate((dialog) => dialog.contains(document.activeElement)));
      await page.keyboard.press("Escape");
      assert(!(await page.locator("#help-dialog").isVisible()));
      assert.equal(await page.evaluate(() => document.activeElement.id), "help-open");
    });

    await check("Modal keeps Tab and Shift+Tab focus inside", async () => {
      await page.locator("#help-open").click();
      for (const key of ["Tab", "Shift+Tab"]) {
        for (let i = 0; i < 6; i += 1) {
          await page.keyboard.press(key);
          const focus = await page.locator("#help-dialog").evaluate((dialog) => ({
            inside: dialog.contains(document.activeElement),
            element: document.activeElement?.id || document.activeElement?.tagName,
          }));
          assert(focus.inside, `${key} iteration ${i + 1} moved modal focus to ${focus.element}`);
        }
      }
      await page.keyboard.press("Escape");
      assert.equal(await page.evaluate(() => document.activeElement.id), "help-open");
    });

    await check("Three CSV downloads match generated files", async () => {
      const downloadsDir = path.join(artifacts, "downloads");
      fs.mkdirSync(downloadsDir, { recursive: true });
      for (const name of ["nodes_roles.csv", "clusters.csv", "top_nodes.csv"]) {
        await page.locator("#export-file").selectOption(name);
        const downloadPromise = page.waitForEvent("download");
        await page.locator("#download").click();
        const download = await downloadPromise;
        assert.equal(download.suggestedFilename(), name);
        assert.equal(await download.failure(), null);
        const destination = path.join(downloadsDir, name);
        await download.saveAs(destination);
        const actual = normalizeCSV(fs.readFileSync(destination, "utf8"));
        const expected = normalizeCSV(fs.readFileSync(path.join(output, name), "utf8"));
        assert.equal(actual, normalizeCSV(data.downloads[name]), `${name} differs from embedded data`);
        assert.equal(actual, expected, `${name} differs from pipeline CSV`);
        assert(actual.includes(name === "clusters.csv" ? "cluster_id,n_nodes,n_seed" : "gid,role"));
      }
    });

    await check("Desktop and mobile layouts", async () => {
      await page.locator("#reset-filters").click();
      await showNode((await queueIds())[0]);
      await page.evaluate(() => window.scrollTo(0, 0));
      await noHorizontalOverflow();
      await page.screenshot({ path: path.join(artifacts, "ui-desktop.png"), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      await noHorizontalOverflow();
      await showNode(data.nodes[data.nodes.length - 1].id);
      assert(await page.locator("#details-content").isVisible());
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: path.join(artifacts, "ui-mobile.png"), fullPage: true });
    });

    await check("320, 640 and 768px reflow and mobile table navigation", async () => {
      report.additionalViewportWidths = [320, 640, 768];
      const hub = data.nodes.reduce((best, node) => node.inDegree + node.outDegree > best.inDegree + best.outDegree ? node : best);
      for (const width of report.additionalViewportWidths) {
        await page.setViewportSize({ width, height: 900 });
        await showNode(hub.id);
        await page.locator("#view-diagram").click();
        await noHorizontalOverflow();
        assert(await page.locator("#neighborhood").isVisible());
        await page.locator("#view-table").click();
        await noHorizontalOverflow();
        assert(await page.locator("#connections-view").isVisible());
        const neighborButton = page.locator("#connection-rows [data-node-id]").first();
        const neighbor = await neighborButton.getAttribute("data-node-id");
        await neighborButton.click();
        assert.equal((await page.locator("#details-heading").innerText()).replace(/GID\s*/i, "").trim(), neighbor);
        assert(await page.locator("#details-content").isVisible());
        await noHorizontalOverflow();
      }
      // Viewport-width checks do not emulate browser zoom at 200%.
      await page.setViewportSize({ width: 390, height: 844 });
      await page.locator("#view-diagram").click();
    });

    await check("No JavaScript exceptions or external network requests", async () => {
      assert.deepEqual(report.pageErrors, []);
      assert.deepEqual(report.requests, []);
      assert.deepEqual(report.consoleErrors, []);
    });
    report.status = "passed";
  } catch (error) {
    report.status = "failed";
    report.error = error.stack || error.message;
    await page.screenshot({ path: path.join(artifacts, "ui-failure.png"), fullPage: true }).catch(() => {});
    throw error;
  } finally {
    report.finishedAt = new Date().toISOString();
    fs.writeFileSync(path.join(artifacts, "ui-test-report.json"), JSON.stringify(report, null, 2) + "\n");
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  if (!report.finishedAt) {
    report.status = "failed";
    report.error = error.stack || error.message;
    report.finishedAt = new Date().toISOString();
    fs.mkdirSync(artifacts, { recursive: true });
    fs.writeFileSync(path.join(artifacts, "ui-test-report.json"), JSON.stringify(report, null, 2) + "\n");
  }
  process.exitCode = 1;
});
