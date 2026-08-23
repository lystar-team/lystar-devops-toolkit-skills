from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
BIN_DIR = Path(os.environ.get("AGENT_OPS_BIN_DIR", REPO_ROOT / "bin"))
sys.path.insert(0, str(REPO_ROOT / "runtime" / "agent-ops" / "scripts"))

import registry_store


FAKE_DBX = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

MODE = os.environ.get("BACKUPX_FAKE_MODE", "")
LOG = os.environ.get("BACKUPX_FAKE_LOG", "")

if LOG:
    Path(LOG).write_text(" ".join(sys.argv[1:]) + "\n", encoding="utf-8", errors="replace")

command = sys.argv[1] if len(sys.argv) > 1 else ""
if command == "export":
    if MODE == "db_export_fail":
        print(json.dumps({"error": "fake export failed"}))
        raise SystemExit(1)
    output = Path(sys.argv[2])
    output.write_text("-- fake SQL dump\nSELECT 1;\n", encoding="utf-8")
    print(json.dumps({
        "out_file": str(output),
        "bytes": output.stat().st_size,
        "datasource": {
            "profile": "db_profile",
            "db_type": "postgresql",
            "database": "app",
            "version": "16.2",
        },
    }))
elif command == "import":
    if MODE == "db_import_fail":
        print(json.dumps({
            "error": "fake import failed",
            "transaction": {"mode": "commit", "state": "failed", "rolled_back": True},
        }))
        raise SystemExit(1)
    print(json.dumps({
        "statement_count": 2,
        "affected_rows": 1,
        "transaction": {"mode": "commit", "state": "committed", "committed": True},
    }))
elif command == "query":
    if MODE == "db_query_fail":
        print(json.dumps({"error": "fake query failed"}))
        raise SystemExit(1)
    print(json.dumps({"columns": ["count"], "rows": [[1]], "has_more": False}))
else:
    print(json.dumps({"error": "unknown fake dbx command"}))
    raise SystemExit(1)
'''


FAKE_SSHX = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import tarfile
import tempfile
from pathlib import Path

MODE = os.environ.get("BACKUPX_FAKE_MODE", "")
command = sys.argv[1] if len(sys.argv) > 1 else ""

if MODE == "ssh_unavailable":
    print(json.dumps({"error": "fake ssh unavailable"}))
    raise SystemExit(1)

if command == "run":
    print(json.dumps({"job_id": "job-123", "pid": "42"}))
elif command == "wait":
    if MODE == "remote_job_fail":
        print(json.dumps({"job_id": "job-123", "status": {"state": "finished", "exit_code": 1}, "stderr": "tar failed"}))
        raise SystemExit(1)
    print(json.dumps({"job_id": "job-123", "status": {"state": "finished", "exit_code": 0}, "stdout": "created"}))
elif command == "get":
    if MODE == "download_fail":
        print(json.dumps({"error": "fake download failed"}))
        raise SystemExit(1)
    local = Path(sys.argv[4])
    local.parent.mkdir(parents=True, exist_ok=True)
    if MODE == "invalid_archive":
        local.write_bytes(b"not a tar archive")
        print(json.dumps({"files": 1, "bytes": local.stat().st_size, "local": str(local)}))
        raise SystemExit(0)
    with tempfile.TemporaryDirectory() as temp:
        source = Path(temp) / "inside.txt"
        source.write_text("backup content\n", encoding="utf-8")
        with tarfile.open(local, "w:gz") as archive:
            archive.add(source, arcname="inside.txt")
    print(json.dumps({"files": 1, "bytes": local.stat().st_size, "local": str(local), "sha256": hashlib.sha256(local.read_bytes()).hexdigest()}))
elif command == "put":
    if MODE == "upload_fail":
        print(json.dumps({"error": "fake upload failed"}))
        raise SystemExit(1)
    print(json.dumps({"files": 1, "bytes": Path(sys.argv[3]).stat().st_size, "remote": sys.argv[4]}))
elif command == "exec":
    remote_command = sys.argv[-1]
    if MODE == "restore_fail" and "tar -xzf" in remote_command:
        print(json.dumps({"stdout": "", "stderr": "restore failed", "exit_code": 1}))
    else:
        print(json.dumps({"stdout": "", "stderr": "", "exit_code": 0}))
else:
    print(json.dumps({"error": "unknown fake sshx command"}))
    raise SystemExit(1)
'''


FAKE_HOSTX = r'''#!/usr/bin/env python3
import json
import sys

if len(sys.argv) > 1 and sys.argv[1] == "health":
    print(json.dumps({"schema_version": 1, "kind": "health", "status": "pass", "checks": [{"name": "facts", "status": "pass"}]}))
else:
    print(json.dumps({"error": "unknown fake hostx command"}))
    raise SystemExit(1)
'''


class BackupOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repository = self.root / "backups"
        self.fake_dbx = self.root / "fake-dbx"
        self.fake_sshx = self.root / "fake-sshx"
        self.fake_hostx = self.root / "fake-hostx"
        self.log = self.root / "calls.log"
        for path, content in (
            (self.fake_dbx, FAKE_DBX),
            (self.fake_sshx, FAKE_SSHX),
            (self.fake_hostx, FAKE_HOSTX),
        ):
            path.write_text(content, encoding="utf-8")
            path.chmod(0o755)
        self.env = {
            **os.environ,
            "LYSTAR_SKILL_AUTO_UPDATE": "0",
            "BACKUPX_DBX": str(self.fake_dbx),
            "BACKUPX_SSHX": str(self.fake_sshx),
            "BACKUPX_HOSTX": str(self.fake_hostx),
            "BACKUPX_FAKE_LOG": str(self.log),
            "XDG_DATA_HOME": str(REPO_ROOT / "runtime"),
            "BACKUPX_REGISTRY_FILE": str(self.root / "config" / "agent-ops" / "ops.toml"),
            "BACKUPX_REGISTRY_REVISION_DIR": str(self.root / "state" / "agent-ops" / "ops" / "revisions"),
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_backupx(self, *args: str, mode: str = "") -> subprocess.CompletedProcess[str]:
        env = {**self.env, "BACKUPX_FAKE_MODE": mode}
        return subprocess.run(
            [str(BIN_DIR / "backupx"), *args],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )

    def create_db_backup(self) -> dict:
        result = self.run_backupx(
            "db", "create",
            "--source", "prod",
            "--repository", str(self.repository),
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)["manifest"]

    def register_backup_binding(self) -> None:
        store = registry_store.RegistryStore(
            registry_file=self.root / "config" / "agent-ops" / "ops.toml",
            revision_dir=self.root / "state" / "agent-ops" / "ops" / "revisions",
        )
        store.create("project", "mall-admin", {"name": "商城后台"})
        store.create("service", "api", {"name": "API", "project_id": "mall-admin"})
        store.create("environment", "prod", {"name": "生产"})

    def test_repository_and_asset_registration_drive_default_bound_manifests(self) -> None:
        self.register_backup_binding()
        repository = self.run_backupx(
            "repository",
            "register",
            "local-prod",
            "--path",
            str(self.repository),
            "--default",
            "--json",
        )
        self.assertEqual(repository.returncode, 0, repository.stderr + repository.stdout)
        self.assertTrue(json.loads(repository.stdout)["object"]["default"])

        db_asset = self.run_backupx(
            "asset",
            "register",
            "mall-db",
            "--kind",
            "db",
            "--project-id",
            "mall-admin",
            "--service-id",
            "api",
            "--environment",
            "prod",
            "--repository-id",
            "local-prod",
            "--db-source",
            "prod",
            "--json",
        )
        self.assertEqual(db_asset.returncode, 0, db_asset.stderr + db_asset.stdout)
        self.assertEqual(json.loads(db_asset.stdout)["object"]["repository_id"], "local-prod")

        created = self.run_backupx("db", "create", "--asset-id", "mall-db", "--json")
        self.assertEqual(created.returncode, 0, created.stderr + created.stdout)
        manifest = json.loads(created.stdout)["manifest"]
        self.assertEqual(manifest["asset_id"], "mall-db")
        self.assertEqual(manifest["project_id"], "mall-admin")
        self.assertEqual(manifest["service_id"], "api")
        self.assertEqual(manifest["environment"], "prod")
        self.assertEqual(manifest["repository_id"], "local-prod")
        self.assertTrue((self.repository / manifest["path"]).is_file())

        verified = self.run_backupx("verify", manifest["backup_id"], "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr + verified.stdout)
        self.assertEqual(json.loads(verified.stdout)["status"], "passed")

        file_asset = self.run_backupx(
            "asset",
            "register",
            "mall-files",
            "--kind",
            "file",
            "--project",
            "mall-admin",
            "--service",
            "api",
            "--environment",
            "prod",
            "--repository",
            "local-prod",
            "--alias",
            "prod-api",
            "--remote-path",
            "/srv/web/uploads",
            "--json",
        )
        self.assertEqual(file_asset.returncode, 0, file_asset.stderr + file_asset.stdout)
        file_created = self.run_backupx("file", "create", "--asset", "mall-files", "--json")
        self.assertEqual(file_created.returncode, 0, file_created.stderr + file_created.stdout)
        file_manifest = json.loads(file_created.stdout)["manifest"]
        self.assertEqual(file_manifest["asset_id"], "mall-files")
        self.assertEqual(file_manifest["remote_path"], "/srv/web/uploads")

        listed = self.run_backupx("asset", "list", "--project", "mall-admin", "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr + listed.stdout)
        self.assertEqual(
            {item["id"] for item in json.loads(listed.stdout)["objects"]},
            {"mall-db", "mall-files"},
        )

    def test_read_only_repository_commands_do_not_create_missing_directory(self) -> None:
        missing = self.root / "missing-repository"
        result = self.run_backupx(
            "verify",
            "backup-1",
            "--repository",
            str(missing),
            "--read-only",
            "--json",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())

    def test_database_create_list_inspect_and_verify(self) -> None:
        manifest = self.create_db_backup()
        self.assertEqual(manifest["kind"], "db")
        self.assertEqual(manifest["db_type"], "postgresql")
        self.assertEqual(manifest["version"], "16.2")
        self.assertEqual(manifest["source_profile"], "db_profile")

        listed = self.run_backupx("list", "--repository", str(self.repository), "--json")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["backups"][0]["backup_id"], manifest["backup_id"])

        inspected = self.run_backupx("inspect", manifest["backup_id"], "--repository", str(self.repository), "--json")
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)["manifest"]["sha256"], manifest["sha256"])

        verified = self.run_backupx("verify", manifest["backup_id"], "--repository", str(self.repository), "--json")
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertEqual(json.loads(verified.stdout)["status"], "passed")

        read_only_manifest = json.loads(
            (self.repository / f"{manifest['backup_id']}.manifest.json").read_text(encoding="utf-8")
        )
        read_only = self.run_backupx(
            "verify", manifest["backup_id"], "--repository", str(self.repository), "--read-only", "--json"
        )
        self.assertEqual(read_only.returncode, 0, read_only.stderr)
        self.assertFalse(json.loads(read_only.stdout)["manifest_updated"])
        self.assertEqual(
            json.loads((self.repository / f"{manifest['backup_id']}.manifest.json").read_text(encoding="utf-8")),
            read_only_manifest,
        )

        (self.repository / manifest["path"]).write_text("changed\n", encoding="utf-8")
        invalid = self.run_backupx("verify", manifest["backup_id"], "--repository", str(self.repository), "--json")
        self.assertEqual(invalid.returncode, 1)
        self.assertEqual(json.loads(invalid.stdout)["status"], "failed")

    def test_database_export_failure_does_not_create_manifest(self) -> None:
        result = self.run_backupx(
            "db", "create",
            "--source", "prod",
            "--repository", str(self.repository),
            "--json",
            mode="db_export_fail",
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.repository.glob("*.manifest.json")), [])
        self.assertEqual(list(self.repository.glob("*.sql")), [])

    def test_database_restore_requires_target_and_reports_commit(self) -> None:
        manifest = self.create_db_backup()
        result = self.run_backupx(
            "restore", manifest["backup_id"],
            "--repository", str(self.repository),
            "--source", "staging",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["transaction"]["state"], "committed")
        self.assertEqual(payload["target"]["source"], "staging")
        self.assertIn("--transaction commit", self.log.read_text(encoding="utf-8"))

        verify = self.run_backupx(
            "restore-verify", manifest["backup_id"],
            "--repository", str(self.repository),
            "--source", "staging",
            "--sql", "SELECT COUNT(*) FROM users",
            "--json",
        )
        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["status"], "passed")

        write_sql = self.run_backupx(
            "restore-verify", manifest["backup_id"],
            "--repository", str(self.repository),
            "--source", "staging",
            "--sql", "UPDATE users SET name='bad'",
            "--json",
        )
        self.assertEqual(write_sql.returncode, 1)
        self.assertEqual(json.loads(write_sql.stdout)["checks"][0]["status"], "failed")

    def test_file_create_restore_verify_and_remote_failure(self) -> None:
        created = self.run_backupx(
            "file", "create", "prod", "/srv/uploads",
            "--repository", str(self.repository),
            "--json",
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        manifest = json.loads(created.stdout)["manifest"]
        self.assertEqual(manifest["kind"], "file")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(manifest["remote_job_id"], "job-123")

        restored = self.run_backupx(
            "restore", manifest["backup_id"],
            "--repository", str(self.repository),
            "--alias", "staging",
            "--target-dir", "/srv/uploads",
            "--json",
        )
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(json.loads(restored.stdout)["status"], "passed")

        checked = self.run_backupx(
            "restore-verify", manifest["backup_id"],
            "--repository", str(self.repository),
            "--alias", "staging",
            "--target-dir", "/srv/uploads",
            "--check-path", "inside.txt",
            "--health-check", "facts",
            "--json",
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)["status"], "passed")

        failed = self.run_backupx(
            "file", "create", "prod", "/srv/uploads",
            "--repository", str(self.repository),
            "--json",
            mode="remote_job_fail",
        )
        self.assertEqual(failed.returncode, 1)
        failure_payload = json.loads(failed.stdout)
        self.assertEqual(failure_payload["stage"], "remote_job")
        self.assertEqual(failure_payload["remote_job_id"], "job-123")

        download_failed = self.run_backupx(
            "file", "create", "prod", "/srv/uploads",
            "--repository", str(self.repository),
            "--json",
            mode="download_fail",
        )
        self.assertEqual(download_failed.returncode, 1)
        self.assertEqual(json.loads(download_failed.stdout)["stage"], "download")

        archive_failed = self.run_backupx(
            "file", "create", "prod", "/srv/uploads",
            "--repository", str(self.repository),
            "--json",
            mode="invalid_archive",
        )
        self.assertEqual(archive_failed.returncode, 1)
        self.assertEqual(json.loads(archive_failed.stdout)["stage"], "archive_verify")

    def test_prune_defaults_to_dry_run_and_confirm_removes_candidates(self) -> None:
        first = self.create_db_backup()
        second = self.create_db_backup()
        dry_run = self.run_backupx(
            "prune", "--repository", str(self.repository), "--keep-last", "1", "--json"
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        dry_payload = json.loads(dry_run.stdout)
        self.assertEqual(dry_payload["status"], "dry_run")
        self.assertEqual(len(dry_payload["candidates"]), 1)
        self.assertEqual(len(list(self.repository.glob("*.manifest.json"))), 2)

        confirmed = self.run_backupx(
            "prune", "--repository", str(self.repository), "--keep-last", "1", "--confirm", "--json"
        )
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertEqual(json.loads(confirmed.stdout)["status"], "pruned")
        remaining = list(self.repository.glob("*.manifest.json"))
        self.assertEqual(len(remaining), 1)
        remaining_manifest = json.loads(remaining[0].read_text(encoding="utf-8"))
        self.assertIn(remaining_manifest["backup_id"], {first["backup_id"], second["backup_id"]})

    def test_corrupt_manifest_is_reported_as_partial_list(self) -> None:
        self.repository.mkdir(parents=True)
        (self.repository / "broken.manifest.json").write_text("{broken", encoding="utf-8")
        result = self.run_backupx("list", "--repository", str(self.repository), "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "partial")
        self.assertEqual(len(payload["warnings"]), 1)


if __name__ == "__main__":
    unittest.main()
