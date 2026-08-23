from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("AGENT_OPS_BIN_DIR", REPO_ROOT / "bin"))
SCRIPTS_DIR = REPO_ROOT / "runtime" / "agent-ops" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import registry_store


FAKE_HOSTX = r'''#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("INCIDENTX_FAKE_MODE") == "host_unavailable":
    print(json.dumps({"status": "unavailable", "connection_status": "unavailable", "error": "host down"}))
    raise SystemExit(1)
print(json.dumps({
    "schema_version": 1,
    "kind": "health",
    "target": {"alias": "prod"},
    "started_at": "2026-08-23T10:00:00Z",
    "finished_at": "2026-08-23T10:00:01Z",
    "status": "pass",
    "connection_status": "ok",
    "checks": [{"name": "facts", "status": "pass"}],
}))
'''


FAKE_SSHX = r'''#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("INCIDENTX_FAKE_LOG"):
    with open(os.environ["INCIDENTX_FAKE_LOG"], "a", encoding="utf-8") as handle:
        handle.write("sshx " + " ".join(sys.argv[1:]) + "\n")
if len(sys.argv) > 1 and sys.argv[1] == "status":
    print(json.dumps({"schema_version": 1, "kind": "status", "status": "ok", "profiles": [{"alias": "prod"}]}))
else:
    print(json.dumps({"error": "unexpected sshx action"}))
    raise SystemExit(1)
'''


FAKE_DBX = r'''#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("INCIDENTX_FAKE_LOG"):
    with open(os.environ["INCIDENTX_FAKE_LOG"], "a", encoding="utf-8") as handle:
        handle.write("dbx " + " ".join(sys.argv[1:]) + "\n")
if len(sys.argv) > 2 and sys.argv[1:3] == ["source", "list"]:
    print(json.dumps({"sources": [{
        "profile": "profile-1",
        "aliases": ["prod-db"],
        "db_type": "postgresql",
        "version": "16.2",
        "version_status": "probed",
        "connection_status": "ok",
        "password": "***",
    }]}))
else:
    print(json.dumps({"error": "unexpected dbx action"}))
    raise SystemExit(1)
'''


FAKE_DEPLOYX = r'''#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("INCIDENTX_FAKE_LOG"):
    with open(os.environ["INCIDENTX_FAKE_LOG"], "a", encoding="utf-8") as handle:
        handle.write("deployx " + " ".join(sys.argv[1:]) + "\n")
if len(sys.argv) > 1 and sys.argv[1] == "status":
    print(json.dumps({
        "schema_version": 1,
        "kind": "deployment_status",
        "status": "ok",
        "target": {"alias": "prod"},
        "app": "web",
        "last_result": {
            "started_at": "2026-08-23T09:59:00Z",
            "finished_at": "2026-08-23T09:59:30Z",
            "status": "healthy",
        },
    }))
else:
    print(json.dumps({"error": "unexpected deployx action"}))
    raise SystemExit(1)
'''


FAKE_BACKUPX = r'''#!/usr/bin/env python3
import json
import os
import sys

if os.environ.get("INCIDENTX_FAKE_LOG"):
    with open(os.environ["INCIDENTX_FAKE_LOG"], "a", encoding="utf-8") as handle:
        handle.write("backupx " + " ".join(sys.argv[1:]) + "\n")
command = sys.argv[1] if len(sys.argv) > 1 else ""
if command == "inspect":
    print(json.dumps({
        "schema_version": 1,
        "kind": "backup_inspect",
        "status": "ok",
        "manifest": {
            "backup_id": sys.argv[2],
            "kind": "db",
            "created_at": "2026-08-23T09:00:00Z",
            "path": "backup.sql",
            "bytes": 10,
            "sha256": "0" * 64,
            "status": "verified",
        },
    }))
elif command == "verify" and "--read-only" in sys.argv:
    print(json.dumps({
        "schema_version": 1,
        "kind": "backup_verification",
        "status": "passed",
        "manifest_updated": False,
        "checks": [{"name": "sha256", "status": "pass"}],
    }))
else:
    print(json.dumps({"error": "unexpected or non-read-only backupx action"}))
    raise SystemExit(1)
'''


class IncidentOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.registry_file = self.root / "config" / "agent-ops" / "ops.toml"
        self.registry_revision_dir = self.root / "state" / "agent-ops" / "ops" / "revisions"
        self.fake = {
            "host": self.root / "fake-hostx",
            "ssh": self.root / "fake-sshx",
            "db": self.root / "fake-dbx",
            "deploy": self.root / "fake-deployx",
            "backup": self.root / "fake-backupx",
        }
        contents = {
            "host": FAKE_HOSTX,
            "ssh": FAKE_SSHX,
            "db": FAKE_DBX,
            "deploy": FAKE_DEPLOYX,
            "backup": FAKE_BACKUPX,
        }
        for name, path in self.fake.items():
            path.write_text(contents[name], encoding="utf-8")
            path.chmod(0o755)
        self.log = self.root / "calls.log"
        self.env = {
            **os.environ,
            "LYSTAR_SKILL_AUTO_UPDATE": "0",
            "INCIDENTX_HOSTX": str(self.fake["host"]),
            "INCIDENTX_SSHX": str(self.fake["ssh"]),
            "INCIDENTX_DBX": str(self.fake["db"]),
            "INCIDENTX_DEPLOYX": str(self.fake["deploy"]),
            "INCIDENTX_BACKUPX": str(self.fake["backup"]),
            "INCIDENTX_FAKE_LOG": str(self.log),
            "INCIDENTX_REGISTRY_FILE": str(self.registry_file),
            "INCIDENTX_REGISTRY_REVISION_DIR": str(self.registry_revision_dir),
            "XDG_DATA_HOME": str(REPO_ROOT / "runtime"),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_incidentx(self, *args: str, mode: str = "") -> subprocess.CompletedProcess[str]:
        env = {**self.env, "INCIDENTX_FAKE_MODE": mode}
        return subprocess.run(
            [str(BIN_DIR / "incidentx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )

    def collect_args(self, bundle: Path, *extra: str) -> tuple[str, ...]:
        return (
            "collect",
            "case-20260823",
            "--out",
            str(bundle),
            "--host",
            "prod",
            "--source",
            "prod-db",
            "--app",
            "web",
            "--release-root",
            "/srv/apps",
            "--service",
            "web.service",
            "--log-source",
            "nginx",
            "--since",
            "2 hours ago",
            "--backup-id",
            "backup-1",
            "--repository",
            str(self.root / "backups"),
            *extra,
            "--json",
        )

    def seed_registry(self) -> registry_store.RegistryStore:
        store = registry_store.RegistryStore(
            registry_file=self.registry_file,
            revision_dir=self.registry_revision_dir,
        )
        document = registry_store.empty_registry()
        document["projects"]["mall-admin"] = {
            "id": "mall-admin",
            "name": "商城后台",
            "local_path": "/srv/src/mall-admin",
        }
        document["services"]["api"] = {
            "id": "api",
            "name": "API",
            "project_id": "mall-admin",
            "service_type": "systemd",
        }
        document["environments"]["prod"] = {
            "id": "prod",
            "name": "生产",
        }
        document["deployments"]["mall-admin/api/prod"] = {
            "id": "mall-admin/api/prod",
            "project_id": "mall-admin",
            "service_id": "api",
            "environment": "prod",
            "ssh_alias": "prod",
            "unit": "web.service",
            "status": "managed",
        }
        document["backup_assets"]["mall-db"] = {
            "id": "mall-db",
            "kind": "db",
            "project_id": "mall-admin",
            "service_id": "api",
            "environment": "prod",
            "db_source": "prod-db",
            "repository_id": "local-prod",
            "status": "active",
        }
        document["relations"]["api-db"] = {
            "id": "api-db",
            "source_kind": "deployment",
            "source_id": "mall-admin/api/prod",
            "target_kind": "backup_asset",
            "target_id": "mall-db",
            "relation": "backup",
        }
        store.save(document)
        return store

    def test_collects_all_read_only_evidence_and_builds_offline_bundle(self) -> None:
        result = self.run_incidentx(*self.collect_args(self.bundle, "--require", "host,db,deploy,backup"))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "collected")
        self.assertEqual({item["name"] for item in payload["collectors"]}, {"host", "ssh", "db", "deploy", "backup"})

        manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "collected")
        self.assertEqual(len(manifest["evidence"]), 5)
        events = [json.loads(line) for line in (self.bundle / "timeline.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(events), 5)
        self.assertTrue(any(event["event_time"] == "2026-08-23T09:59:30Z" for event in events))
        self.assertTrue(any(event["event_time"] == "2026-08-23T09:00:00Z" for event in events))
        self.assertTrue(any(event["time_basis"] == "observed_at" and event["event_time"] is None for event in events))

        shown = self.run_incidentx("show", str(self.bundle), "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["evidence_count"], 5)

        timeline = self.run_incidentx("timeline", str(self.bundle), "--json")
        self.assertEqual(timeline.returncode, 0, timeline.stderr)
        self.assertEqual(len(json.loads(timeline.stdout)["events"]), 5)

        verified = self.run_incidentx("verify", str(self.bundle), "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "passed")

        calls = self.log.read_text(encoding="utf-8")
        self.assertIn("backupx verify", calls)
        self.assertIn("--read-only", calls)
        for forbidden in (" apply ", " rollback ", " restore ", " prune "):
            self.assertNotIn(forbidden, calls)

        evidence_path = self.bundle / manifest["evidence"][0]["path"]
        evidence_path.write_text(evidence_path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        invalid = self.run_incidentx("verify", str(self.bundle), "--json")
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stdout)["status"], "failed")

    def test_optional_failure_keeps_bundle_and_required_failure_changes_exit_code(self) -> None:
        optional_bundle = self.root / "optional"
        optional = self.run_incidentx(
            "collect",
            "case-optional",
            "--out",
            str(optional_bundle),
            "--host",
            "prod",
            "--source",
            "prod-db",
            "--json",
            mode="host_unavailable",
        )
        self.assertEqual(optional.returncode, 0, optional.stderr)
        self.assertEqual(json.loads(optional.stdout)["status"], "partial")
        self.assertTrue((optional_bundle / "manifest.json").is_file())

        required_bundle = self.root / "required"
        required = self.run_incidentx(
            "collect",
            "case-required",
            "--out",
            str(required_bundle),
            "--host",
            "prod",
            "--source",
            "prod-db",
            "--require",
            "host",
            "--json",
            mode="host_unavailable",
        )
        self.assertEqual(required.returncode, 1)
        self.assertEqual(json.loads(required.stdout)["status"], "failed")
        self.assertTrue((required_bundle / "manifest.json").is_file())

    def test_collects_registry_objects_revision_and_relations_read_only(self) -> None:
        store = self.seed_registry()
        before = self.registry_file.read_bytes()
        revision_files_before = sorted(path.name for path in self.registry_revision_dir.glob("*.toml"))
        result = self.run_incidentx(
            *self.collect_args(
                self.bundle,
                "--project-id",
                "mall-admin",
                "--service-id",
                "api",
                "--deployment-id",
                "mall-admin/api/prod",
                "--backup-asset-id",
                "mall-db",
                "--require",
                "registry",
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "collected")
        self.assertEqual(payload["registry_revision"], store.load()["revision"])
        self.assertIn("registry", {item["name"] for item in payload["collectors"]})

        manifest = json.loads((self.bundle / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["registry_revision"], store.load()["revision"])
        self.assertEqual(manifest["registry_selection"]["project_id"], "mall-admin")
        self.assertEqual(manifest["registry_counts"]["projects"], 1)
        registry_item = next(item for item in manifest["evidence"] if item["component"] == "registry")
        registry_envelope = json.loads(
            (self.bundle / registry_item["path"]).read_text(encoding="utf-8")
        )
        self.assertEqual(registry_envelope["data"]["registry_revision"], store.load()["revision"])
        self.assertEqual(registry_envelope["data"]["counts"]["relations"], 1)
        self.assertEqual(
            registry_envelope["data"]["objects"]["deployments"][0]["id"],
            "mall-admin/api/prod",
        )

        events = [
            json.loads(line)
            for line in (self.bundle / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        registry_event = next(event for event in events if event["source"] == "registry")
        self.assertEqual(registry_event["registry_revision"], store.load()["revision"])
        self.assertIn("revision=1", registry_event["summary"])

        shown = self.run_incidentx("show", str(self.bundle), "--json")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        self.assertEqual(json.loads(shown.stdout)["registry_revision"], store.load()["revision"])
        verified = self.run_incidentx("verify", str(self.bundle), "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "passed")
        self.assertEqual(self.registry_file.read_bytes(), before)
        self.assertEqual(
            sorted(path.name for path in self.registry_revision_dir.glob("*.toml")),
            revision_files_before,
        )

    def test_corrupt_registry_is_read_without_repairing_current_file(self) -> None:
        store = self.seed_registry()
        revision = store.load()["revision"]
        self.registry_file.write_text("[broken\n", encoding="utf-8")
        before = self.registry_file.read_bytes()
        result = self.run_incidentx(
            *self.collect_args(
                self.bundle,
                "--project-id",
                "mall-admin",
                "--require",
                "registry",
            )
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["registry_revision"], revision)
        self.assertEqual(self.registry_file.read_bytes(), before)

    def test_corrupt_registry_without_revision_is_unavailable_without_creating_current_file(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        self.registry_file.write_text("[broken\n", encoding="utf-8")
        before = self.registry_file.read_bytes()
        result = self.run_incidentx(
            *self.collect_args(
                self.bundle,
                "--require",
                "registry",
            )
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.registry_file.read_bytes(), before)
        payload = json.loads(result.stdout)
        self.assertIn("registry", json.dumps(payload, ensure_ascii=False))

    def test_registry_failure_keeps_optional_bundle_and_honors_required(self) -> None:
        optional_bundle = self.root / "registry-optional"
        optional = self.run_incidentx(
            "collect",
            "case-registry-optional",
            "--out",
            str(optional_bundle),
            "--project-id",
            "missing-project",
            "--json",
        )
        self.assertEqual(optional.returncode, 0, optional.stderr)
        optional_payload = json.loads(optional.stdout)
        self.assertEqual(optional_payload["status"], "partial")
        self.assertEqual(optional_payload["collectors"][0]["status"], "failed")
        optional_verified = self.run_incidentx("verify", str(optional_bundle), "--json")
        self.assertEqual(optional_verified.returncode, 0, optional_verified.stderr)
        self.assertEqual(json.loads(optional_verified.stdout)["status"], "passed")

        required_bundle = self.root / "registry-required"
        required = self.run_incidentx(
            "collect",
            "case-registry-required",
            "--out",
            str(required_bundle),
            "--project-id",
            "missing-project",
            "--require",
            "registry",
            "--json",
        )
        self.assertEqual(required.returncode, 1)
        self.assertEqual(json.loads(required.stdout)["status"], "failed")
        self.assertTrue((required_bundle / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
