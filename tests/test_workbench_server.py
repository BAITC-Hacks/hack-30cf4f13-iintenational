"""Изолированные HTTP-проверки локального приложения, без внешней сети."""
from __future__ import annotations

import http.client
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workbench.models import ImportFailure
from workbench.server import ARTIFACTS, create_server


class WorkbenchHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = create_server(port=0, storage=self.root / "store")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.token = self.request("GET", "/api/session")[1]["token"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, authenticated=True):
        defaults = {}
        if authenticated and hasattr(self, "token"):
            defaults["X-Session-Token"] = self.token
        if method == "POST":
            defaults["Origin"] = self.origin
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
            defaults["Content-Type"] = "application/json"
        defaults.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(method, path, body=body, headers=defaults)
        response = connection.getresponse()
        raw = response.read()
        result = json.loads(raw) if "application/json" in response.getheader("Content-Type", "") and "/artifacts/" not in path else raw
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        return status, result, response_headers

    def upload(self):
        status, result, _ = self.request("POST", "/api/uploads", b"src,dst,amount\n001,B,10\n", {"X-Filename": "transfers.csv"})
        self.assertEqual(status, 201)
        return result

    def job_request(self):
        upload = self.upload()
        return {"sources": [{"upload_id": upload["id"], "kind": "transactions", "mapping": {"src": "src", "dst": "dst", "amount": "amount"}, "options": {}}], "config": {"profile": "generic"}}

    def await_job(self, job_id):
        for _ in range(200):
            result = self.request("GET", f"/api/runs/{job_id}")[1]
            if result["status"] not in {"queued", "running"}:
                return result
            time.sleep(.01)
        self.fail("Локальное тестовое задание не завершилось")

    def test_session_security_headers_and_limits(self):
        status, result, headers = self.request("GET", "/api/session", authenticated=False)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(result["token"]), 32)
        self.assertEqual(result["limits"]["max_file_bytes"], 20 * 1024 * 1024)
        self.assertIn(".xlsx", result["formats"])
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        script_policy = headers["Content-Security-Policy"].split("script-src ", 1)[1].split(";", 1)[0]
        self.assertNotIn("unsafe-inline", script_policy)
        self.assertIn(f"'nonce-{result['csp_nonce']}'", script_policy)
        self.assertNotEqual(result["token"], result["csp_nonce"])

    def test_all_other_api_requires_token(self):
        for method, path, body in [("GET", "/api/runs", None), ("GET", "/api/runs/" + "a" * 32, None), ("POST", "/api/uploads", b"a"), ("POST", "/api/inspect", {}), ("POST", "/api/preview", {}), ("POST", "/api/runs", {})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(method, path, body, authenticated=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/runs", headers={"X-Session-Token": "wrong"})[0], 403)

    def test_host_origin_and_cross_site_are_rejected(self):
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "attacker.example"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Host": "127.0.0.1:1"})[0], 403)
        self.assertEqual(self.request("GET", "/api/session", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        for origin in ("https://attacker.example", "null", "http://localhost:1", ""):
            self.assertEqual(self.request("POST", "/api/inspect", {}, {"Origin": origin})[0], 403)

    def test_nonloopback_server_is_forbidden(self):
        for host in ("0.0.0.0", "::", "192.168.1.1", "evil.example"):
            with self.assertRaises(ValueError):
                create_server(host=host, port=0, storage=self.root / "bad")

    def test_same_storage_cannot_be_opened_by_second_server(self):
        with self.assertRaisesRegex(ValueError, "другим экземпляром"):
            create_server(port=0, storage=self.root / "store")

    def test_only_explicit_static_assets_are_served(self):
        assets = self.root / "assets"
        assets.mkdir()
        for name in ("index.html", "app.js", "styles.css"):
            (assets / name).write_text(name, encoding="utf-8")
        (assets / "private.txt").write_text("private", encoding="utf-8")
        with patch("workbench.server.WEB_DIR", assets):
            for path in ("/", "/index.html", "/app.js", "/styles.css", "/assets/app.js", "/assets/styles.css"):
                self.assertEqual(self.request("GET", path)[0], 200)
            self.assertEqual(self.request("GET", "/private.txt")[0], 404)
            self.assertEqual(self.request("GET", "/assets/private.txt")[0], 404)

    def test_upload_has_opaque_disk_name_and_safe_display_basename(self):
        status, result, _ = self.request("POST", "/api/uploads", b"src,dst,amount\nA,B,2", {"X-Filename": "..%2F..%2F%D0%B4%D0%B0%D0%BD%D0%BD%D1%8B%D0%B5.csv"})
        self.assertEqual(status, 201)
        self.assertEqual(result["filename"], "данные.csv")
        self.assertRegex(result["id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("path", result)
        self.assertTrue((self.root / "store" / "uploads" / f"{result['id']}.csv").is_file())

    def test_upload_rejects_unsupported_extensions_and_empty_files(self):
        for filename in ("attack.py", "attack.xlsm", "archive.zip", "file", "", "bad%00.csv"):
            self.assertEqual(self.request("POST", "/api/uploads", b"content", {"X-Filename": filename})[0], 422)
        self.assertEqual(self.request("POST", "/api/uploads", b"", {"X-Filename": "empty.csv"})[0], 422)

    def test_upload_byte_limit_and_total_quota(self):
        with patch("workbench.server.MAX_FILE_BYTES", 3):
            self.assertEqual(self.request("POST", "/api/uploads", b"1234", {"X-Filename": "data.csv"})[0], 413)
        with patch("workbench.server.MAX_UPLOAD_BYTES", 5):
            self.assertEqual(self.request("POST", "/api/uploads", b"123", {"X-Filename": "data.csv"})[0], 201)
            self.assertEqual(self.request("POST", "/api/uploads", b"123", {"X-Filename": "data.csv"})[0], 413)

    def test_invalid_json_and_request_shapes(self):
        for body in (b"not json", b"[]", b'{"config":NaN}', b'{"config":{},"config":{}}'):
            self.assertEqual(self.request("POST", "/api/preview", body, {"Content-Type": "application/json"})[0], 400)
        for body in ({}, {"sources": [], "config": {}}, {"sources": "bad", "config": {}}, {"sources": [{"upload_id": "../x", "kind": "nodes"}], "config": {}}):
            self.assertEqual(self.request("POST", "/api/preview", body)[0], 422)
        with patch("workbench.server.MAX_JSON_BYTES", 2):
            self.assertEqual(self.request("POST", "/api/preview", {"sources": []})[0], 413)

    def test_bad_content_length_and_chunked_are_rejected(self):
        for length_lines, expected in [("Content-Length: -1\r\n", 400), ("Content-Length: 1\r\nContent-Length: 1\r\n", 400), ("Transfer-Encoding: chunked\r\n", 400), ("", 411)]:
            request = (f"POST /api/uploads HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nX-Session-Token: {self.token}\r\nX-Filename: data.csv\r\n{length_lines}\r\n").encode()
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
                client.sendall(request)
                self.assertIn(f" {expected} ".encode(), client.recv(4096).split(b"\r\n", 1)[0])

    def test_short_body_does_not_hang(self):
        request = (f"POST /api/uploads HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nX-Session-Token: {self.token}\r\nX-Filename: data.csv\r\nContent-Length: 10\r\n\r\nx").encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
            client.sendall(request)
            client.shutdown(socket.SHUT_WR)
            self.assertIn(b" 400 ", client.recv(4096).split(b"\r\n", 1)[0])

    def test_short_body_without_eof_times_out(self):
        request = (f"POST /api/uploads HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\nX-Session-Token: {self.token}\r\nX-Filename: data.csv\r\nContent-Length: 10\r\n\r\nx").encode()
        with patch("workbench.server.REQUEST_TIMEOUT", .1):
            with socket.create_connection(("127.0.0.1", self.port), timeout=3) as client:
                client.sendall(request)
                self.assertIn(b" 400 ", client.recv(4096).split(b"\r\n", 1)[0])

    def test_inspect_resolves_path_without_client_path(self):
        upload = self.upload()
        with patch("workbench.server.inspect_file", return_value={"columns": ["src", "dst", "amount"], "preview": [], "rows": 1, "sheets": [], "warnings": []}) as inspect:
            status, result, _ = self.request("POST", "/api/inspect", {"upload_id": upload["id"], "options": {"encoding": "utf-8"}})
            self.assertEqual(status, 200)
            self.assertEqual(result["rows"], 1)
            self.assertEqual(inspect.call_args.args[0], self.root / "store" / "uploads" / f"{upload['id']}.csv")
            self.assertEqual(inspect.call_args.args[1], "transfers.csv")
        self.assertEqual(self.request("POST", "/api/inspect", {"upload_id": "a" * 32})[0], 422)

    def test_preview_passes_only_declared_sources(self):
        body = self.job_request()
        with patch("workbench.server.validate_request", return_value={"counts": {"nodes": 2}, "warnings": [], "capabilities": []}) as validate:
            self.assertEqual(self.request("POST", "/api/preview", body)[0], 200)
            sources, config = validate.call_args.args
            self.assertEqual(sources[0]["kind"], "transactions")
            self.assertIsInstance(sources[0]["path"], Path)
            self.assertEqual(config, body["config"])
        body["sources"][0]["path"] = "../../outside.csv"
        self.assertEqual(self.request("POST", "/api/preview", body)[0], 422)

    def test_bad_source_types_and_duplicate_kinds(self):
        body = self.job_request()
        for kind in ([], {}, None, 3):
            invalid = json.loads(json.dumps(body))
            invalid["sources"][0]["kind"] = kind
            self.assertEqual(self.request("POST", "/api/preview", invalid)[0], 422)
        body["sources"].append(body["sources"][0].copy())
        self.assertEqual(self.request("POST", "/api/preview", body)[0], 422)

    def test_structured_import_errors_and_internal_errors_sanitized(self):
        upload = self.upload()
        issues = [{"table": "transactions", "row": 2, "field": "amount", "message": "Нужна положительная сумма"}]
        with patch("workbench.server.inspect_file", side_effect=ImportFailure("Ошибка таблицы", issues)):
            status, result, _ = self.request("POST", "/api/inspect", {"upload_id": upload["id"]})
            self.assertEqual(status, 422)
            self.assertEqual(result["issues"], issues)
        with patch("workbench.server.inspect_file", side_effect=RuntimeError("C:/private/secret.csv")):
            status, result, _ = self.request("POST", "/api/inspect", {"upload_id": upload["id"]})
            self.assertEqual(status, 500)
            self.assertNotIn("private", json.dumps(result))

    def test_completed_job_artifacts_history_and_restart(self):
        body = self.job_request()
        def runner(sources, config, output_dir):
            for name in ARTIFACTS:
                (output_dir / name).write_text("test artifact", encoding="utf-8")
            return {"counts": {"nodes": 2}, "profile": "generic"}
        with patch("workbench.server.run_analysis", side_effect=runner):
            status, job, _ = self.request("POST", "/api/runs", body)
            self.assertEqual(status, 202)
            final = self.await_job(job["id"])
        self.assertEqual(final["status"], "completed")
        self.assertEqual(set(final["artifacts"]), set(ARTIFACTS))
        for name in ARTIFACTS:
            status, result, _ = self.request("GET", f"/api/runs/{job['id']}/artifacts/{name}")
            self.assertEqual(status, 200)
            self.assertEqual(result, b"test artifact")
        self.assertEqual(self.request("GET", "/api/runs")[1]["runs"][0]["id"], job["id"])
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.server = create_server(port=0, storage=self.root / "store")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        new_token = self.request("GET", "/api/session")[1]["token"]
        self.assertNotEqual(new_token, self.token)
        self.token = new_token
        self.assertEqual(self.request("GET", f"/api/runs/{job['id']}")[1]["status"], "completed")

    def test_failed_job_never_exposes_partial_results(self):
        def runner(sources, config, output_dir):
            (output_dir / "nodes_roles.csv").write_text("partial", encoding="utf-8")
            raise RuntimeError("C:/private/passwords.txt")
        with patch("workbench.server.run_analysis", side_effect=runner):
            job = self.request("POST", "/api/runs", self.job_request())[1]
            final = self.await_job(job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["artifacts"], [])
        self.assertNotIn("passwords", final["error"])
        self.assertEqual(self.request("GET", f"/api/runs/{job['id']}/artifacts/nodes_roles.csv")[0], 409)

    def test_missing_artifact_fails_whole_job(self):
        with patch("workbench.server.run_analysis", return_value={"counts": {"nodes": 2}}):
            job = self.request("POST", "/api/runs", self.job_request())[1]
            final = self.await_job(job["id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["artifacts"], [])

    def test_concurrent_run_rejected(self):
        entered, release = threading.Event(), threading.Event()
        def runner(*args):
            entered.set()
            release.wait(3)
            raise ImportFailure("Остановлено в тесте")
        body = self.job_request()
        try:
            with patch("workbench.server.run_analysis", side_effect=runner):
                first = self.request("POST", "/api/runs", body)[1]
                self.assertTrue(entered.wait(2))
                self.assertEqual(self.request("POST", "/api/runs", body)[0], 409)
                release.set()
                self.await_job(first["id"])
        finally:
            release.set()

    def test_unfinished_state_marked_interrupted_on_restart(self):
        run_id = "b" * 32
        run_dir = self.root / "other" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps({"id": run_id, "status": "running", "created_at": "2026-09-23T00:00:00Z", "artifacts": []}), encoding="utf-8")
        other = create_server(port=0, storage=self.root / "other")
        try:
            saved = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["artifacts"], [])
        finally:
            other.server_close()

    def test_traversal_unlisted_assets_and_query_tokens_rejected(self):
        for path in ("/../README.md", "/%2e%2e/README.md", "/%252e%252e/README.md", "/data/nodes.parquet", "/.git/config", "/api/runs/../../README.md", "/api/runs?token=" + self.token, "/%5c..%5cREADME.md"):
            self.assertIn(self.request("GET", path)[0], {400, 404})
        self.assertEqual(self.request("GET", "/api/runs/" + "f" * 32)[0], 404)

    def test_no_unlisted_artifact(self):
        self.assertEqual(self.request("GET", "/api/runs/" + "f" * 32 + "/artifacts/state.json")[0], 404)

    def test_actual_csv_import_analysis_and_artifacts(self):
        body = self.job_request()
        upload_id = body["sources"][0]["upload_id"]
        status, preview, _ = self.request("POST", "/api/inspect", {"upload_id": upload_id})
        self.assertEqual(status, 200, preview)
        self.assertEqual(preview["columns"], ["src", "dst", "amount"])
        self.assertEqual(preview["rows"], 1)
        status, checked, _ = self.request("POST", "/api/preview", body)
        self.assertEqual(status, 200, checked)
        self.assertEqual(checked["counts"]["nodes"], 2)
        status, accepted, _ = self.request("POST", "/api/runs", body)
        self.assertEqual(status, 202, accepted)
        final = self.await_job(accepted["id"])
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(len(final["artifacts"]), 6)
        status, roles, _ = self.request("GET", f"/api/runs/{accepted['id']}/artifacts/nodes_roles.csv")
        self.assertEqual(status, 200)
        self.assertIn("001", roles.decode("utf-8-sig"))
        self.assertNotIn("terminal", roles.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
