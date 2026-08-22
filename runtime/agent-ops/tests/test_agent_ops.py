from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import os
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

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

logging.getLogger("paramiko.transport").setLevel(logging.CRITICAL)


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


class FakeDatabaseCursor:
    def __init__(self, connection: "FakeDatabaseConnection") -> None:
        self.connection = connection
        self.description = None
        self.rowcount = 1
        self.executed: list[str] = []

    def __enter__(self) -> "FakeDatabaseCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        self.executed.append(sql)
        self.connection.executed.append(sql)
        if "FAIL" in sql:
            raise RuntimeError("synthetic SQL failure")
        if "VERSION()" in sql.upper():
            self.description = [("VERSION()",)]

    def fetchone(self):
        return {"VERSION()": self.connection.version}

    def nextset(self) -> bool:
        return False


class FakeDatabaseConnection:
    def __init__(self, version: str = "10.11.8-MariaDB") -> None:
        self.version = version
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.executed: list[str] = []

    def cursor(self, **_kwargs: object) -> FakeDatabaseCursor:
        return FakeDatabaseCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class DatabaseTransactionAndVersionTest(unittest.TestCase):
    def test_version_normalization_keeps_database_version_only(self) -> None:
        self.assertEqual(db_core.normalize_engine("postgres"), "postgresql")
        self.assertEqual(db_core.version_parts("PostgreSQL 16.3 (Ubuntu 16.3-1)"), (16, 3))
        self.assertEqual(db_core.version_parts("10.11.8-MariaDB-1:10.11.8"), (10, 11, 8))

    def test_version_probe_uses_engine_specific_queries(self) -> None:
        for engine, version, expected_sql in (
            ("mysql", "8.0.36", "SELECT VERSION()"),
            ("mariadb", "10.11.8-MariaDB", "SELECT VERSION()"),
            ("postgresql", "PostgreSQL 16.3", "SELECT version()"),
        ):
            connection = FakeDatabaseConnection(version)
            result = db_core.probe_server_version(connection, engine)
            self.assertEqual(result["version_status"], "detected")
            self.assertEqual(result["version"], version)
            self.assertEqual(result["version_parts"][:2], list(db_core.version_parts(version)[:2]))

            self.assertEqual(connection.executed[-1], expected_sql)

    def test_single_command_transaction_commit_and_rollback(self) -> None:
        datasource = db_core.DataSource(
            engine="mariadb",
            host="127.0.0.1",
            port=3306,
            database="demo",
            user="root",
            password="secret",
            source="test",
            framework="manual",
        )
        committed = FakeDatabaseConnection()
        with patch.object(db_core, "connect", return_value=("pymysql", committed)):
            result = db_core.run_non_query(datasource, "UPDATE demo SET value = 1")
        self.assertEqual(result["transaction"]["state"], "committed")
        self.assertEqual(committed.commits, 1)
        self.assertEqual(committed.rollbacks, 0)

        rolled_back = FakeDatabaseConnection()
        with patch.object(db_core, "connect", return_value=("pymysql", rolled_back)):
            result = db_core.run_non_query(
                datasource,
                "UPDATE demo SET value = 2",
                transaction="rollback",
            )
        self.assertEqual(result["transaction"]["state"], "rolled_back")
        self.assertEqual(rolled_back.commits, 0)
        self.assertEqual(rolled_back.rollbacks, 1)

    def test_failed_transaction_reports_rollback_state(self) -> None:
        datasource = db_core.DataSource(
            engine="mysql",
            host="127.0.0.1",
            port=3306,
            database="demo",
            user="root",
            password="secret",
            source="test",
            framework="manual",
        )
        connection = FakeDatabaseConnection()
        with patch.object(db_core, "connect", return_value=("pymysql", connection)):
            with self.assertRaises(db_core.TransactionError) as context:
                db_core.run_non_query(datasource, "FAIL UPDATE demo")
        self.assertEqual(context.exception.details["transaction"]["state"], "failed")
        self.assertTrue(context.exception.details["transaction"]["rolled_back"])
        self.assertEqual(connection.rollbacks, 1)

    def test_connection_failure_is_distinct_from_version_probe_failure(self) -> None:
        datasource = db_core.DataSource(
            engine="postgresql",
            host="127.0.0.1",
            port=5432,
            database="demo",
            user="postgres",
            password="secret",
            source="test",
            framework="manual",
        )
        with patch.object(
            db_core,
            "connect",
            side_effect=db_core.DatabaseConnectionError("connection refused"),
        ):
            result = db_core.probe_datasource(datasource)
        self.assertEqual(result["connection_status"], "unavailable")
        self.assertEqual(result["version_status"], "not_probed")

        connection = FakeDatabaseConnection()
        with patch.object(
            connection,
            "cursor",
            side_effect=RuntimeError("version query denied"),
        ):
            result = db_core.probe_server_version(connection, "postgresql")
        self.assertEqual(result["connection_status"], "connected")
        self.assertEqual(result["version_status"], "query_failed")


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
        self.assertEqual(
            parser.parse_args(["open", "prod", "root@example.com", "--jump", "bastion", "--jump", "inner"]).jump,
            ["bastion", "inner"],
        )
        self.assertEqual(parser.parse_args(["run", "prod", "true"]).timeout, 120)
        self.assertEqual(parser.parse_args(["job", "prod", "job-1"]).timeout, 120)
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

    def test_new_job_and_forward_commands_parse(self) -> None:
        parser = ssh_ops.build_parser()
        self.assertEqual(parser.parse_args(["jobs", "prod"]).limit, 100)
        self.assertTrue(parser.parse_args(["job", "prod", "job-1", "--follow"]).follow)
        forward = parser.parse_args(["forward", "open", "prod", "15432", "db.internal", "5432"])
        self.assertEqual(forward.forward_action, "open")
        self.assertEqual(forward.local_port_arg, 15432)
        self.assertEqual(forward.remote_host_arg, "db.internal")
        self.assertEqual(forward.remote_port_arg, 5432)

    def test_job_status_parses_metadata_and_tail_sections(self) -> None:
        output = (
            "META_JSON\n"
            '{"command":"sleep 10","created_at":"2026-08-22T00:00:00Z",'
            '"stdout_log":"/tmp/stdout.log","stderr_log":"/tmp/stderr.log"}\n'
            "STATUS_JSON\n"
            '{"state":"running","pid":42,"started_at":"2026-08-22T00:00:00Z"}\n'
            "STDOUT_TAIL\n"
            "line one\n\n"
            "STDERR_TAIL\n"
            "warning\n"
        )
        with patch.object(ssh_ops, "remote_home", return_value="/home/test"):
            with patch.object(ssh_ops, "exec_command", return_value={"exit_code": 0, "stdout": output}):
                result = ssh_ops.remote_job_status(object(), "job-1", 10, 5)
        self.assertEqual(result["command"], "sleep 10")
        self.assertEqual(result["status"]["state"], "running")
        self.assertEqual(result["stdout"], "line one\n")
        self.assertEqual(result["stderr"], "warning\n")

    def test_follow_event_separates_state_and_streams(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            ssh_ops.render_follow_event("job-1", "state", {"state": "running"}, False)
            ssh_ops.render_follow_event("job-1", "stdout", {"data": "line\n"}, False)
            ssh_ops.render_follow_event("job-1", "stderr", {"data": "warn\n"}, False)
        self.assertEqual(stdout.getvalue(), "# job=job-1 state=running\n# stdout\nline\n# stderr\nwarn\n")

    def test_job_follow_tracks_offsets_until_terminal_logs_are_drained(self) -> None:
        with patch.object(
            ssh_ops,
            "remote_job_status",
            return_value={"job_id": "job-1", "remote_dir": "/home/test/.agent-ops/jobs/job-1", "status": {"state": "finished", "exit_code": 0}},
        ), patch.object(
            ssh_ops,
            "remote_log_delta",
            side_effect=[("out\n", 4, 4, False), ("err\n", 4, 4, False)],
        ):
            result = ssh_ops.remote_job_follow(object(), "job-1", 0, 0, 5, 64)
        self.assertTrue(result["follow_complete"])
        self.assertEqual(result["stdout_offset"], 4)
        self.assertEqual(result["stderr_offset"], 4)
        self.assertEqual(result["stdout_delta"], "out\n")
        self.assertEqual(result["stderr_delta"], "err\n")


class SshProfileChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "profiles": {
                "jump-1": {"host": "jump-1", "port": 22, "user": "tester"},
                "jump-2": {"host": "jump-2", "port": 22, "user": "tester", "jump_profiles": ["jump-1"]},
                "target": {"host": "target", "port": 22, "user": "tester", "jump_profiles": ["jump-2"]},
            },
            "aliases": {"one": "jump-1", "two": "jump-2", "target": "target"},
        }

    def test_recursive_chain_is_ordered_from_first_jump_to_target(self) -> None:
        self.assertEqual(
            ssh_ops.resolve_profile_chain(self.config, "target"),
            ["jump-1", "jump-2", "target"],
        )

    def test_jump_cycle_is_rejected(self) -> None:
        self.config["profiles"]["jump-1"]["jump_profiles"] = ["target"]
        with self.assertRaisesRegex(ValueError, "SSH jump cycle detected"):
            ssh_ops.resolve_profile_chain(self.config, "target")

    def test_jump_profile_cannot_be_forgotten_while_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            old_config_file = ssh_ops.CONFIG_FILE
            ssh_ops.CONFIG_FILE = Path(temp) / "ssh.toml"
            try:
                config_store.write_toml(ssh_ops.CONFIG_FILE, {
                    "profiles": {
                        "jump-1": self.config["profiles"]["jump-1"],
                        "target": {
                            **self.config["profiles"]["target"],
                            "jump_profiles": ["jump-1"],
                        },
                    },
                    "aliases": {"one": "jump-1", "target": "target"},
                })
                with self.assertRaisesRegex(ValueError, "used by jump profiles"):
                    ssh_ops.run_forget(argparse.Namespace(alias="one"))
                saved = config_store.load_toml(ssh_ops.CONFIG_FILE)
                self.assertIn("one", saved["aliases"])
            finally:
                ssh_ops.CONFIG_FILE = old_config_file

    def test_connection_chain_uses_each_previous_transport_as_next_socket(self) -> None:
        class FakeChannel:
            def __init__(self, name: str):
                self.name = name
                self.closed = False

            def close(self) -> None:
                self.closed = True

        class FakeTransport:
            def __init__(self, name: str):
                self.name = name
                self.channels: list[FakeChannel] = []

            def is_active(self) -> bool:
                return True

            def open_channel(self, _kind: str, destination: tuple[str, int], _source: tuple[str, int], timeout: int) -> FakeChannel:
                channel = FakeChannel(f"{self.name}->{destination[0]}:{destination[1]}:{timeout}")
                self.channels.append(channel)
                return channel

        class FakeClient:
            def __init__(self, name: str):
                self.name = name
                self.transport = FakeTransport(name)
                self.closed = False

            def get_transport(self) -> FakeTransport:
                return self.transport

            def close(self) -> None:
                self.closed = True

        clients: list[FakeClient] = []
        calls: list[tuple[str, int, object | None]] = []

        def fake_connect(profile: dict[str, object], timeout: int, sock: object | None = None) -> FakeClient:
            calls.append((str(profile["host"]), timeout, sock))
            client = FakeClient(str(profile["host"]))
            clients.append(client)
            return client

        with patch.object(ssh_ops, "connect_client", side_effect=fake_connect):
            target, connected, channels = ssh_ops.connect_profile_chain(self.config, "target", timeout=7)

        self.assertIs(target, clients[-1])
        self.assertEqual(connected, clients)
        self.assertEqual([call[0] for call in calls], ["jump-1", "jump-2", "target"])
        self.assertEqual([call[1] for call in calls], [7, 7, 7])
        self.assertIsNone(calls[0][2])
        self.assertIs(calls[1][2], channels[0])
        self.assertIs(calls[2][2], channels[1])

    def test_connection_failure_closes_already_opened_chain(self) -> None:
        class FakeChannel:
            def close(self) -> None:
                pass

        class FakeTransport:
            def is_active(self) -> bool:
                return True

            def open_channel(self, *_args, **_kwargs) -> FakeChannel:
                return FakeChannel()

        class FakeClient:
            def __init__(self) -> None:
                self.transport = FakeTransport()
                self.closed = False

            def get_transport(self):
                return self.transport

            def close(self) -> None:
                self.closed = True

        first = FakeClient()

        def fake_connect(profile: dict[str, object], timeout: int, sock: object | None = None):
            if profile["host"] == "jump-1":
                return first
            raise OSError("auth failed")

        with patch.object(ssh_ops, "connect_client", side_effect=fake_connect):
            with self.assertRaisesRegex(RuntimeError, "connection failed at two"):
                ssh_ops.connect_profile_chain(self.config, "target", timeout=7)
        self.assertTrue(first.closed)


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


class ForwardingMockSshServer(MockSshServer):
    def __init__(self) -> None:
        self.direct_destinations: dict[int, tuple[str, int]] = {}

    def check_channel_direct_tcpip_request(
        self,
        chanid: int,
        _origin: tuple[str, int],
        destination: tuple[str, int],
    ) -> int:
        self.direct_destinations[chanid] = destination
        return paramiko.OPEN_SUCCEEDED


class MockSshService:
    def __init__(self, forwarding: bool = False) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.forwarding = forwarding
        self.stop_event = threading.Event()
        self.transports: list[paramiko.Transport] = []
        self.channels: list[paramiko.Channel] = []
        self.transport_threads: list[threading.Thread] = []
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
            worker = threading.Thread(target=self.handle_transport, args=(transport, host_key), daemon=True)
            self.transport_threads.append(worker)
            worker.start()

    def handle_transport(self, transport: paramiko.Transport, host_key: paramiko.RSAKey) -> None:
        server = ForwardingMockSshServer() if self.forwarding else MockSshServer()
        try:
            transport.add_server_key(host_key)
            transport.start_server(server=server)
            while transport.is_active() and not self.stop_event.is_set():
                channel = transport.accept(0.2)
                if channel is not None:
                    self.channels.append(channel)
                    if isinstance(server, ForwardingMockSshServer):
                        destination = server.direct_destinations.get(channel.chanid)
                        if destination:
                            threading.Thread(
                                target=self.bridge_channel,
                                args=(channel, destination),
                                daemon=True,
                            ).start()
        finally:
            transport.close()

    @staticmethod
    def bridge_channel(channel: paramiko.Channel, destination: tuple[str, int]) -> None:
        try:
            downstream = socket.create_connection(destination, timeout=5)
        except OSError:
            channel.close()
            return

        def forward(source, target) -> None:
            try:
                while True:
                    data = source.recv(64 * 1024)
                    if not data:
                        break
                    target.sendall(data)
            except (OSError, EOFError, socket.timeout):
                pass

        upstream = threading.Thread(target=forward, args=(channel, downstream), daemon=True)
        downstream_thread = threading.Thread(target=forward, args=(downstream, channel), daemon=True)
        upstream.start()
        downstream_thread.start()
        upstream.join()
        try:
            channel.close()
        except (OSError, EOFError):
            pass
        try:
            downstream.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        downstream.close()
        downstream_thread.join(timeout=1)

    def close(self) -> None:
        self.stop_event.set()
        for channel in self.channels:
            channel.close()
        for transport in self.transports:
            transport.close()
        self.listener.close()
        self.thread.join(timeout=2)
        for worker in self.transport_threads:
            worker.join(timeout=2)


class LocalEchoService:
    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                client, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self.handle, args=(client,), daemon=True).start()

    @staticmethod
    def handle(client: socket.socket) -> None:
        with client:
            while True:
                data = client.recv(64 * 1024)
                if not data:
                    return
                client.sendall(data)

    def close(self) -> None:
        self.stop_event.set()
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

    def test_local_forward_lifecycle_uses_existing_daemon_connection(self) -> None:
        service = MockSshService(forwarding=True)
        echo = LocalEchoService()
        service.start()
        echo.start()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {
                **os.environ,
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
                "PI_SESSION_ID": "ssh-forward-integration",
            }
            sshx = str(BIN_DIR / "sshx")

            def run(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sshx, *args], env=env, cwd=root, text=True,
                    capture_output=True, timeout=timeout,
                )

            try:
                opened = run("open", "local", f"tester@127.0.0.1:{service.port}", "plain-password")
                self.assertEqual(opened.returncode, 0, opened.stderr + opened.stdout)

                created = run(
                    "forward", "open", "--json", "local", "0", "127.0.0.1", str(echo.port),
                )
                self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
                forward = json.loads(created.stdout)
                self.assertEqual(forward["state"], "open")
                self.assertGreater(forward["local_port"], 0)

                with socket.create_connection(("127.0.0.1", forward["local_port"]), timeout=5) as client:
                    client.sendall(b"through-forward")
                    self.assertEqual(client.recv(64), b"through-forward")

                listed = run("forward", "list", "--json", "local")
                self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
                self.assertEqual(json.loads(listed.stdout)["forwards"][0]["state"], "open")

                closed = run("forward", "close", "--json", "local", forward["forward_id"])
                self.assertEqual(closed.returncode, 0, closed.stderr + closed.stdout)
                self.assertTrue(json.loads(closed.stdout)["closed"])

                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", forward["local_port"]), timeout=1)
                status = run("forward", "status", "--json", "local", forward["forward_id"])
                self.assertEqual(json.loads(status.stdout)["forwards"][0]["state"], "closed")
            finally:
                run("close", "local")
                echo.close()
                service.close()


class SshJumpIntegrationTest(unittest.TestCase):
    def test_three_hop_chain_executes_on_final_server(self) -> None:
        services = [
            MockSshService(forwarding=True),
            MockSshService(forwarding=True),
            MockSshService(),
        ]
        for service in services:
            service.start()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = {
                **os.environ,
                "XDG_DATA_HOME": str(REPO_ROOT / "runtime"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_RUNTIME_DIR": str(root / "runtime"),
                "LYSTAR_SKILL_AUTO_UPDATE": "0",
                "PI_SESSION_ID": "ssh-jump-integration",
            }
            sshx = str(BIN_DIR / "sshx")

            def run(*args: str, timeout: int = 20) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [sshx, *args], env=env, cwd=root, text=True,
                    capture_output=True, timeout=timeout,
                )

            try:
                opened_one = run("open", "server-1", f"tester@127.0.0.1:{services[0].port}", "plain-password")
                self.assertEqual(opened_one.returncode, 0, opened_one.stderr + opened_one.stdout)
                opened_two = run(
                    "open", "server-2", f"tester@127.0.0.1:{services[1].port}",
                    "plain-password", "--jump", "server-1",
                )
                self.assertEqual(opened_two.returncode, 0, opened_two.stderr + opened_two.stdout)
                opened_three = run(
                    "open", "server-3", f"tester@127.0.0.1:{services[2].port}",
                    "plain-password", "--jump", "server-2",
                )
                self.assertEqual(opened_three.returncode, 0, opened_three.stderr + opened_three.stdout)

                executed = run("server-3", "echo through-jumps")
                self.assertEqual(executed.returncode, 0, executed.stderr + executed.stdout)
                self.assertEqual(executed.stdout, "ran:echo through-jumps\n")
                self.assertEqual(executed.stderr, "")

                status = run("status", "--json", "server-3")
                self.assertEqual(status.returncode, 0, status.stderr + status.stdout)
                status_payload = json.loads(status.stdout)["connections"][0]
                self.assertTrue(status_payload["connected"])
                self.assertEqual(status_payload["jumps"], ["server-2"])
            finally:
                run("close", "--all")
                for service in reversed(services):
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
