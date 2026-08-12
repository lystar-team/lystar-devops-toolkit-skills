from __future__ import annotations

import csv
import io
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("AGENT_OPS_BIN_DIR", REPO_ROOT / "bin"))
os.environ.setdefault("XDG_DATA_HOME", str(REPO_ROOT / "runtime"))
os.environ.setdefault("LYSTAR_SKILL_AUTO_UPDATE", "0")
sys.path.insert(0, str(SCRIPTS))

import config_store
import db_core
import db_ops
import paramiko
import result_store
import ssh_ops


class ConfigStoreTest(unittest.TestCase):
    def test_round_trip_nested_toml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.toml"
            payload = {
                "default_source": "db_1",
                "profiles": {
                    "db_1": {
                        "engine": "postgresql",
                        "host": "127.0.0.1",
                        "port": 5432,
                        "password": "明文密码",
                    }
                },
                "bindings": {"spring:application.yml:primary": "db_1"},
            }
            config_store.write_toml(path, payload)
            self.assertEqual(config_store.load_toml(path), payload)


class ResultStoreTest(unittest.TestCase):
    def test_session_snapshot_overwrite_summary_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            old_env = os.environ.copy()
            try:
                os.environ["XDG_STATE_HOME"] = str(Path(temp) / "state")
                os.environ["PI_SESSION_ID"] = "session-one"
                first = {"columns": ["id"], "rows": [[1], [2]], "has_more": False}
                path = result_store.save_snapshot("db", "query", root, {"sql": "SELECT 1"}, first, True, "main")
                assert path is not None
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(list(root.iterdir()), [])
                loaded = result_store.load_snapshot("db", root)
                self.assertEqual(loaded["result"], first)
                self.assertEqual(result_store.snapshot_summary(loaded)["rows"], 2)

                second = {"statement_count": 1, "affected_rows": 3}
                result_store.save_snapshot("db", "exec", root, {"sql": "UPDATE demo"}, second, True, "main")
                self.assertEqual(result_store.load_snapshot("db", root)["action"], "exec")

                os.environ["PI_SESSION_ID"] = "session-two"
                with self.assertRaises(FileNotFoundError):
                    result_store.load_snapshot("db", root)
                os.environ["PI_SESSION_ID"] = "session-one"
                self.assertTrue(result_store.clear_snapshot("db", root))
                with self.assertRaises(FileNotFoundError):
                    result_store.load_snapshot("db", root)

                os.environ.pop("PI_SESSION_ID")
                os.environ["CODEX_THREAD_ID"] = "codex-thread"
                codex_path = result_store.save_snapshot("ssh", "exec", root, {}, {"exit_code": 0}, True)
                assert codex_path is not None
                self.assertTrue(codex_path.parent.name.startswith("codex-"))
            finally:
                os.environ.clear()
                os.environ.update(old_env)

    def test_cleanup_removes_expired_and_oldest_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            old_env = os.environ.copy()
            try:
                os.environ["XDG_STATE_HOME"] = str(Path(temp) / "state")
                os.environ["PI_SESSION_ID"] = "expired"
                result_store.save_snapshot("ssh", "exec", root, {}, {"stdout": "old"}, True)
                expired_dir = result_store.session_dir(root)
                assert expired_dir is not None
                now = time.time()
                os.utime(expired_dir, (now - 100, now - 100))
                result_store.cleanup(now=now, ttl_seconds=50, max_bytes=10_000)
                self.assertFalse(expired_dir.exists())

                os.environ["PI_SESSION_ID"] = "older"
                result_store.save_snapshot("ssh", "exec", root, {}, {"stdout": "a" * 200}, True)
                older_dir = result_store.session_dir(root)
                assert older_dir is not None
                os.utime(older_dir, (now - 10, now - 10))
                os.environ["PI_SESSION_ID"] = "newer"
                result_store.save_snapshot("ssh", "exec", root, {}, {"stdout": "b" * 200}, True)
                newer_dir = result_store.session_dir(root)
                assert newer_dir is not None
                os.utime(newer_dir, (now, now))
                newer_size = sum(path.stat().st_size for path in newer_dir.glob("*-last.json"))
                result_store.cleanup(now=now, ttl_seconds=1_000, max_bytes=newer_size)
                self.assertFalse(older_dir.exists())
                self.assertTrue(newer_dir.exists())
            finally:
                os.environ.clear()
                os.environ.update(old_env)

    def test_no_session_id_skips_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            old_env = os.environ.copy()
            try:
                for name, _agent in result_store.SESSION_ENV:
                    os.environ.pop(name, None)
                os.environ["XDG_STATE_HOME"] = str(Path(temp) / "state")
                self.assertIsNone(
                    result_store.save_snapshot("db", "query", Path(temp), {}, {"rows": []}, True)
                )
            finally:
                os.environ.clear()
                os.environ.update(old_env)


class DatabaseOutputTest(unittest.TestCase):
    def test_query_csv_handles_metadata_types_and_escaping(self) -> None:
        payload = {
            "columns": ["id", "name", "note", "active", "details"],
            "rows": [
                [1, None, "", True, {"role": "admin"}],
                [2, "A,B", "say \"hi\"\nnext", False, [1, 2]],
            ],
            "has_more": True,
            "cells_truncated": True,
        }
        stream = io.StringIO(db_ops.query_csv(payload))
        self.assertEqual(
            stream.readline(),
            "# rows=2 has_more=true cells_truncated=true\n",
        )
        rows = list(csv.reader(stream))
        self.assertEqual(rows[0], payload["columns"])
        self.assertEqual(rows[1], ["1", r"\N", "", "true", '{"role":"admin"}'])
        self.assertEqual(rows[2], ["2", "A,B", 'say "hi"\nnext', "false", "[1,2]"])

    def test_empty_query_csv_keeps_columns(self) -> None:
        self.assertEqual(
            db_ops.query_csv({"columns": ["id", "name"], "rows": [], "has_more": False}),
            "# rows=0 has_more=false\nid,name\n",
        )

    def test_json_flags_are_explicit(self) -> None:
        parser = db_ops.build_parser()
        self.assertTrue(parser.parse_args(["query", "SELECT 1", "--json"]).json)
        self.assertTrue(parser.parse_args(["last", "--json"]).json)


class DatabaseRegistryTest(unittest.TestCase):
    def test_same_identity_reuses_profile(self) -> None:
        item = db_core.DataSource(
            engine="mariadb",
            host="127.0.0.1",
            port=3306,
            database="demo",
            user="root",
            password="one",
            source="manual:first",
            framework="manual",
        )
        config = {"profiles": {}, "aliases": {}}
        profile_id = db_ops.make_profile_id(db_ops.datasource_identity(item))
        config["profiles"][profile_id] = db_ops.profile_from_datasource(item)
        self.assertEqual(db_ops.find_profile(config, item), profile_id)

        changed_password = db_core.DataSource(**{**item.__dict__, "password": "two"})
        config["profiles"][profile_id] = db_ops.profile_from_datasource(
            changed_password, config["profiles"][profile_id]
        )
        self.assertEqual(len(config["profiles"]), 1)
        self.assertEqual(config["profiles"][profile_id]["password"], "two")


class DatabaseLastIntegrationTest(unittest.TestCase):
    def test_successful_query_snapshot_renders_csv_or_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            env = {
                **os.environ,
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "PI_SESSION_ID": "db-success",
            }
            old_env = os.environ.copy()
            try:
                os.environ.update(env)
                result_store.save_snapshot(
                    "db",
                    "query",
                    root,
                    {"sql": "SELECT id, name FROM user"},
                    {"columns": ["id", "name"], "rows": [[1, "Yean"], [2, None]], "has_more": False},
                    True,
                    "main",
                )
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            dbx = str(BIN_DIR / "dbx")
            csv_result = subprocess.run(
                [dbx, "last", "--root", str(root)], env=env, cwd=root,
                text=True, capture_output=True, timeout=10,
            )
            self.assertEqual(csv_result.returncode, 0, csv_result.stderr + csv_result.stdout)
            self.assertEqual(
                csv_result.stdout,
                "# rows=2 has_more=false\nid,name\n1,Yean\n2,\\N\n",
            )

            json_result = subprocess.run(
                [dbx, "last", "--json", "--root", str(root)], env=env, cwd=root,
                text=True, capture_output=True, timeout=10,
            )
            payload = json.loads(json_result.stdout)
            self.assertEqual(payload["result"]["rows"], [[1, "Yean"], [2, None]])

    def test_failed_query_is_available_without_requery(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            env = {
                **os.environ,
                "XDG_CONFIG_HOME": str(Path(temp) / "config"),
                "XDG_STATE_HOME": str(Path(temp) / "state"),
                "PI_SESSION_ID": "db-integration",
            }
            dbx = str(BIN_DIR / "dbx")

            def run(*args: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [dbx, *args], env=env, cwd=root, text=True,
                    capture_output=True, timeout=10,
                )

            queried = run("query", "SELECT 1", "--root", str(root))
            self.assertEqual(queried.returncode, 1)
            last = run("last", "--root", str(root))
            self.assertEqual(last.returncode, 0, last.stderr + last.stdout)
            payload = json.loads(last.stdout)
            self.assertEqual(payload["action"], "query")
            self.assertFalse(payload["success"])
            self.assertIn("no datasource found", payload["result"]["error"])
            summary = run("last", "--summary", "--root", str(root))
            self.assertIn("no datasource found", json.loads(summary.stdout)["error"])


class SshOutputTest(unittest.TestCase):
    def test_bounded_output_keeps_head_and_tail(self) -> None:
        output = ssh_ops.BoundedOutput(10)
        output.add(b"0123456789ABCDEF")
        self.assertEqual(output.text(), "01234\n... omitted 6 bytes ...\nBCDEF")

        complete = ssh_ops.BoundedOutput(10)
        complete.add(b"012")
        complete.add(b"345")
        self.assertEqual(complete.text(), "012345")

    def test_default_ssh_timeout_is_120_seconds(self) -> None:
        parser = ssh_ops.build_parser()
        self.assertEqual(parser.parse_args(["open", "prod", "root@example.com"]).timeout, 120)
        self.assertEqual(parser.parse_args(["exec", "prod", "true"]).timeout, 120)
        self.assertEqual(ssh_ops.DEFAULT_TIMEOUT, 120)

    def test_exec_uses_native_streams(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            ssh_ops.render_result(
                "exec",
                {"exit_code": 0, "stdout": "raw output\n", "stderr": "warning\n"},
                False,
            )
        self.assertEqual(stdout.getvalue(), "raw output\n")
        self.assertEqual(stderr.getvalue(), "warning\n")

    def test_ls_and_status_use_csv(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            ssh_ops.render_result(
                "ls",
                {"entries": [{"type": "file", "mode": "0o644", "bytes": 12, "name": "a,b.log"}]},
                False,
            )
        stream = io.StringIO(stdout.getvalue())
        self.assertEqual(stream.readline(), "# entries=1\n")
        self.assertEqual(list(csv.reader(stream))[1], ["file", "0o644", "12", "a,b.log"])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            ssh_ops.render_status(
                [{"aliases": ["prod"], "user": "root", "host": "127.0.0.1", "port": 22, "connected": True}],
                False,
            )
        self.assertTrue(stdout.getvalue().startswith("# connections=1\naliases,user,host,port,connected,note\n"))

    def test_job_output_keeps_raw_sections(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            ssh_ops.render_result(
                "wait",
                {
                    "job_id": "job-1",
                    "status": {"state": "finished", "exit_code": 3},
                    "stdout": "normal output\n",
                    "stderr": "failure details\n",
                },
                False,
            )
        self.assertEqual(
            stdout.getvalue(),
            "# job=job-1 state=finished exit=3\nnormal output\n# stderr\nfailure details\n",
        )


class MockSshServer(paramiko.ServerInterface):
    def check_auth_password(self, username: str, password: str) -> int:
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("tester", "plain-password") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username: str) -> str:
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel: paramiko.Channel, command: bytes) -> bool:
        text = command.decode()

        def respond() -> None:
            time.sleep(0.05)
            if text == "slow":
                time.sleep(2.5)
            stdout = f"ran:{text}\n"
            stderr = ""
            exit_code = 0
            if text == "stderr":
                stdout = "normal\n"
                stderr = "warning\n"
            elif text == "fail":
                stdout = ""
                stderr = "failed\n"
                exit_code = 7
            elif text == "empty":
                stdout = ""
            elif text == "large":
                stdout = "0123456789ABCDEFGHIJ"
            if stdout:
                channel.send(stdout.encode())
            if stderr:
                channel.send_stderr(stderr.encode())
            channel.send_exit_status(exit_code)
            channel.close()

        threading.Thread(target=respond, daemon=True).start()
        return True


class MockSshService:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop_event = threading.Event()
        self.transports: list[paramiko.Transport] = []
        self.channels: list[paramiko.Channel] = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        host_key = paramiko.RSAKey.generate(1024)
        while not self.stop_event.is_set():
            try:
                client, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(client)
            self.transports.append(transport)
            transport.add_server_key(host_key)
            transport.start_server(server=MockSshServer())
            while transport.is_active() and not self.stop_event.is_set():
                channel = transport.accept(0.2)
                if channel is not None:
                    self.channels.append(channel)

    def close(self) -> None:
        self.stop_event.set()
        for channel in self.channels:
            channel.close()
        for transport in self.transports:
            transport.close()
        self.listener.close()
        self.thread.join(timeout=2)


class SshIntegrationTest(unittest.TestCase):
    def test_saved_password_concurrency_and_close(self) -> None:
        service = MockSshService()
        service.start()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {
                **os.environ,
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
                "PI_SESSION_ID": "ssh-integration",
            }
            sshx = str(BIN_DIR / "sshx")

            def run(*args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sshx, *args], env=env, cwd=root, text=True,
                    capture_output=True, timeout=timeout,
                )

            try:
                opened = run("open", "local", f"tester@127.0.0.1:{service.port}", "plain-password")
                self.assertEqual(opened.returncode, 0, opened.stderr + opened.stdout)

                executed = run("local", "echo test")
                self.assertEqual(executed.returncode, 0, executed.stderr + executed.stdout)
                self.assertEqual(executed.stdout, "ran:echo test\n")
                self.assertEqual(executed.stderr, "")

                status_plain = run("status", "local")
                self.assertEqual(status_plain.returncode, 0, status_plain.stderr + status_plain.stdout)
                self.assertTrue(status_plain.stdout.startswith("# connections=1\n"))
                status_before_last = run("status", "--json", "local")
                self.assertEqual(status_before_last.returncode, 0, status_before_last.stderr + status_before_last.stdout)

                last_plain = run("last")
                self.assertEqual(last_plain.stdout, "ran:echo test\n")
                last = run("last", "--json")
                self.assertEqual(last.returncode, 0, last.stderr + last.stdout)
                self.assertEqual(json.loads(last.stdout)["request"]["command"], "echo test")
                summary = run("last", "--summary")
                self.assertEqual(json.loads(summary.stdout)["stdout_chars"], len("ran:echo test\n"))

                structured = run("exec", "--json", "local", "echo test")
                self.assertEqual(json.loads(structured.stdout)["stdout"], "ran:echo test\n")
                streams = run("local", "stderr")
                self.assertEqual(streams.stdout, "normal\n")
                self.assertEqual(streams.stderr, "warning\n")
                failed = run("local", "fail")
                self.assertEqual(failed.returncode, 7)
                self.assertEqual(failed.stderr, "failed\n")
                empty = run("local", "empty")
                self.assertEqual(empty.stdout, "")
                self.assertEqual(empty.stderr, "")
                large = run("exec", "--max-bytes", "10", "local", "large")
                self.assertEqual(
                    large.stdout,
                    "01234\n... omitted 10 bytes ...\nFGHIJ",
                )

                slow = subprocess.Popen(
                    [sshx, "local", "slow"], env=env, cwd=root, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                time.sleep(0.2)
                status = run("status", "--json", "local")
                self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
                self.assertTrue(json.loads(status.stdout)["connections"][0]["connected"])
                slow_stdout, slow_stderr = slow.communicate(timeout=5)
                self.assertEqual(slow.returncode, 0, slow_stderr + slow_stdout)

                closed = run("close", "local")
                self.assertEqual(closed.returncode, 0, closed.stderr + closed.stdout)
                status_after = run("status", "--json", "local")
                self.assertFalse(json.loads(status_after.stdout)["connections"][0]["connected"])
                cleared = run("last", "--clear")
                self.assertTrue(json.loads(cleared.stdout)["cleared"])
            finally:
                run("close", "local")
                service.close()


class SshReapTest(unittest.TestCase):
    def test_live_pid_is_never_reaped_for_slow_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "run"
            base_dir = Path(temp) / "state"
            run_dir.mkdir()
            old_run, old_base = ssh_ops.RUN_DIR, ssh_ops.BASE_DIR
            ssh_ops.RUN_DIR, ssh_ops.BASE_DIR = run_dir, base_dir
            process = subprocess.Popen(["sleep", "10"])
            try:
                profile_id = "ssh_test"
                ssh_ops.pid_path(profile_id).write_text(str(process.pid))
                ssh_ops.socket_path(profile_id).touch()
                self.assertEqual(ssh_ops.reap_stale(), [])
                self.assertTrue(ssh_ops.socket_path(profile_id).exists())
            finally:
                process.terminate()
                process.wait(timeout=2)
                time.sleep(0.05)
                ssh_ops.reap_stale()
                ssh_ops.RUN_DIR, ssh_ops.BASE_DIR = old_run, old_base


if __name__ == "__main__":
    unittest.main()
