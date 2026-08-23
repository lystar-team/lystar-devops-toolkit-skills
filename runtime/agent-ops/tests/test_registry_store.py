from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import config_store
import registry_store


class RegistryStoreTest(unittest.TestCase):
    def make_store(self, temp: str) -> registry_store.RegistryStore:
        root = Path(temp)
        return registry_store.RegistryStore(
            registry_file=root / "config" / "agent-ops" / "ops.toml",
            revision_dir=root / "state" / "agent-ops" / "ops" / "revisions",
        )

    def test_create_read_update_delete_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            created = store.create(
                "project",
                "mall-admin",
                {"name": "商城后台", "aliases": ["mall"], "future_flag": "keep"},
            )
            self.assertEqual(created["id"], "mall-admin")
            self.assertEqual(store.load()["revision"], 1)
            self.assertEqual(store.get("projects", "mall-admin"), created)

            updated = store.update("project", "mall-admin", {"name": "商城管理后台"})
            self.assertEqual(updated["name"], "商城管理后台")
            self.assertEqual(updated["future_flag"], "keep")
            self.assertEqual(store.load()["revision"], 2)

            self.assertTrue(store.delete("project", "mall-admin"))
            self.assertIsNone(store.get("project", "mall-admin"))
            self.assertEqual(store.load()["revision"], 3)
            self.assertFalse(store.delete("project", "mall-admin"))
            self.assertEqual(
                len(list(store.revision_dir.glob("revision-*.toml"))),
                3,
            )

    def test_stable_deployment_identity_and_unassigned_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            deployment_id = registry_store.deployment_identity(
                "mall-admin", "api", registry_store.DEFAULT_ENVIRONMENT
            )
            deployment = store.create(
                "deployments",
                deployment_id,
                {"ssh_alias": "prod-api", "strategy": "tar.gz-systemd"},
            )
            self.assertEqual(deployment["id"], "mall-admin/api/unassigned")
            self.assertEqual(deployment["project_id"], "mall-admin")
            self.assertEqual(deployment["service_id"], "api")
            self.assertEqual(deployment["environment"], "unassigned")
            self.assertEqual(registry_store.legacy_environment(None), "unassigned")
            self.assertEqual(registry_store.legacy_environment(""), "unassigned")
            self.assertEqual(registry_store.legacy_environment("staging"), "staging")

    def test_missing_or_corrupt_current_recovers_latest_valid_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            store.create("project", "first", {"name": "第一版"})
            store.update("project", "first", {"name": "第二版"})

            store.registry_file.write_text("[broken\n", encoding="utf-8")
            loaded = store.load()
            self.assertEqual(loaded["revision"], 2)
            self.assertEqual(loaded["projects"]["first"]["name"], "第二版")
            self.assertEqual(config_store.load_toml(store.registry_file), loaded)

            store.registry_file.unlink()
            loaded_again = store.load()
            self.assertEqual(loaded_again, loaded)
            self.assertTrue(store.registry_file.exists())

    def test_corrupt_latest_revision_falls_back_to_previous_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            store.create("project", "first", {"name": "第一版"})
            store.update("project", "first", {"name": "第二版"})
            latest = sorted(store.revision_dir.glob("revision-*.toml"))[-1]
            latest.write_text("not = [valid", encoding="utf-8")
            store.registry_file.write_text("not = [valid", encoding="utf-8")

            loaded = store.load()
            self.assertEqual(loaded["revision"], 1)
            self.assertEqual(loaded["projects"]["first"]["name"], "第一版")

    def test_unknown_fields_and_legacy_configs_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = self.make_store(temp)
            old_ssh = Path(temp) / "config" / "agent-ops" / "ssh.toml"
            old_db = Path(temp) / "config" / "agent-ops" / "databases.toml"
            ssh_payload = {
                "profiles": {"prod": {"host": "example.com", "user": "root", "password": "保留"}},
                "aliases": {"prod": "prod"},
            }
            db_payload = {
                "profiles": {"main": {"engine": "postgresql", "host": "db", "password": "保留"}},
                "aliases": {"main": "main"},
            }
            config_store.write_toml(old_ssh, ssh_payload)
            config_store.write_toml(old_db, db_payload)
            before = {"ssh": old_ssh.read_bytes(), "db": old_db.read_bytes()}

            document = registry_store.empty_registry()
            document["future"] = {"enabled": True, "owner": "next-round"}
            document["projects"]["legacy"] = {
                "id": "legacy",
                "name": "历史项目",
                "future_object_field": {"keep": ["value"]},
            }
            store.save(document)
            loaded = store.load()
            self.assertEqual(loaded["future"], document["future"])
            self.assertEqual(
                loaded["projects"]["legacy"]["future_object_field"],
                {"keep": ["value"]},
            )
            self.assertEqual(old_ssh.read_bytes(), before["ssh"])
            self.assertEqual(old_db.read_bytes(), before["db"])
            self.assertEqual(config_store.load_toml(old_ssh), ssh_payload)
            self.assertEqual(config_store.load_toml(old_db), db_payload)

            # 返回值是副本，调用方修改它不会静默修改磁盘中的注册表。
            loaded["projects"]["legacy"]["name"] = "本地修改"
            self.assertEqual(store.get("project", "legacy")["name"], "历史项目")

    def test_reference_impact_sync_and_orphan_marking(self) -> None:
        document = registry_store.empty_registry()
        document["projects"]["mall"] = {"id": "mall", "name": "商城"}
        document["services"]["api"] = {
            "id": "api",
            "name": "API",
            "project_id": "mall",
        }
        document["deployments"]["mall/api/prod"] = {
            "id": "mall/api/prod",
            "project_id": "mall",
            "service_id": "api",
            "environment": "prod",
            "ssh_alias": "old-prod",
            "db_sources": ["main-db", "audit-db"],
            "status": "managed",
            "management_status": "managed",
        }
        document["backup_assets"]["mall-files"] = {
            "id": "mall-files",
            "project_id": "mall",
            "service_id": "api",
            "environment": "prod",
            "ssh_alias": "old-prod",
            "status": "active",
        }

        impact = registry_store.reference_impact(document, ssh_aliases={"old-prod"})
        self.assertEqual(impact["counts"], {
            "deployments": 1,
            "services": 1,
            "backup_assets": 1,
            "relations": 0,
        })
        changes = registry_store.synchronize_references(
            document,
            {"old-prod": "new-prod"},
            {"deployments": ("ssh_alias",), "backup_assets": ("ssh_alias",)},
        )
        self.assertEqual(len(changes), 2)
        self.assertEqual(document["deployments"]["mall/api/prod"]["ssh_alias"], "new-prod")
        self.assertEqual(document["backup_assets"]["mall-files"]["ssh_alias"], "new-prod")

        db_impact = registry_store.reference_impact(document, db_sources={"main-db"})
        self.assertEqual([item["id"] for item in db_impact["deployments"]], ["mall/api/prod"])
        marked = registry_store.mark_references_orphaned(
            document,
            impact,
            "SSH profile removed: ssh_profile",
        )
        self.assertEqual(
            marked,
            [
                {"kind": "backup_asset", "id": "mall-files", "status": "orphaned"},
                {"kind": "deployment", "id": "mall/api/prod", "status": "orphaned"},
            ],
        )
        self.assertEqual(document["deployments"]["mall/api/prod"]["management_status"], "orphaned")
        self.assertEqual(document["backup_assets"]["mall-files"]["status"], "orphaned")


if __name__ == "__main__":
    unittest.main()
