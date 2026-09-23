/* Palette contract shared by source checks and real-browser regression suites. */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const palette = Object.freeze({
  bg: "#f7f7f8", paper: "#ffffff", ink: "#18181b", muted: "#52525b",
  accent: "#0e6472", "accent-hover": "#0b4b57", soft: "#eaf3f5",
});

function luminance(hex) {
  const rgb = hex.slice(1).match(/../g).map(channel => parseInt(channel, 16) / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return rgb[0] * 0.2126 + rgb[1] * 0.7152 + rgb[2] * 0.0722;
}

function contrast(a, b) {
  const x = luminance(a), y = luminance(b);
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function checkSourceTheme() {
  const root = path.resolve(__dirname, "..");
  for (const file of ["web/styles.css", "workbench/web/styles.css"]) {
    const css = fs.readFileSync(path.join(root, file), "utf8");
    const declaration = css.match(/:root\s*\{([^}]+)\}/)?.[1];
    assert(declaration, `${file}: missing root tokens`);
    const tokens = Object.fromEntries([...declaration.matchAll(/--([\w-]+)\s*:\s*([^;}]+)/g)].map(match => [match[1], match[2].trim()]));
    for (const [key, value] of Object.entries(palette)) assert.equal(tokens[key], value, `${file}: --${key}`);
    assert(!/--green\b|#245d4d|#edf3ee|#f5f6f2/i.test(css), `${file}: old shell colors remain`);
  }
  const checks = [
    ["main text / white", palette.ink, palette.paper, 4.5],
    ["secondary text / page", palette.muted, palette.bg, 4.5],
    ["secondary text / selected", palette.muted, palette.soft, 4.5],
    ["primary label", palette.paper, palette.accent, 4.5],
    ["primary label on hover", palette.paper, palette["accent-hover"], 4.5],
    ["accent text / selected", palette.accent, palette.soft, 4.5],
    ["focus / sidebar", palette.accent, "#f1f1f3", 3],
    ["focus / white", palette.accent, palette.paper, 3],
  ];
  for (const [name, foreground, background, minimum] of checks) {
    const ratio = contrast(foreground, background);
    assert(ratio >= minimum, `${name}: ${ratio.toFixed(2)} < ${minimum}`);
    console.log(`PASS ${name}: ${ratio.toFixed(2)}:1`);
  }
  console.log("PASS both source palettes");
}

const rgb = hex => `rgb(${hex.slice(1).match(/../g).map(channel => parseInt(channel, 16)).join(", ")})`;

async function checkBrowserTheme(page, primarySelector) {
  const actual = await page.evaluate(() => {
    const root = getComputedStyle(document.documentElement), body = getComputedStyle(document.body);
    return {
      background: body.backgroundColor === "rgba(0, 0, 0, 0)" ? root.backgroundColor : body.backgroundColor,
      color: body.color,
      muted: getComputedStyle(document.querySelector(".muted")).color,
    };
  });
  assert.deepEqual(actual, { background: rgb(palette.bg), color: rgb(palette.ink), muted: rgb(palette.muted) });
  const primary = page.locator(primarySelector);
  await page.mouse.move(0, 0);
  assert.deepEqual(await primary.evaluate(element => ({ background: getComputedStyle(element).backgroundColor, color: getComputedStyle(element).color })),
    { background: rgb(palette.accent), color: rgb(palette.paper) });
  await primary.hover();
  assert.equal(await primary.evaluate(element => getComputedStyle(element).backgroundColor), rgb(palette["accent-hover"]));
  await page.keyboard.press("Tab");
  await primary.focus();
  const focus = await primary.evaluate(element => {
    const style = getComputedStyle(element);
    return { visible: element.matches(":focus-visible"), color: style.outlineColor, width: style.outlineWidth, style: style.outlineStyle };
  });
  assert.deepEqual(focus, { visible: true, color: rgb(palette.accent), width: "3px", style: "solid" });
  await primary.evaluate(element => element.blur());
  await page.mouse.move(0, 0);
}

if (require.main === module) checkSourceTheme();
module.exports = { checkBrowserTheme, checkSourceTheme, contrast };
