from __future__ import annotations

import hashlib
import json
import os
import sys
import subprocess
import tarfile
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("AGENT_OPS_BIN_DIR", REPO_ROOT / "bin"))


FAKE_SSHX = r'''#!/usr/bin/env python3
import json
import os

if os.environ.get("DEPLOYX_FAKE_MODE") == "unavailable":
    print(json.dumps({"error": "SSH daemon unavailable"}))
    raise SystemExit(1)

manifest = {
    "schema_version": 1,
    "release_id": "web-111111111111",
    "created_at": "2026-08-21T10:00:00Z",
    "artifact_name": "web-old.tar.gz",
    "artifact_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
    "state": "available",
    "previous_release": None,
    "service": "web.service",
}
stdout = "\n".join([
    "app_root\t/srv/apps/web",
    "releases_root\t/srv/apps/web/releases",
    "current_link\t/srv/apps/web/current",
    "app_root_exists\ttrue",
    "release_root_exists\ttrue",
    "release_root_is_dir\ttrue",
    "release_root_parent_exists\ttrue",
    "releases_root_exists\ttrue",
    "current_target\treleases/web-111111111111",
    "available_bytes\t9000000",
    "deployment_spec\t" + json.dumps({"service": "web.service"}),
    "last_result\t" + json.dumps({"status": "healthy", "service": "web.service"}),
    "manifest\tweb-111111111111\t" + json.dumps(manifest),
    "manifest\tbroken\t{not-json}",
]) + "\n"
print(json.dumps({"stdout": stdout, "stderr": "", "exit_code": 0}))
'''


FAKE_HOSTX = r'''#!/usr/bin/env python3
import json
import os

if os.environ.get("DEPLOYX_FAKE_MODE") == "unavailable":
    print(json.dumps({"error": "hostx unavailable"}))
    raise SystemExit(1)
if os.environ.get("DEPLOYX_FAKE_MODE") == "not_found":
    print(json.dumps({
        "schema_version": 1,
        "kind": "service",
        "status": "failed",
        "connection_status": "ok",
        "service": {
            "name": "web.service",
            "manager": "systemd",
            "load_state": "not-found",
            "active": "",
            "substate": "",
            "pid": None,
            "enabled": "",
            "error": "服务单元不存在",
        },
        "error": "服务单元不存在",
    }))
    raise SystemExit(1)
print(json.dumps({
    "schema_version": 1,
    "kind": "service",
    "status": "ok",
    "connection_status": "ok",
    "service": {
        "name": "web.service",
        "manager": "systemd",
        "load_state": "loaded",
        "active": "active",
        "substate": "running",
        "pid": 123,
        "enabled": "enabled",
        "error": None,
    },
}))
'''


FAKE_NEW_SERVICE_SSHX = r'''#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ.get("DEPLOYX_NEW_LOG")
if log_path:
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(sys.argv) + "\n")

if len(sys.argv) > 1 and sys.argv[1] == "put":
    print(json.dumps({"error": "remote write must not be called"}))
    raise SystemExit(1)

command = sys.argv[-1] if len(sys.argv) > 1 else ""
occupied = os.environ.get("DEPLOYX_NEW_MODE") == "occupied"
if "deployx:inspect" in command:
    app_root_exists = "true" if occupied else "false"
    stdout = "\n".join([
        "app_root\t/srv/apps/api",
        "releases_root\t/srv/apps/api/releases",
        "current_link\t/srv/apps/api/current",
        f"app_root_exists\t{app_root_exists}",
        "release_root_exists\ttrue",
        "release_root_is_dir\ttrue",
        "release_root_parent_exists\ttrue",
        "releases_root_exists\tfalse",
        "current_target\t",
        "available_bytes\t10000000",
    ]) + "\n"
elif "DEPLOYX_PATH" in command:
    stdout = "\n".join([
        "DEPLOYX_PATH\tconfig\t/etc/new-api\tfalse\tmissing",
        "DEPLOYX_PATH\tdata\t/var/lib/new-api\tfalse\tmissing",
    ]) + "\n"
else:
    stdout = ""
print(json.dumps({"stdout": stdout, "stderr": "", "exit_code": 0}))
'''


FAKE_NEW_SERVICE_HOSTX = r'''#!/usr/bin/env python3
import json
import sys

if "inspect" in sys.argv:
    print(json.dumps({
        "schema_version": 1,
        "kind": "service_inspect",
        "status": "failed",
        "connection_status": "ok",
        "service": {
            "name": "new-api.service",
            "manager": "systemd",
            "load_state": "not-found",
            "active": "",
            "substate": "",
            "pid": None,
            "enabled": "",
        },
        "error": "服务单元不存在",
    }))
    raise SystemExit(1)
if "ports" in sys.argv:
    print(json.dumps({
        "schema_version": 1,
        "kind": "ports",
        "status": "ok",
        "connection_status": "ok",
        "ports": [],
    }))
    raise SystemExit(0)
print(json.dumps({"error": "unexpected hostx command"}))
raise SystemExit(1)
'''


FAKE_ADOPT_SSHX = r'''#!/usr/bin/env python3
import json
import os
import sys

mode = os.environ.get("DEPLOYX_ADOPT_MODE", "legacy")
if mode == "unavailable":
    print(json.dumps({"error": "SSH daemon unavailable"}))
    raise SystemExit(1)

command = sys.argv[-1] if len(sys.argv) > 1 else ""
if "deployx:inspect" in command:
    versioned = mode == "versioned"
    rows = [
        "app_root\t/srv/apps/api",
        "releases_root\t/srv/apps/api/releases",
        "current_link\t/srv/apps/api/current",
        "app_root_exists\ttrue",
        "release_root_exists\ttrue",
        "release_root_is_dir\ttrue",
        "release_root_parent_exists\ttrue",
        f"releases_root_exists\t{'true' if versioned else 'false'}",
        f"current_target\t{'releases/api-111111111111' if versioned else ''}",
        "available_bytes\t9000000",
    ]
    print(json.dumps({"stdout": "\n".join(rows) + "\n", "stderr": "", "exit_code": 0}))
else:
    print(json.dumps({"stdout": "", "stderr": "", "exit_code": 0}))
'''


FAKE_ADOPT_HOSTX = r'''#!/usr/bin/env python3
import json
import os
import sys

mode = os.environ.get("DEPLOYX_ADOPT_MODE", "legacy")
if mode == "unavailable":
    print(json.dumps({"error": "hostx unavailable"}))
    raise SystemExit(1)
if "inspect" not in sys.argv:
    print(json.dumps({"error": "unexpected hostx command"}))
    raise SystemExit(1)

drift = mode == "drift"
prefix = "/srv/apps/other" if drift else "/srv/apps/api"
config = "/etc/other/config.yaml" if drift else "/etc/api/config.yaml"
data = "/var/lib/other" if drift else "/var/lib/api"
log = "/var/log/other.log" if drift else "/var/log/api.log"
port = 18081 if drift else 18080
payload = {
    "schema_version": 1,
    "kind": "service_inspect",
    "status": "ok",
    "connection_status": "ok",
    "service": {
        "name": "api.service",
        "manager": "systemd",
        "load_state": "loaded",
        "active": "active",
        "substate": "running",
        "pid": 321,
        "enabled": "enabled",
    },
    "systemd": {"fragment_path": "/etc/systemd/system/api.service"},
    "unit_file": {"path": "/etc/systemd/system/api.service", "exists": True},
    "drop_ins": [],
    "exec_start": {"main": f"{{ path={prefix}/bin/api ; argv[]={prefix}/bin/api --config {config} ; }}"},
    "user": "other" if drift else "api",
    "working_directory": prefix,
    "environment_files": [{"path": "/etc/api/api.env", "optional": False}],
    "dependencies": {"requires": ["network.target"]},
    "process": {"pid": 321, "user": "other" if drift else "api", "cwd": prefix},
    "ports": [{"protocol": "tcp", "local_address": "127.0.0.1", "port": port, "pid": 321, "process": "api"}],
    "ports_status": "ok",
    "ports_scope": "service",
    "paths": {
        "release": [prefix],
        "config": [config],
        "data": [data],
        "log": [log],
        "other": [],
        "evidence": [],
    },
}
if mode == "float":
    payload["process"] = {
        "pid": 321,
        "user": "api",
        "cwd": "/srv/apps/api",
        "cpu": 0.25,
        "memory": 1.5,
    }
print(json.dumps(payload))
'''


FAKE_APPLY_SSHX = r'''#!/usr/bin/env python3
import json
import os
import re
import sys

MODE = os.environ.get("DEPLOYX_FAKE_MODE", "")
STATE_PATH = os.environ.get("DEPLOYX_FAKE_STATE", "")
OLD_RELEASE = "web-old-111111111111"
NEW_RELEASE = "web-new-222222222222"


def manifest(release_id, sha):
    return {
        "schema_version": 1,
        "release_id": release_id,
        "created_at": "2026-08-21T10:00:00Z",
        "artifact_name": release_id + ".tar.gz",
        "artifact_sha256": sha,
        "state": "available",
        "previous_release": None,
        "service": "web.service",
    }


def load_state():
    if STATE_PATH and os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    if os.environ.get("DEPLOYX_FAKE_ROLLBACK") == "1":
        value = {
            "current": NEW_RELEASE,
            "releases": {
                OLD_RELEASE: manifest(OLD_RELEASE, "1" * 64),
                NEW_RELEASE: manifest(NEW_RELEASE, "2" * 64),
            },
        }
    else:
        value = {
            "current": OLD_RELEASE,
            "releases": {OLD_RELEASE: manifest(OLD_RELEASE, "1" * 64)},
        }
    value["health_calls"] = 0
    return value


def save_state(value):
    if STATE_PATH:
        with open(STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(value, handle)


def inspect(value):
    rows = [
        "app_root\t/srv/apps/web",
        "releases_root\t/srv/apps/web/releases",
        "current_link\t/srv/apps/web/current",
        "app_root_exists\ttrue",
        "release_root_exists\ttrue",
        "release_root_is_dir\ttrue",
        "release_root_parent_exists\ttrue",
        "releases_root_exists\ttrue",
        "current_target\treleases/" + value["current"],
        "available_bytes\t9000000",
        "deployment_spec\t" + json.dumps({"service": "web.service", "health_checks": ["facts", "disk:/"]}),
        "last_result\t" + json.dumps({"status": "healthy", "service": "web.service"}),
    ]
    for release_id, item in value["releases"].items():
        rows.append("manifest\t" + release_id + "\t" + json.dumps(item))
    return "\n".join(rows) + "\n"


if MODE == "unavailable":
    print(json.dumps({"error": "SSH daemon unavailable"}))
    raise SystemExit(1)

if len(sys.argv) > 1 and sys.argv[1] == "put":
    if os.environ.get("DEPLOYX_UPLOAD_LOG"):
        with open(os.environ["DEPLOYX_UPLOAD_LOG"], "a", encoding="utf-8") as handle:
            json.dump(sys.argv, handle)
            handle.write("\n")
    if MODE == "put_fail":
        print(json.dumps({"error": "upload failed"}))
        raise SystemExit(1)
    print(json.dumps({"files": 1, "bytes": 123, "remote": sys.argv[-3], "sha256": "1" * 64}))
    raise SystemExit(0)

command = sys.argv[-1] if len(sys.argv) > 1 else ""
value = load_state()
if "deployx:inspect" in command:
    print(json.dumps({"stdout": inspect(value), "stderr": "", "exit_code": 0}))
elif "DEPLOYX_STAGE_JSON" in command:
    if MODE == "stage_fail":
        print(json.dumps({"stdout": "", "stderr": "stage failed", "exit_code": 1}))
        raise SystemExit(0)
    match = re.search(r"release_path=(?:'([^']+)'|([^\s]+))", command)
    release_path = match.group(1) or match.group(2) if match else ""
    release_id = release_path.rsplit("/", 1)[-1] if release_path else "web-candidate"
    sha_match = re.search(r'"artifact_sha256":"([0-9a-f]{64})"', command)
    sha = sha_match.group(1) if sha_match else "3" * 64
    reused = release_id in value["releases"]
    if not reused:
        value["releases"][release_id] = manifest(release_id, sha)
    save_state(value)
    print(json.dumps({
        "stdout": "DEPLOYX_STAGE_JSON\t" + json.dumps({
            "release_id": release_id,
            "release_path": "/srv/apps/web/releases/" + release_id,
            "staged": True,
            "reused": reused,
        }) + "\n",
        "stderr": "",
        "exit_code": 0,
    }))
elif "DEPLOYX_STATE_WRITTEN" in command:
    print(json.dumps({"stdout": "DEPLOYX_STATE_WRITTEN\ttrue\n", "stderr": "", "exit_code": 0}))
elif "DEPLOYX_SWITCH_TARGET" in command:
    if MODE == "switch_fail":
        print(json.dumps({"stdout": "", "stderr": "switch failed", "exit_code": 1}))
        raise SystemExit(0)
    match = re.search(r"target=(?:'([^']+)'|([^\s]+))", command)
    target = (match.group(1) or match.group(2)) if match else "releases/web-candidate"
    value["current"] = target.rsplit("/", 1)[-1]
    save_state(value)
    print(json.dumps({"stdout": "DEPLOYX_SWITCH_TARGET\t" + target + "\n", "stderr": "", "exit_code": 0}))
elif "systemctl restart" in command:
    if MODE == "restart_fail":
        print(json.dumps({"stdout": "", "stderr": "restart failed", "exit_code": 1}))
        raise SystemExit(0)
    print(json.dumps({"stdout": "DEPLOYX_RESTART_STATUS\tactive\n", "stderr": "", "exit_code": 0}))
else:
    print(json.dumps({"stdout": "", "stderr": "", "exit_code": 0}))
'''


FAKE_APPLY_HOSTX = r'''#!/usr/bin/env python3
import json
import os

MODE = os.environ.get("DEPLOYX_FAKE_MODE", "")
STATE_PATH = os.environ.get("DEPLOYX_FAKE_STATE", "")


def state():
    if STATE_PATH and os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    return {"health_calls": 0}


def save(value):
    if STATE_PATH:
        with open(STATE_PATH, "w", encoding="utf-8") as handle:
            json.dump(value, handle)


if MODE == "unavailable":
    print(json.dumps({"error": "hostx unavailable"}))
    raise SystemExit(1)

if "health" in __import__("sys").argv:
    value = state()
    value["health_calls"] = value.get("health_calls", 0) + 1
    fail = MODE == "health_fail" or (MODE == "health_fail_once" and value["health_calls"] == 1)
    save(value)
    if fail:
        print(json.dumps({
            "schema_version": 1,
            "kind": "health",
            "status": "fail",
            "connection_status": "ok",
            "checks": [{"name": "facts", "status": "fail", "result": {"status": "failed", "error": "fake health failure"}}],
        }))
        raise SystemExit(1)
    print(json.dumps({
        "schema_version": 1,
        "kind": "health",
        "status": "pass",
        "connection_status": "ok",
        "checks": [{"name": "facts", "status": "pass", "result": {"status": "ok"}}],
    }))
    raise SystemExit(0)

print(json.dumps({
    "schema_version": 1,
    "kind": "service",
    "status": "ok",
    "connection_status": "ok",
    "service": {
        "name": "web.service",
        "manager": "systemd",
        "load_state": "loaded",
        "active": "active",
        "substate": "running",
        "pid": 123,
        "enabled": "enabled",
        "error": None,
    },
}))
'''


class DeployOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fake_sshx = self.root / "fake-sshx"
        self.fake_hostx = self.root / "fake-hostx"
        self.fake_new_service_sshx = self.root / "fake-new-service-sshx"
        self.fake_new_service_hostx = self.root / "fake-new-service-hostx"
        self.fake_adopt_sshx = self.root / "fake-adopt-sshx"
        self.fake_adopt_hostx = self.root / "fake-adopt-hostx"
        self.new_service_log = self.root / "new-service.log"
        self.fake_apply_sshx = self.root / "fake-apply-sshx"
        self.fake_apply_hostx = self.root / "fake-apply-hostx"
        self.fake_state = self.root / "fake-state.json"
        self.upload_log = self.root / "upload.log"
        self.backup_repository = self.root / "backups"
        self.fake_sshx.write_text(FAKE_SSHX, encoding="utf-8")
        self.fake_hostx.write_text(FAKE_HOSTX, encoding="utf-8")
        self.fake_new_service_sshx.write_text(FAKE_NEW_SERVICE_SSHX, encoding="utf-8")
        self.fake_new_service_hostx.write_text(FAKE_NEW_SERVICE_HOSTX, encoding="utf-8")
        self.fake_adopt_sshx.write_text(FAKE_ADOPT_SSHX, encoding="utf-8")
        self.fake_adopt_hostx.write_text(FAKE_ADOPT_HOSTX, encoding="utf-8")
        self.fake_apply_sshx.write_text(FAKE_APPLY_SSHX, encoding="utf-8")
        self.fake_apply_hostx.write_text(FAKE_APPLY_HOSTX, encoding="utf-8")
        self.fake_sshx.chmod(0o755)
        self.fake_hostx.chmod(0o755)
        self.fake_new_service_sshx.chmod(0o755)
        self.fake_new_service_hostx.chmod(0o755)
        self.fake_adopt_sshx.chmod(0o755)
        self.fake_adopt_hostx.chmod(0o755)
        self.fake_apply_sshx.chmod(0o755)
        self.fake_apply_hostx.chmod(0o755)
        self.source_dir = self.root / "mall-admin"
        self.source_dir.mkdir()
        self.artifact = self.root / "web.tar.gz"
        with tarfile.open(self.artifact, "w:gz") as archive:
            source = self.root / "index.html"
            source.write_text("hello", encoding="utf-8")
            archive.add(source, arcname="index.html")
        self.digest = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        self.env = {
            **os.environ,
            "XDG_DATA_HOME": str(REPO_ROOT / "runtime"),
            "LYSTAR_SKILL_AUTO_UPDATE": "0",
            "DEPLOYX_SSHX": str(self.fake_sshx),
            "DEPLOYX_HOSTX": str(self.fake_hostx),
            "DEPLOYX_REGISTRY_FILE": str(self.root / "config" / "agent-ops" / "ops.toml"),
            "DEPLOYX_REGISTRY_REVISION_DIR": str(self.root / "state" / "agent-ops" / "ops" / "revisions"),
            "DEPLOYX_RECIPE_HOME": str(self.root / "recipes"),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_deployx(self, *args: str, mode: str = "") -> subprocess.CompletedProcess[str]:
        env = {**self.env, "DEPLOYX_FAKE_MODE": mode}
        return subprocess.run(
            [str(BIN_DIR / "deployx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_backupx(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = {
            **self.env,
            "BACKUPX_REGISTRY_FILE": self.env["DEPLOYX_REGISTRY_FILE"],
            "BACKUPX_REGISTRY_REVISION_DIR": self.env["DEPLOYX_REGISTRY_REVISION_DIR"],
        }
        return subprocess.run(
            [str(BIN_DIR / "backupx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_apply_deployx(
        self,
        *args: str,
        mode: str = "",
        rollback_state: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **self.env,
            "DEPLOYX_SSHX": str(self.fake_apply_sshx),
            "DEPLOYX_HOSTX": str(self.fake_apply_hostx),
            "DEPLOYX_FAKE_MODE": mode,
            "DEPLOYX_FAKE_STATE": str(self.fake_state),
            "DEPLOYX_UPLOAD_LOG": str(self.upload_log),
        }
        if rollback_state:
            env["DEPLOYX_FAKE_ROLLBACK"] = "1"
        return subprocess.run(
            [str(BIN_DIR / "deployx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_new_service_deployx(
        self,
        *args: str,
        mode: str = "",
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **self.env,
            "DEPLOYX_SSHX": str(self.fake_new_service_sshx),
            "DEPLOYX_HOSTX": str(self.fake_new_service_hostx),
            "DEPLOYX_NEW_MODE": mode,
            "DEPLOYX_NEW_LOG": str(self.new_service_log),
        }
        return subprocess.run(
            [str(BIN_DIR / "deployx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def run_observed_deployx(
        self,
        *args: str,
        mode: str = "legacy",
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **self.env,
            "DEPLOYX_SSHX": str(self.fake_adopt_sshx),
            "DEPLOYX_HOSTX": str(self.fake_adopt_hostx),
            "DEPLOYX_ADOPT_MODE": mode,
        }
        return subprocess.run(
            [str(BIN_DIR / "deployx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )

    def register_new_service_objects(self) -> None:
        project = self.run_deployx(
            "project",
            "register",
            "mall-admin",
            "--name",
            "商城后台",
            "--local-path",
            str(self.source_dir),
            "--json",
        )
        self.assertEqual(project.returncode, 0, project.stderr)
        environment = self.run_deployx(
            "environment",
            "register",
            "prod",
            "--name",
            "生产",
            "--json",
        )
        self.assertEqual(environment.returncode, 0, environment.stderr)
        service = self.run_deployx(
            "service",
            "register",
            "mall-admin",
            "api",
            "--name",
            "API",
            "--service-type",
            "systemd",
            "--json",
        )
        self.assertEqual(service.returncode, 0, service.stderr)

    def test_plan_is_read_only_and_returns_stable_contract(self) -> None:
        result = self.run_deployx(
            "plan",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--artifact-sha256",
            self.digest,
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--health-check",
            "facts,disk:/",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "deployment_plan")
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["artifact"]["status"], "ok")
        self.assertEqual(payload["candidate_release"]["release_id"], f"web-{self.digest[:12]}")
        self.assertEqual(payload["current_release"]["release_id"], "web-111111111111")
        self.assertEqual(payload["service_status"]["service"]["active"], "active")
        self.assertEqual([item["status"] for item in payload["preconditions"]], ["pass", "pass", "pass", "pass"])

    def test_status_and_history_read_manifests_without_remote_write(self) -> None:
        status = self.run_deployx(
            "status",
            "prod",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--json",
        )
        self.assertEqual(status.returncode, 0, status.stderr)
        status_payload = json.loads(status.stdout)
        self.assertEqual(status_payload["kind"], "deployment_status")
        self.assertEqual(status_payload["current_release"]["release_id"], "web-111111111111")
        self.assertEqual(status_payload["service"], "web.service")
        self.assertEqual(status_payload["last_result"]["status"], "healthy")
        self.assertEqual(len(status_payload["warnings"]), 1)

        missing_service = self.run_deployx(
            "status",
            "prod",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--json",
            mode="not_found",
        )
        self.assertEqual(missing_service.returncode, 0)
        missing_service_payload = json.loads(missing_service.stdout)
        self.assertEqual(missing_service_payload["status"], "partial")
        self.assertEqual(missing_service_payload["service_status"]["status"], "failed")

        history = self.run_deployx(
            "history",
            "prod",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--limit",
            "1",
            "--json",
        )
        self.assertEqual(history.returncode, 0, history.stderr)
        history_payload = json.loads(history.stdout)
        self.assertEqual(history_payload["kind"], "deployment_history")
        self.assertEqual(len(history_payload["releases"]), 1)
        self.assertEqual(history_payload["releases"][0]["release_id"], "web-111111111111")

    def test_invalid_artifact_and_unavailable_target_are_explicit(self) -> None:
        missing = self.run_deployx(
            "plan",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.root / "missing.tar.gz"),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(json.loads(missing.stdout)["status"], "blocked")

        unavailable = self.run_deployx(
            "status",
            "prod",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--json",
            mode="unavailable",
        )
        self.assertNotEqual(unavailable.returncode, 0)
        unavailable_payload = json.loads(unavailable.stdout)
        self.assertEqual(unavailable_payload["status"], "unavailable")
        self.assertEqual(unavailable_payload["connection_status"], "unavailable")

        not_found = self.run_deployx(
            "plan",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
            mode="not_found",
        )
        self.assertNotEqual(not_found.returncode, 0)
        not_found_payload = json.loads(not_found.stdout)
        self.assertEqual(not_found_payload["status"], "blocked")
        service_precondition = next(
            item for item in not_found_payload["preconditions"] if item["name"] == "service"
        )
        self.assertEqual(service_precondition["status"], "fail")

    def test_apply_completes_stages_and_writes_remote_state(self) -> None:
        result = self.run_apply_deployx(
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--artifact-sha256",
            self.digest,
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--health-check",
            "facts,disk:/",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["stage"], "healthy")
        self.assertEqual(
            [item["name"] for item in payload["stages"]],
            ["uploaded", "staged", "switched", "restarted", "healthy"],
        )
        self.assertFalse(payload["staged_release"]["reused"])
        self.assertEqual(payload["persistence"]["status"], "ok")
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["current"], f"web-{self.digest[:12]}")
        self.assertIn(f"web-{self.digest[:12]}", state["releases"])

    def test_apply_health_failure_rolls_back_to_previous_release(self) -> None:
        result = self.run_apply_deployx(
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
            mode="health_fail_once",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "rolled_back")
        self.assertEqual(payload["stage"], "rolled_back")
        self.assertEqual(payload["failed_stage"], "health")
        self.assertEqual(payload["rollback"]["status"], "ok")
        self.assertEqual(payload["rollback"]["release_id"], "web-old-111111111111")
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["current"], "web-old-111111111111")

    def test_repeated_apply_reuses_the_same_release_directory(self) -> None:
        args = (
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
        )
        first = self.run_apply_deployx(*args)
        second = self.run_apply_deployx(*args)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        second_payload = json.loads(second.stdout)
        self.assertTrue(second_payload["staged_release"]["reused"])
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(len(state["releases"]), 2)

    def test_rollback_uses_previous_release_and_infers_service(self) -> None:
        result = self.run_apply_deployx(
            "rollback",
            "prod",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--json",
            rollback_state=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "healthy")
        self.assertEqual(payload["release"]["release_id"], "web-old-111111111111")
        self.assertEqual(
            [item["name"] for item in payload["stages"]],
            ["staged", "switched", "restarted", "healthy"],
        )
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["current"], "web-old-111111111111")

    def test_apply_upload_failure_does_not_switch_current(self) -> None:
        result = self.run_apply_deployx(
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
            mode="put_fail",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["failed_stage"], "upload")
        self.assertEqual([item["name"] for item in payload["stages"]], ["failed"])
        self.assertEqual(payload["persistence"]["status"], "ok")
        self.assertTrue(payload["resume"]["available"])
        self.assertEqual(payload["cleanup"]["status"], "preserved")
        upload_args = json.loads(self.upload_log.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("--resume", upload_args)
        self.assertIn("--chunk-size", upload_args)
        self.assertFalse(self.fake_state.exists())

    def test_apply_stage_failure_persists_failure_and_keeps_resume_metadata(self) -> None:
        result = self.run_apply_deployx(
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
            mode="stage_fail",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["failed_stage"], "stage")
        self.assertEqual(payload["persistence"]["status"], "ok")
        self.assertTrue(payload["resume"]["available"])
        self.assertEqual(payload["cleanup"]["status"], "preserved")
        retry = self.run_apply_deployx(
            "apply",
            "prod",
            "--app",
            "web",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--json",
            mode="stage_fail",
        )
        self.assertNotEqual(retry.returncode, 0)
        upload_calls = [
            json.loads(line)
            for line in self.upload_log.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(len(upload_calls), 2)
        self.assertEqual(upload_calls[0][5], upload_calls[1][5])
        self.assertIn(".deployx-staging/upload-", upload_calls[0][5])
        self.assertFalse(self.fake_state.exists())

    def test_registry_recipe_and_new_service_plan_are_read_only(self) -> None:
        self.register_new_service_objects()
        prepare = self.root / "prepare.sh"
        prepare.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        prepare.chmod(0o755)
        recipe = self.run_deployx(
            "recipe",
            "register",
            "mall-script",
            "--name",
            "商城托管脚本",
            "--stage",
            f"prepare={prepare}",
            "--json",
        )
        self.assertEqual(recipe.returncode, 0, recipe.stderr)
        recipe_payload = json.loads(recipe.stdout)
        self.assertEqual(recipe_payload["object"]["strategy"], "managed-script")
        self.assertTrue(Path(recipe_payload["object"]["stages"]["prepare"]["path"]).is_file())

        listed = self.run_deployx("recipe", "list", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        listed_payload = json.loads(listed.stdout)
        self.assertEqual(
            {item["id"] for item in listed_payload["objects"]},
            {"tar.gz-systemd", "mall-script"},
        )

        plan = self.run_new_service_deployx(
            "service",
            "plan",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--artifact",
            str(self.artifact),
            "--artifact-sha256",
            self.digest,
            "--release-root",
            "/srv/apps",
            "--unit",
            "new-api.service",
            "--path",
            "config=/etc/new-api",
            "--path",
            "data=/var/lib/new-api",
            "--port",
            "tcp:0.0.0.0:18080:http:true",
            "--json",
        )
        self.assertEqual(plan.returncode, 0, plan.stderr + plan.stdout)
        plan_payload = json.loads(plan.stdout)
        self.assertEqual(plan_payload["status"], "ready")
        self.assertEqual(plan_payload["deployment_id"], "mall-admin/api/prod")
        self.assertFalse(plan_payload["remote_write"])
        self.assertEqual(
            [item["status"] for item in plan_payload["preconditions"]],
            ["pass", "pass", "pass", "pass", "pass", "pass", "pass", "pass"],
        )
        registry = tomllib.loads(
            (self.root / "config" / "agent-ops" / "ops.toml").read_text(encoding="utf-8")
        )
        self.assertNotIn("mall-admin/api/prod", registry.get("deployments", {}))
        self.assertFalse(self.new_service_log.read_text(encoding="utf-8").find('"put"') >= 0)

    def test_new_service_create_only_persists_draft_and_keeps_remote_write_pending(self) -> None:
        self.register_new_service_objects()
        result = self.run_new_service_deployx(
            "service",
            "create",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--unit",
            "new-api.service",
            "--path",
            "config=/etc/new-api",
            "--path",
            "data=/var/lib/new-api",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "drafted")
        self.assertEqual(payload["plan_status"], "ready")
        self.assertFalse(payload["remote_write"])
        self.assertEqual(payload["draft"]["status"], "draft")
        self.assertEqual(payload["draft"]["management_status"], "draft")
        stages = {item["name"]: item for item in payload["draft"]["creation_stages"]}
        self.assertEqual(stages["draft"]["status"], "completed")
        self.assertEqual(stages["preflight"]["status"], "ready")
        self.assertEqual(stages["install"]["status"], "not_started")

        registry = tomllib.loads(
            (self.root / "config" / "agent-ops" / "ops.toml").read_text(encoding="utf-8")
        )
        draft = registry["deployments"]["mall-admin/api/prod"]
        self.assertEqual(draft["unit"], "new-api.service")
        self.assertEqual(draft["paths"][0]["kind"], "release")
        self.assertEqual(draft["ports"], [])
        self.assertNotIn('"put"', self.new_service_log.read_text(encoding="utf-8"))

    def test_doctor_reports_missing_then_verified_backup_coverage(self) -> None:
        self.register_new_service_objects()
        created = self.run_new_service_deployx(
            "service",
            "create",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--unit",
            "new-api.service",
            "--path",
            "config=/etc/new-api",
            "--path",
            "data=/var/lib/new-api",
            "--db-source",
            "prod-db",
            "--json",
        )
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)

        missing = self.run_deployx("doctor", "mall-admin", "api", "prod", "--json")
        self.assertNotEqual(missing.returncode, 0)
        missing_payload = json.loads(missing.stdout)
        self.assertEqual(missing_payload["status"], "missing")
        self.assertEqual(missing_payload["backup_coverage"]["status"], "missing")
        self.assertTrue(
            any(
                item["name"] == "database:prod-db" and item["status"] == "missing"
                for item in missing_payload["backup_coverage"]["checks"]
            )
        )
        self.assertTrue(
            any(
                item["name"] == "file:data:/var/lib/new-api" and item["status"] == "missing"
                for item in missing_payload["backup_coverage"]["checks"]
            )
        )

        repository = self.run_backupx(
            "repository",
            "register",
            "local-prod",
            "--path",
            str(self.backup_repository),
            "--default",
            "--json",
        )
        self.assertEqual(repository.returncode, 0, repository.stderr + repository.stdout)
        registrations = (
            (
                "asset", "register", "mall-db", "--kind", "db",
                "--project", "mall-admin", "--service", "api", "--environment", "prod",
                "--repository", "local-prod", "--source", "prod-db", "--json",
            ),
            (
                "asset", "register", "mall-data", "--kind", "file",
                "--project", "mall-admin", "--service", "api", "--environment", "prod",
                "--repository", "local-prod", "--alias", "prod-api",
                "--remote-path", "/var/lib/new-api", "--json",
            ),
            (
                "asset", "register", "mall-config", "--kind", "file",
                "--project", "mall-admin", "--service", "api", "--environment", "prod",
                "--repository", "local-prod", "--alias", "prod-api",
                "--remote-path", "/etc/new-api", "--json",
            ),
        )
        for args in registrations:
            registered = self.run_backupx(*args)
            self.assertEqual(registered.returncode, 0, registered.stderr + registered.stdout)

        self.backup_repository.mkdir(parents=True, exist_ok=True)
        for asset_id, kind in (("mall-db", "db"), ("mall-data", "file"), ("mall-config", "file")):
            manifest = {
                "schema_version": 1,
                "backup_id": f"{asset_id}-latest",
                "kind": kind,
                "asset_id": asset_id,
                "created_at": "2026-08-23T01:00:00Z",
                "status": "verified",
                "sha256": "0" * 64,
            }
            (self.backup_repository / f"{manifest['backup_id']}.manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

        healthy = self.run_deployx("doctor", "mall-admin", "api", "prod", "--json")
        self.assertEqual(healthy.returncode, 0, healthy.stderr + healthy.stdout)
        healthy_payload = json.loads(healthy.stdout)
        self.assertEqual(healthy_payload["status"], "healthy")
        self.assertEqual(healthy_payload["backup_coverage"]["status"], "ok")
        self.assertTrue(
            all(item["status"] == "pass" for item in healthy_payload["backup_coverage"]["checks"])
        )

    def test_new_service_plan_blocks_existing_managed_directory(self) -> None:
        self.register_new_service_objects()
        result = self.run_new_service_deployx(
            "service",
            "plan",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--artifact",
            str(self.artifact),
            "--release-root",
            "/srv/apps",
            "--unit",
            "new-api.service",
            "--json",
            mode="occupied",
        )
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "blocked")
        target = next(item for item in payload["preconditions"] if item["name"] == "remote_target")
        self.assertEqual(target["status"], "fail")

    def test_existing_service_inspect_adopt_and_legacy_migration_are_read_only_until_confirmed(self) -> None:
        self.register_new_service_objects()
        inspect = self.run_observed_deployx(
            "service",
            "inspect",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
        )
        self.assertEqual(inspect.returncode, 0, inspect.stderr + inspect.stdout)
        inspect_payload = json.loads(inspect.stdout)
        self.assertEqual(inspect_payload["status"], "observed")
        self.assertEqual(inspect_payload["registration_status"], "unregistered")
        self.assertEqual(inspect_payload["legacy_mode"], "legacy_single_directory")
        self.assertEqual(inspect_payload["drift"]["status"], "not_registered")
        self.assertFalse(inspect_payload["remote_write"])

        migration = self.run_observed_deployx(
            "service",
            "migrate-plan",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
        )
        self.assertEqual(migration.returncode, 0, migration.stderr + migration.stdout)
        migration_payload = json.loads(migration.stdout)
        self.assertEqual(migration_payload["status"], "ready")
        self.assertEqual(migration_payload["migration"]["source_layout"], "legacy_single_directory")
        self.assertFalse(migration_payload["migration"]["remote_write"])

        blocked = self.run_observed_deployx(
            "service",
            "adopt",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual(json.loads(blocked.stdout)["status"], "blocked")
        blocked_registry = tomllib.loads(
            (self.root / "config" / "agent-ops" / "ops.toml").read_text(encoding="utf-8")
        )
        self.assertNotIn("mall-admin/api/prod", blocked_registry.get("deployments", {}))

        adopted = self.run_observed_deployx(
            "service",
            "adopt",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--confirm",
            "--json",
        )
        self.assertEqual(adopted.returncode, 0, adopted.stderr + adopted.stdout)
        adopted_payload = json.loads(adopted.stdout)
        self.assertEqual(adopted_payload["status"], "adopted")
        self.assertFalse(adopted_payload["remote_write"])
        self.assertEqual(adopted_payload["management_status"], "adopted")
        deployment = adopted_payload["deployment"]
        self.assertEqual(deployment["legacy_mode"], "legacy_single_directory")
        self.assertEqual(deployment["systemd_baseline"]["user"], "api")
        self.assertIn({"kind": "release", "path": "/srv/apps/api", "ownership": "external", "observed_kind": "release"}, deployment["paths"])

    def test_service_adopt_persists_observed_float_fields(self) -> None:
        self.register_new_service_objects()
        adopted = self.run_observed_deployx(
            "service",
            "adopt",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--confirm",
            "--json",
            mode="float",
        )
        self.assertEqual(adopted.returncode, 0, adopted.stderr + adopted.stdout)
        registry = tomllib.loads(
            (self.root / "config" / "agent-ops" / "ops.toml").read_text(encoding="utf-8")
        )
        process = registry["deployments"]["mall-admin/api/prod"]["observed"]["service_inspect"]["process"]
        self.assertEqual(process["cpu"], 0.25)
        self.assertEqual(process["memory"], 1.5)

    def test_existing_service_drift_is_reported_and_versioned_layout_is_not_applicable(self) -> None:
        self.register_new_service_objects()
        adopted = self.run_observed_deployx(
            "service",
            "adopt",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--confirm",
            "--json",
        )
        self.assertEqual(adopted.returncode, 0, adopted.stderr + adopted.stdout)

        in_sync = self.run_observed_deployx(
            "service",
            "inspect",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
        )
        self.assertEqual(in_sync.returncode, 0, in_sync.stderr + in_sync.stdout)
        self.assertEqual(json.loads(in_sync.stdout)["drift"]["status"], "in_sync")

        drift = self.run_observed_deployx(
            "service",
            "inspect",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
            mode="drift",
        )
        self.assertNotEqual(drift.returncode, 0)
        drift_payload = json.loads(drift.stdout)
        self.assertEqual(drift_payload["status"], "drifted")
        self.assertEqual(drift_payload["drift"]["checks"]["systemd"]["status"], "drifted")
        self.assertEqual(drift_payload["drift"]["checks"]["paths"]["status"], "drifted")
        self.assertEqual(drift_payload["drift"]["checks"]["ports"]["status"], "drifted")

        versioned = self.run_observed_deployx(
            "service",
            "migrate-plan",
            "mall-admin",
            "api",
            "prod",
            "--ssh-alias",
            "prod-api",
            "--release-root",
            "/srv/apps",
            "--unit",
            "api.service",
            "--json",
            mode="versioned",
        )
        self.assertEqual(versioned.returncode, 0, versioned.stderr + versioned.stdout)
        versioned_payload = json.loads(versioned.stdout)
        self.assertEqual(versioned_payload["status"], "not_applicable")
        self.assertEqual(versioned_payload["legacy_mode"], "versioned")
        self.assertFalse(versioned_payload["remote_write"])


if __name__ == "__main__":
    unittest.main()
