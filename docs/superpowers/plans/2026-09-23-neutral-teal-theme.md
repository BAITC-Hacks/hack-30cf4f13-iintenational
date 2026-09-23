# Neutral teal theme implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Apply the approved neutral/dark-teal theme to both interfaces and make the README answer all four requested questions.

**Architecture:** Keep the current HTML, workflows and analytical contracts. CSS custom properties define the shell palette; SVG and Canvas use the graph's computed palette. Role/cluster colors remain independent of shell selection colors.

**Tech Stack:** Existing HTML/CSS/JavaScript, Python pipeline, Node.js assertions and Playwright.

## Global Constraints

- Background `#f7f7f8`, panels `#ffffff`, sidebar `#f1f1f3`.
- Main text `#18181b`, secondary text `#52525b`.
- Accent `#0e6472`, hover `#0b4b57`, subtle selection `#eaf3f5`.
- No layout, import, API, history, data, role-rule or CSV changes.
- Preserve existing semantic role/cluster colors, error/warning text and colors.
- Do not overwrite saved user HTML artifacts or regenerate source data.
- No dark mode, theme switcher, new runtime dependencies or GitHub publication.

## Task 1: Palette contract and regression check

Files: create `scripts/check_theme.js`; modify `scripts/test_ui.js` and `scripts/test_workbench_ui.js`.

- [x] Write source checks for both CSS roots and a reusable browser check. Use `assert.equal(tokens['--accent'], '#0e6472')` and the corresponding expected values for `--bg`, `--paper`, `--ink`, `--muted`, `--soft`, `--accent-hover`.
- [x] Compute sRGB contrast using `v <= 0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4` and luminance weights `0.2126, 0.7152, 0.0722`. Require text/background >= 4.5 and focus/background >= 3.
- [x] Assert browser-computed body background, primary button text/background, hover, focus outline and neutral text in both existing test suites; run after the initial page is loaded. Export `checkBrowserTheme(page, primarySelector)` from the helper.
- [x] Run `node scripts/check_theme.js` before theme edits; expect failure on the old palette. Re-run after tasks 2/3; expect PASS.

## Task 2: Import workbench shell

Files: `workbench/web/styles.css` only. No JS changes.

- [x] Add the shared root tokens, rename `--green` references to `--accent`; use `var(--ink)` and `var(--bg)` for inherited color/background.
- [x] Replace green-tinted surfaces with `#f7f7f8`, `#f1f1f3`, `#fafafa`, `#e4e4e7`; decorative borders with `#d4d4d8` / `#e4e4e7`; form boundaries with `#71717a`.
- [x] Use `var(--accent)` for focus, links, active step, primary button and status badge; `var(--accent-hover)` for primary hover; `var(--soft)` for highlighted surfaces. Preserve amber warnings and red errors.
- [x] Verify source contract and `npm.cmd run test:workbench`; inspect desktop/mobile screenshots.

## Task 3: Graph shell, SVG and Canvas

Files: `web/styles.css`, `web/index.html`, `web/app.js`.

- [x] Apply the same tokens and neutralize shell surfaces/borders/shadows. Keep selected queue row and node on `var(--soft)`, focused outlines on accent. Brand SVG fill becomes `var(--accent)`.
- [x] Add `const themeStyle = getComputedStyle(document.documentElement); const theme = Object.fromEntries(['ink','muted','accent','soft','paper','line','edge'].map(key => [key, themeStyle.getPropertyValue('--'+key).trim()]));` after DOM initialization. Define `--edge:#71717a` in graph CSS.
- [x] Replace SVG shell text/fills/strokes with `theme` values (template interpolation), and Canvas strokes/text with the same values. Do not replace entries in `roles`, `clusterColor`, or analytical calculations.
- [x] Run `node --check web/app.js`, rebuild with `.\.venv\Scripts\python.exe -X utf8 run_pipeline.py`, then `node scripts/check_graph_view.js` and `npm.cmd run test:ui`.

## Task 4: README and final verification

Files: `README.md`, generated `outputs/graph_view.html`, `docs/theme-verification.md`.

- [x] Explain the analyst's problem and the two entry points (multi-format wizard / strict Parquet pipeline). Add the factual technologies section without changing role thresholds or provenance disclaimers.
- [x] Make test steps rebuild `outputs` before static/UI checks. Document `npx.cmd playwright install chromium` only as an alternative when Chrome is absent.
- [x] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`, `node scripts/check_theme.js`, `node scripts/check_graph_view.js`, `npm.cmd run test:ui`, `npm.cmd run test:workbench`.
- [x] Compare SHA-256 of all three generated CSV files against their pre-change values; require equality. Inspect desktop/mobile renders and README links.
- [x] Record actual test counts, contrast ratios, pipeline time, unchanged CSV and the caveat about historical HTML appearance. Report local changes; do not push without a current request.

## Execution note

The user approved the written specification and explicitly requested application. The subagent-driven-development skill is unavailable here; execute bounded, non-overlapping tasks with the available collaboration tools and main-agent review. README work is independent of UI implementation.
