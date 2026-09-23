# Local Import Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Локальное приложение с мастером импорта и историей для обоих согласованных вариантов входа.

**Architecture:** Адаптеры создают единый Dataset, аналитическое ядро строит независимые артефакты, HTTP-сервис управляет загрузками и очередью. Интерфейс не содержит расчётных правил. Строгий HackAlem и пользовательский профиль отделены.

**Tech Stack:** Python 3.12+ (не 3.14.1), pandas, pyarrow, NetworkX, openpyxl, стандартный HTTP-сервер на loopback; HTML/CSS/JavaScript, Playwright.

**Execution note:** дополнительный subagent-driven-development отсутствует в этой
среде. Реализация разделена штатным делегированием на импорт, аналитику, сервер
и UI; выполнены перекрёстное ревью и общие регрессионные прогоны. Модули входят
в один интеграционный коммит после проверки. Фактический протокол —
`docs/workbench-verification.md`.

## Global Constraints

- Существующий `run.ps1` и точные схемы HackAlem сохраняются.
- Форматы: CSV, TSV, XLSX, JSON, JSONL, Parquet; без макросов, внешних API и загружаемого кода.
- 20 MiB на файл, 100 000 строк, 10 000 узлов, 50 000 рёбер; один расчёт одновременно.
- Неизвестные seed/depth/полнота не заполняются выдуманными значениями.
- Роли — гипотезы; исходные и производные пользовательские файлы исключены из Git.

---

## Общие контракты

`workbench.models.Dataset`: nodes (gid:str, depth:int|None, is_seed:bool|None),
edges (src:str, dst:str, amount_minor:int, n_tx:int|None), transactions
(src, dst, amount_minor, date; либо None), metadata:dict, warnings:list[dict].

Источник: `{kind, path, filename, mapping, options}`. mapping сопоставляет
канонические поля gid/src/dst/amount/n_tx/date/depth/is_seed/currency исходным
колонкам. options содержит delimiter, encoding, sheet, decimal, date_format.

Конфигурация: `{profile: generic|hackalem, currency, decimals, top_n,
coverage: unknown|outward, max_depth: int|null}`.

Ошибка `ImportFailure(message, issues=[])` содержит структурированные issues:
`{table, row, field, message}`. Ошибочные значения не пишутся в системный журнал.

### Task 1: Форматы и контракт данных

**Files:** create `workbench/models.py`, `workbench/importing.py`, `workbench/__init__.py`, `tests/test_importing.py`.

**Interfaces:**
- `inspect_file(path: Path, filename: str, options: dict | None = None) -> dict`
  возвращает columns, preview (до 5 строк), rows, sheets, warnings.
- `prepare_dataset(sources: list[dict], config: dict) -> Dataset`.

- [x] Написать тест одинаковой сети во всех форматах, сохранив ID `001` и `A-2`.
- [x] Зафиксировать отказы: дробный/пустой ID, недостаточная точность суммы,
  формулы XLSX, смешанные валюты, пустой граф, неизвестный конец ребра, лимиты.
- [x] Реализовать чтение ограниченных таблиц и строгую Decimal-нормализацию.
- [x] Проверить: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_importing.py -v`.
- [x] Закоммитить модуль и тесты после интеграционного ревью.

Контрольный пример для всех адаптеров:

```python
assert dataset.nodes.gid.tolist() == ["001", "A-2", "B"]
assert dataset.edges.amount_minor.sum() == 125025
assert dataset.nodes.depth.isna().all()
assert dataset.nodes.is_seed.isna().all()
```

### Task 2: Два профиля анализа и артефакты

**Files:** create `workbench/analysis.py`, `tests/test_workbench_analysis.py`; reuse `aml_graph/metrics.py`, `aml_graph/clustering.py`, `aml_graph/roles.py`.

**Interfaces:**
- `validate_request(sources: list[dict], config: dict) -> dict`: counts, warnings, capabilities.
- `run_analysis(sources: list[dict], config: dict, output_dir: Path) -> dict`: report.
- `write_graph_view` получает дополнительные metadata, display_ids и download_frames,
  сохраняя прежние аргументы и старые результаты.

- [x] Написать тест generic из одного файла и из трёх согласованных таблиц.
- [x] Зафиксировать отсутствие terminal/transit при неизвестной полноте.
- [x] Повторно использовать сборку метрик и компонентную кластеризацию через
  внутреннее стабильное отображение строковых ID; не менять исходные ID экспорта.
- [x] Сохранить report.json, result.json, три CSV и graph_view.html; report содержит
  версии правил, ограничения, параметры и хеши файлов.
- [x] Проверить: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_workbench_analysis.py -v`.

```python
assert report["counts"]["nodes"] == 3
assert report["profile"] == "generic"
assert (output_dir / "graph_view.html").is_file()
assert (output_dir / "nodes_roles.csv").is_file()
```

### Task 3: Локальный HTTP и история

**Files:** create `workbench/server.py`, `run_app.py`, `run_app.ps1`, `tests/test_workbench_server.py`.

**Interfaces:** `create_server(host: str = "127.0.0.1", port: int = 8765,
storage: Path | None = None) -> ThreadingHTTPServer`.

API: GET /api/session, POST /api/uploads (raw bytes, X-Filename),
POST /api/inspect ({upload_id, options}), POST /api/preview и /api/runs
({sources:[{upload_id,kind,mapping,options}], config}), GET /api/runs,
GET /api/runs/{id}, GET /api/runs/{id}/artifacts/{name}. Все API после session
требуют X-Session-Token; записи требуют same-origin. Имена артефактов ограничены.

- [x] Написать HTTP-тесты Host/Origin/token/traversal/размеров и сохранения истории.
- [x] Реализовать случайные upload/run ID, атомарные состояния и worker с одной задачей.
- [x] Незавершённые запуски после рестарта отмечать interrupted; ошибки не публикуют артефакты.
- [x] Проверить: `.venv\Scripts\python.exe -m unittest discover -s tests -p test_workbench_server.py -v`.

```python
assert response.status == 403  # POST без сессионного токена
assert create_response.status == 202  # принятый запуск
assert run_status in {"queued", "running", "completed", "failed", "interrupted"}
```

### Task 4: Мастер, история и общий просмотр

**Files:** create `workbench/web/index.html`, `workbench/web/app.js`, `workbench/web/styles.css`, `scripts/test_workbench_ui.js`; modify `aml_graph/visualization.py`, `web/app.js`, `web/index.html`.

- [x] Реализовать четыре шага с настоящими label, прогрессом и live-ошибками.
- [x] Предлагать mapping по известным именам колонок; изменения отменяют preview.
- [x] Через authenticated fetch открывать результат в sandboxed iframe Blob;
  скачивание CSV также через fetch, без токенов в URL.
- [x] Отображать строковые ID, валюту и неизвестные seed/depth корректно;
  старый offline payload сохраняет поведение по умолчанию.
- [x] Playwright проходит импорт CSV, исправление mapping, запуск, граф, CSV,
  историю после reload, проверку узкой ширины и ошибки сервера.

```javascript
assert.equal(await page.locator('#run-status').textContent(), 'Готово');
assert.equal(await page.locator('#run-history [data-history]').count(), 1);
assert(await page.locator('#result-frame').isVisible());
```

### Task 5: Интеграция и передача

**Files:** modify `README.md`, `docs/testing.md`, `.gitignore`, `requirements.txt`, `package.json`; create `docs/workbench.md`, `docs/workbench-verification.md`.

- [x] Зафиксировать зависимости и добавить workbench_data/ в gitignore.
- [x] Запустить весь unittest, старые 17 UI-сценариев и новый мастер.
- [x] Повторить HackAlem и сравнить три CSV побайтово с текущими результатами.
- [x] Проверить отображение ошибок, локальную приватность и весь контракт спецификации.
- [x] Записать фактические результаты, ограничения и команду `powershell -ExecutionPolicy Bypass -File .\run_app.ps1`.
- [x] Закоммитить проверенный результат; не публиковать реальные загрузки и не выполнять push без отдельной просьбы.
