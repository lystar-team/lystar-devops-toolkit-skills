from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_store
import magicapi_ops


class MagicApiHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def log_message(self, *_args: object) -> None:
        pass

    def _body(self) -> object:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw:
            return None
        if self.headers.get_content_type() == "application/x-www-form-urlencoded":
            return {key: values[-1] for key, values in parse_qs(raw.decode("utf-8")).items()}
        return json.loads(raw.decode("utf-8"))

    def _send(self, status: int, payload: object, headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        payload = self._body()
        self.__class__.requests.append(
            {"method": "POST", "path": self.path, "body": payload, "headers": dict(self.headers)}
        )
        if self.path == "/webide/login":
            self._send(
                200,
                {"code": 200, "message": "success", "data": {}},
                {"magic-token": "token-value", "Set-Cookie": "sid=abc; Path=/"},
            )
            return
        if self.path == "/webide/resource":
            self._send(200, {"code": 200, "message": "success", "data": [{"id": "r1", "path": "/yean/demo"}]})
            return
        if self.path.startswith("/webide/resource/file/api/save"):
            self._send(200, {"code": 200, "message": "success", "data": "r1"})
            return
        self._send(404, {"code": 404, "message": "not found"})

    def do_GET(self) -> None:  # noqa: N802
        self.__class__.requests.append(
            {"method": "GET", "path": self.path, "body": None, "headers": dict(self.headers)}
        )
        if self.path == "/runtime/yean/demo":
            if self.headers.get("magic-token") != "token-value":
                self._send(401, {"code": 401, "message": "unauthorized"})
                return
            self._send(200, {"code": 200, "message": "success", "data": {"ok": 1}})
            return
        if self.path == "/runtime/yean/error":
            self._send(200, {"code": 400, "message": "header required", "data": None})
            return
        if self.path == "/webide/resource/file/r1":
            self._send(200, {"code": 200, "message": "success", "data": {"id": "r1", "path": "/yean/demo", "script": "return 0"}})
            return
        self._send(404, {"code": 404, "message": "not found"})


class MagicApiOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MagicApiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        MagicApiHandler.requests = []
        self.env = patch.dict(
            os.environ,
            {
                "LYSTAR_HOME": str(self.home / ".lystar"),
                "LYSTAR_SKILL_AUTO_UPDATE": "0",
                "PI_SESSION_ID": "magicapi-test",
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def run_cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = magicapi_ops.main(list(args))
        return status, output.getvalue()

    def add_profile(self) -> None:
        status, output = self.run_cli(
            "profile",
            "add",
            "prod",
            "--base-url",
            self.base_url(),
            "--webide-base-path",
            "/webide",
            "--runtime-base-path",
            "/runtime",
            "--username",
            "yean",
            "--password",
            "secret-password",
        )
        self.assertEqual(status, 0, output)

    def test_profile_output_masks_password_and_header(self) -> None:
        self.add_profile()
        header_file = Path(self.temp.name) / "headers.json"
        header_file.write_text(json.dumps({"X-Test-Header": "query-secret"}), encoding="utf-8")
        status, output = self.run_cli("profile", "header-set", "prod", "query", "--file", str(header_file))
        self.assertEqual(status, 0, output)
        status, output = self.run_cli("profile", "show", "prod")
        self.assertEqual(status, 0, output)
        self.assertNotIn("secret-password", output)
        self.assertNotIn("query-secret", output)
        self.assertIn("X-Test-Header", output)

    def test_login_and_run_use_saved_auth_and_header_set(self) -> None:
        self.add_profile()
        header_file = Path(self.temp.name) / "headers.json"
        header_file.write_text(json.dumps({"X-Test-Header": "query-secret"}), encoding="utf-8")
        self.assertEqual(self.run_cli("profile", "header-set", "prod", "query", "--file", str(header_file))[0], 0)
        status, output = self.run_cli("login", "prod")
        self.assertEqual(status, 0, output)
        self.assertNotIn("token-value", output)
        status, output = self.run_cli("run", "prod", "--path", "/yean/demo", "--header-set", "query")
        self.assertEqual(status, 0, output)
        self.assertIn('"ok": 1', output)
        self.assertNotIn("query-secret", output)
        requests = [item for item in MagicApiHandler.requests if item["path"] == "/runtime/yean/demo"]
        self.assertEqual(len(requests), 1)
        request_headers = {str(key).lower(): value for key, value in requests[0]["headers"].items()}
        self.assertEqual(request_headers.get("x-test-header"), "query-secret")
        self.assertEqual(request_headers.get("magic-token"), "token-value")

    def test_resource_get_by_path_and_save_requires_confirmation(self) -> None:
        self.add_profile()
        status, output = self.run_cli("resource", "get", "prod", "--path", "/yean/demo")
        self.assertEqual(status, 0, output)
        self.assertIn('"id": "r1"', output)
        status, output = self.run_cli("resource", "save", "prod", "--id", "r1")
        self.assertNotEqual(status, 0)
        self.assertIn("--confirm", output)

    def test_magic_api_business_error_is_failure(self) -> None:
        self.add_profile()
        status, output = self.run_cli("run", "prod", "--path", "/yean/error")
        self.assertNotEqual(status, 0, output)
        self.assertIn("header required", output)

    def test_config_file_has_private_mode(self) -> None:
        self.add_profile()
        config_file = self.home / ".lystar" / "config" / "magicapi.toml"
        self.assertEqual(config_file.stat().st_mode & 0o777, 0o600)
        document = config_store.load_toml(config_file)
        self.assertEqual(document["profiles"]["prod"]["password"], "secret-password")


if __name__ == "__main__":
    unittest.main()
