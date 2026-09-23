"""Ограниченный loopback HTTP-сервис: загрузки, один расчёт, локальная история.

Это не публичный веб-сервер. Токен защищает браузерный API от чужих сайтов,
но не заменяет права доступа и защиту учётной записи операционной системы.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import socket
import stat
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import (
    FORMATS, MAX_CELL_CHARS, MAX_COLUMNS, MAX_EDGES, MAX_FILE_BYTES,
    MAX_NODES, MAX_ROWS, MAX_UPLOAD_BYTES, ImportFailure,
)

MAX_JSON_BYTES = 1024 * 1024
REQUEST_TIMEOUT = 10
WEB_DIR = Path(__file__).resolve().parent / "web"
DEFAULT_STORAGE = Path(__file__).resolve().parents[1] / "workbench_data"
ARTIFACTS = {
    "nodes_roles.csv": "text/csv; charset=utf-8",
    "clusters.csv": "text/csv; charset=utf-8",
    "top_nodes.csv": "text/csv; charset=utf-8",
    "report.json": "application/json; charset=utf-8",
    "result.json": "application/json; charset=utf-8",
    "graph_view.html": "text/html; charset=utf-8",
}
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/styles.css": ("styles.css", "text/css; charset=utf-8"),
}
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CSP = (
    "default-src 'none'; script-src 'self' 'nonce-__NONCE__'; script-src-attr 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; frame-src blob:; img-src data:; "
    "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def inspect_file(path: Path, filename: str, options: dict | None = None) -> dict:
    from .importing import inspect_file as implementation
    return implementation(path, filename, options)


def validate_request(sources: list[dict], config: dict) -> dict:
    from .analysis import validate_request as implementation
    return implementation(sources, config)


def run_analysis(sources: list[dict], config: dict, output_dir: Path) -> dict:
    from .analysis import run_analysis as implementation
    return implementation(sources, config, output_dir)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_file(path: Path, parent: Path) -> bool:
    """Файл не должен выходить из ожидаемого каталога или быть ссылкой."""
    return (
        not parent.is_symlink() and not path.is_symlink()
        and path.is_file() and path.resolve().parent == parent.resolve()
    )


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class RequestError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _make_storage_directory(path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
    # On Windows, Python treats 0700 specially: it replaces inherited ACLs
    # with creator/admin access. A helper process can then lock out the actual
    # desktop user. Use the parent's Windows ACL; retain owner-only POSIX mode.
    mode = 0o777 if sys.platform == "win32" else 0o700
    access_message = (
        f"Нет доступа к каталогу данных '{path}'. Проверьте права вашей учётной "
        "записи Windows/ОС (раздел «Если не запускается» в README.md). "
        "Не удаляйте хранилище: в нём могут быть загрузки и история."
    )
    try:
        path.mkdir(parents=parents, exist_ok=exist_ok, mode=mode)
    except PermissionError as exc:
        raise ValueError(access_message) from exc
    except FileExistsError as exc:
        # Path.mkdir(exist_ok=True) can surface WinError 183 when is_dir()
        # cannot inspect an existing directory because its ACL denies access.
        try:
            existing = path.stat()
        except PermissionError as denied:
            raise ValueError(access_message) from denied
        if not stat.S_ISDIR(existing.st_mode):
            raise ValueError(
                f"Путь данных '{path}' уже существует, но это не каталог. "
                "Файл не изменён; выберите другой --data-dir или сохраните его отдельно."
            ) from exc
        raise


class Storage:
    """Единственный владелец хранилища и состояния задания внутри процесса."""

    def __init__(self, root: Path):
        self.root = root.absolute()
        if self.root.is_symlink():
            raise ValueError("Каталог данных не должен быть символической ссылкой")
        _make_storage_directory(self.root, parents=True, exist_ok=True)
        self.upload_dir = self.root / "uploads"
        self.run_dir = self.root / "runs"
        for directory in (self.upload_dir, self.run_dir):
            if directory.is_symlink():
                raise ValueError("Каталоги приложения не должны быть символическими ссылками")
            _make_storage_directory(directory, exist_ok=True)
        self.lock = threading.RLock()
        self.closed = False
        self.active_id: str | None = None
        self.uploads: dict[str, dict] = {}
        self.runs: dict[str, dict] = {}
        self._lock_file = None
        self._acquire_process_lock()
        try:
            self._restore()
        except BaseException:
            self.close()
            raise

    def _acquire_process_lock(self) -> None:
        lock_path = self.root / ".server.lock"
        if lock_path.is_symlink():
            raise ValueError("Файл блокировки не должен быть символической ссылкой")
        self._lock_file = lock_path.open("a+b")
        try:
            self._lock_file.seek(0, os.SEEK_END)
            if not self._lock_file.tell():
                self._lock_file.write(b"0")
                self._lock_file.flush()
            self._lock_file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise ValueError("Это хранилище уже открыто другим экземпляром приложения") from exc

    def _restore(self) -> None:
        index = self.root / "uploads.json"
        if index.exists():
            if not _safe_file(index, self.root):
                raise ValueError("Небезопасный индекс загрузок")
            records = json.loads(index.read_text(encoding="utf-8"))
            if not isinstance(records, dict):
                raise ValueError("Повреждён индекс загрузок")
            for upload_id, record in records.items():
                if not ID_PATTERN.fullmatch(upload_id) or not isinstance(record, dict):
                    raise ValueError("Повреждён индекс загрузок")
                suffix = record.get("suffix")
                if suffix not in FORMATS or not isinstance(record.get("filename"), str):
                    raise ValueError("Повреждён индекс загрузок")
                candidate = self.upload_dir / f"{upload_id}{suffix}"
                if _safe_file(candidate, self.upload_dir):
                    self.uploads[upload_id] = record
        for directory in self.run_dir.iterdir():
            if not ID_PATTERN.fullmatch(directory.name) or directory.is_symlink() or not directory.is_dir():
                continue
            state_path = directory / "state.json"
            if not _safe_file(state_path, directory):
                continue
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if not isinstance(state, dict) or state.get("id") != directory.name:
                    continue
                if state.get("status") not in {"queued", "running", "completed", "failed", "interrupted"}:
                    continue
                if not isinstance(state.get("created_at"), str):
                    continue
                if state["status"] in {"queued", "running"}:
                    state.update(status="interrupted", finished_at=_now(), artifacts=[], error="Приложение было остановлено до завершения расчёта. Запустите анализ заново.")
                    _atomic_json(state_path, state)
                elif state["status"] == "completed" and not all(_safe_file(directory / "artifacts" / name, directory / "artifacts") for name in ARTIFACTS):
                    state.update(status="failed", artifacts=[], error="Часть сохранённых файлов результата отсутствует или недоступна. Запустите анализ заново.")
                    _atomic_json(state_path, state)
                self.runs[directory.name] = state
            except (OSError, ValueError, TypeError):
                # Отдельный повреждённый запуск не мешает открыть остальные.
                continue

    def close(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            try:
                if self.active_id and self.active_id in self.runs:
                    state = self.runs[self.active_id]
                    state.update(status="interrupted", finished_at=_now(), artifacts=[], error="Приложение было остановлено до завершения расчёта. Запустите анализ заново.")
                    self._save(state)
            finally:
                if self._lock_file is not None:
                    self._lock_file.close()
                    self._lock_file = None

    def _save(self, state: dict) -> None:
        _atomic_json(self.run_dir / state["id"] / "state.json", state)

    def upload(self, filename: str, content: bytes) -> dict:
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 240 or any(ord(char) < 32 or ord(char) == 127 for char in filename):
            raise ImportFailure("Укажите корректное имя файла")
        suffix = Path(filename).suffix.lower()
        if suffix not in FORMATS:
            raise ImportFailure("Поддерживаются CSV, TSV, XLSX, JSON, JSONL и Parquet")
        if not content:
            raise ImportFailure("Файл пуст. Выберите таблицу с данными")
        with self.lock:
            # Учитываются и оставшиеся после сбоя файлы, а не только записи индекса.
            total = sum(path.stat().st_size for path in self.upload_dir.iterdir() if _safe_file(path, self.upload_dir))
            if total + len(content) > MAX_UPLOAD_BYTES:
                raise RequestError(413, "Лимит хранилища загрузок исчерпан. Остановите приложение и удалите ненужные локальные данные по инструкции.")
            upload_id = uuid.uuid4().hex
            path = self.upload_dir / f"{upload_id}{suffix}"
            with path.open("xb") as stream:
                stream.write(content)
            record = {"id": upload_id, "filename": filename, "size": len(content), "suffix": suffix, "created_at": _now()}
            self.uploads[upload_id] = record
            _atomic_json(self.root / "uploads.json", self.uploads)
        return {key: record[key] for key in ("id", "filename", "size")}

    def resolve(self, upload_id: str) -> tuple[Path, dict]:
        if not isinstance(upload_id, str) or not ID_PATTERN.fullmatch(upload_id):
            raise ImportFailure("Некорректный идентификатор загрузки")
        with self.lock:
            record = self.uploads.get(upload_id)
            if record is None:
                raise ImportFailure("Загрузка не найдена. Выберите файл заново")
            path = self.upload_dir / f"{upload_id}{record['suffix']}"
            if not _safe_file(path, self.upload_dir):
                raise ImportFailure("Исходный файл недоступен. Выберите файл заново")
            return path, record.copy()

    def resolve_request(self, payload: dict) -> tuple[list[dict], dict]:
        if set(payload) - {"sources", "config"}:
            raise ImportFailure("Неизвестные параметры запроса")
        sources, config = payload.get("sources"), payload.get("config")
        if not isinstance(sources, list) or not 1 <= len(sources) <= 3:
            raise ImportFailure("Выберите от одной до трёх таблиц")
        if not isinstance(config, dict):
            raise ImportFailure("Нужны настройки анализа")
        resolved = []
        kinds = set()
        for source in sources:
            if not isinstance(source, dict) or set(source) - {"upload_id", "kind", "mapping", "options"}:
                raise ImportFailure("Некорректное описание таблицы")
            kind = source.get("kind")
            if not isinstance(kind, str) or kind not in {"nodes", "edges", "transactions"} or kind in kinds:
                raise ImportFailure("У каждой таблицы должно быть уникальное назначение: nodes, edges или transactions")
            mapping, options = source.get("mapping", {}), source.get("options", {})
            if not isinstance(mapping, dict) or not isinstance(options, dict):
                raise ImportFailure("Настройка полей и формата должна быть объектом")
            if any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
                raise ImportFailure("Сопоставление полей должно содержать имена колонок")
            path, record = self.resolve(source.get("upload_id"))
            resolved.append({"kind": kind, "path": path, "filename": record["filename"], "mapping": mapping, "options": options})
            kinds.add(kind)
        return resolved, config

    @staticmethod
    def public(state: dict) -> dict:
        return {key: state[key] for key in ("id", "status", "created_at", "finished_at", "error", "issues", "report", "artifacts") if key in state}

    def list_runs(self) -> list[dict]:
        with self.lock:
            return [self.public(state) for state in sorted(self.runs.values(), key=lambda item: item["created_at"], reverse=True)]

    def get_run(self, run_id: str) -> dict:
        with self.lock:
            if run_id not in self.runs:
                raise RequestError(404, "Запуск не найден")
            return self.public(self.runs[run_id])

    def start_run(self, sources: list[dict], config: dict, payload: dict) -> dict:
        with self.lock:
            if self.closed:
                raise RequestError(503, "Приложение завершает работу")
            if self.active_id is not None:
                raise RequestError(409, "Другой анализ уже выполняется. Дождитесь его завершения")
            run_id = uuid.uuid4().hex
            directory = self.run_dir / run_id
            _make_storage_directory(directory)
            _make_storage_directory(directory / "artifacts")
            state = {"id": run_id, "status": "queued", "created_at": _now(), "finished_at": None, "artifacts": [], "request": payload}
            self._save(state)
            self.runs[run_id] = state
            self.active_id = run_id
            accepted = self.public(state)
            worker = threading.Thread(target=self._worker, args=(run_id, sources, config), daemon=True, name=f"analysis-{run_id[:8]}")
            worker.start()
            return accepted

    def _worker(self, run_id: str, sources: list[dict], config: dict) -> None:
        state = self.runs[run_id]
        directory = self.run_dir / run_id / "artifacts"
        try:
            with self.lock:
                if self.closed:
                    return
                state["status"] = "running"
                self._save(state)
            report = run_analysis(sources, config, directory)
            if not isinstance(report, dict) or not all(_safe_file(directory / name, directory) for name in ARTIFACTS):
                raise ValueError("Incomplete analysis result")
            # Проверяем сериализацию до изменения готовности состояния.
            json.dumps(report, ensure_ascii=False, allow_nan=False)
            update = {"status": "completed", "report": report, "artifacts": list(ARTIFACTS)}
        except ImportFailure as exc:
            update = {"status": "failed", "error": str(exc), "issues": exc.issues, "artifacts": []}
        except Exception:
            update = {"status": "failed", "error": "Не удалось завершить анализ. Проверьте входные данные и повторите запуск. Частичные результаты не опубликованы.", "artifacts": []}
        finally:
            with self.lock:
                if not self.closed:
                    state.update(update, finished_at=_now())
                    try:
                        self._save(state)
                    except OSError:
                        state.update(status="failed", error="Не удалось сохранить результат на диск. Проверьте свободное место и права доступа.", artifacts=[])
                    self.active_id = None

    def artifact(self, run_id: str, name: str) -> Path:
        if name not in ARTIFACTS:
            raise RequestError(404, "Файл не найден")
        state = self.get_run(run_id)
        if state["status"] != "completed":
            raise RequestError(409, "Файлы доступны только после успешного завершения анализа")
        directory = self.run_dir / run_id / "artifacts"
        path = directory / name
        if not _safe_file(path, directory):
            raise RequestError(404, "Файл результата недоступен")
        return path


class LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], storage: Path):
        self.store = Storage(storage)
        self.session_token = secrets.token_urlsafe(32)
        # Мастер добавляет этот nonce только script сгенерированного нами графа.
        # Blob iframe наследует CSP страницы; nonce сохраняет offline-графы
        # прежних версий без разрешения произвольных inline scripts/handlers.
        self.csp_nonce = secrets.token_urlsafe(24)
        self.csp = CSP.replace("__NONCE__", self.csp_nonce)
        self.request_slots = threading.BoundedSemaphore(16)
        try:
            super().__init__(address, Handler)
        except BaseException:
            self.store.close()
            raise

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def handle_error(self, request, client_address):
        # Не печатаем URL, имена файлов, содержимое таблиц или traceback в журнал.
        pass

    def server_close(self):
        super().server_close()
        self.store.close()


class Handler(BaseHTTPRequestHandler):
    server: LocalHTTPServer
    server_version = "LocalWorkbench"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(REQUEST_TIMEOUT)

    def log_message(self, format, *args):
        pass

    def _send(self, status: int, body: bytes, content_type: str, *, filename: str | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", self.server.csp)
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Connection", "close")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _security(self) -> str:
        port = self.server.server_address[1]
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            allowed_hosts.update({"127.0.0.1", "localhost"})
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in allowed_hosts:
            raise RequestError(403, "Разрешены только запросы к локальному адресу приложения")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise RequestError(403, "Запрос с другого сайта запрещён")
        try:
            parts = urlsplit(self.path)
            path = unquote(parts.path, errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise RequestError(400, "Некорректный адрес запроса") from exc
        if parts.scheme or parts.netloc or parts.query or parts.fragment:
            raise RequestError(400, "Параметры в адресе запроса не поддерживаются")
        if any(char in path for char in ("\\", "\x00", "%")) or any(part in {".", ".."} for part in path.split("/")):
            raise RequestError(404, "Страница не найдена")
        if path.startswith("/api/") and path != "/api/session":
            tokens = self.headers.get_all("X-Session-Token", [])
            if len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode("utf-8"), self.server.session_token.encode("ascii")):
                raise RequestError(403, "Сессия недействительна. Обновите страницу")
        if self.command == "POST":
            origins = self.headers.get_all("Origin", [])
            if len(origins) != 1 or origins[0] != f"http://{hosts[0]}":
                raise RequestError(403, "Запись разрешена только со страницы этого приложения")
        return path

    def _body(self, limit: int) -> bytes:
        if self.headers.get_all("Transfer-Encoding"):
            raise RequestError(400, "Потоковая загрузка не поддерживается")
        lengths = self.headers.get_all("Content-Length", [])
        if not lengths:
            raise RequestError(411, "Нужно указать размер запроса")
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]):
            raise RequestError(400, "Некорректный размер запроса")
        length = int(lengths[0])
        if length > limit:
            raise RequestError(413, "Превышен допустимый размер запроса")
        try:
            content = self.rfile.read(length)
        except (OSError, TimeoutError) as exc:
            raise RequestError(400, "Загрузка не завершена. Выберите файл и повторите попытку") from exc
        if len(content) != length:
            raise RequestError(400, "Получен неполный запрос")
        return content

    def _payload(self) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise RequestError(415, "Нужен Content-Type application/json")
        def reject_constant(value):
            raise ValueError("Non-finite JSON value")
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate JSON key")
                result[key] = value
            return result
        try:
            payload = json.loads(self._body(MAX_JSON_BYTES).decode("utf-8"), parse_constant=reject_constant, object_pairs_hook=unique_object)
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise RequestError(400, "Некорректный JSON-запрос") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "JSON-запрос должен быть объектом")
        return payload

    def _dispatch(self):
        path = self._security()
        if self.command == "GET":
            if path == "/api/session":
                self._json(200, {"token": self.server.session_token, "csp_nonce": self.server.csp_nonce, "limits": {
                    "max_file_bytes": MAX_FILE_BYTES, "max_upload_bytes": MAX_UPLOAD_BYTES,
                    "max_rows": MAX_ROWS, "max_columns": MAX_COLUMNS, "max_cell_chars": MAX_CELL_CHARS,
                    "max_nodes": MAX_NODES, "max_edges": MAX_EDGES,
                    "max_json_bytes": MAX_JSON_BYTES, "concurrent_runs": 1,
                }, "formats": list(FORMATS)})
                return
            if path == "/api/runs":
                self._json(200, {"runs": self.server.store.list_runs()})
                return
            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})", path)
            if match:
                self._json(200, self.server.store.get_run(match[1]))
                return
            match = re.fullmatch(r"/api/runs/([0-9a-f]{32})/artifacts/([a-z_]+\.(?:csv|json|html))", path)
            if match:
                artifact = self.server.store.artifact(match[1], match[2])
                self._send(200, artifact.read_bytes(), ARTIFACTS[match[2]], filename=match[2])
                return
            if path in ASSETS:
                filename, content_type = ASSETS[path]
                asset = WEB_DIR / filename
                if not _safe_file(asset, WEB_DIR):
                    raise RequestError(404, "Ресурс интерфейса не найден")
                self._send(200, asset.read_bytes(), content_type)
                return
        elif self.command == "POST":
            if path == "/api/uploads":
                names = self.headers.get_all("X-Filename", [])
                if len(names) != 1:
                    raise ImportFailure("Укажите имя загружаемого файла")
                try:
                    filename = unquote(names[0], errors="strict")
                except UnicodeError as exc:
                    raise ImportFailure("Некорректное имя файла") from exc
                self._json(201, self.server.store.upload(filename, self._body(MAX_FILE_BYTES)))
                return
            if path == "/api/inspect":
                payload = self._payload()
                if set(payload) - {"upload_id", "options"} or not isinstance(payload.get("options", {}), dict):
                    raise ImportFailure("Некорректные настройки предпросмотра")
                source_path, record = self.server.store.resolve(payload.get("upload_id"))
                self._json(200, inspect_file(source_path, record["filename"], payload.get("options", {})))
                return
            if path in {"/api/preview", "/api/runs"}:
                payload = self._payload()
                sources, config = self.server.store.resolve_request(payload)
                if path == "/api/preview":
                    self._json(200, validate_request(sources, config))
                else:
                    self._json(202, self.server.store.start_run(sources, config, payload))
                return
        raise RequestError(404, "Страница не найдена")

    def _handle(self):
        try:
            self._dispatch()
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc), "issues": []})
        except ImportFailure as exc:
            self._json(422, {"error": str(exc), "issues": exc.issues})
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception:
            self._json(500, {"error": "Не удалось выполнить запрос. Проверьте данные и доступность локального хранилища.", "issues": []})

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def do_OPTIONS(self):
        try:
            self._security()
            self._json(405, {"error": "Этот метод не поддерживается", "issues": []})
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc), "issues": []})


def create_server(host: str = "127.0.0.1", port: int = 8765, storage: Path | None = None) -> LocalHTTPServer:
    """Создать сервер без запуска loop, доступ разрешён только через loopback."""
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("Приложение должно слушать только 127.0.0.1")
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("Порт должен быть целым числом от 0 до 65535")
    return LocalHTTPServer(("127.0.0.1", port), Path(storage) if storage is not None else DEFAULT_STORAGE)
