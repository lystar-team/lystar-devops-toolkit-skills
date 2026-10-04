from __future__ import annotations

import json
import os
import sys
import tempfile
import textwrap
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = REPO_ROOT / "runtime" / "agent-ops" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import codeup_devops  # noqa: E402


FAKE_ALIYUN = r'''#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if args[:2] == ["devops", "version"]:
    print("aliyun-cli-devops test")
elif "base-list-organizations" in args:
    print(json.dumps([{"id": "org-1", "name": "测试组织"}]))
elif "flow-list-pipelines" in args:
    print(json.dumps([{"pipelineId": 10, "pipelineName": "测试流水线"}]))
elif "flow-get-pipeline" in args:
    print(json.dumps({"id": 10, "name": "测试流水线", "pipelineConfig": {"flow": "stages: {}\n"}}))
elif "flow-create-pipeline-run" in args:
    print(json.dumps({"pipelineRunId": "run-1"}))
elif "flow-get-pipeline-run" in args:
    print(json.dumps({"pipelineRunId": "run-1", "status": "SUCCESS"}))
elif "flow-create-pipeline" in args:
    print(json.dumps({"pipelineId": 11, "name": "新流水线"}))
elif "flow-update-pipeline" in args:
    print(json.dumps({"pipelineId": 10, "name": "测试流水线"}))
elif "codeup-create-repository" in args:
    print(json.dumps({"id": 20, "name": "新代码库", "path": "new-repo", "httpUrlToRepo": "https://codeup.aliyun.com/org/new-repo.git"}))
elif "codeup-list-namespaces" in args:
    print(json.dumps([{"id": 30, "name": "代码组", "path": "demo-platform", "parentId": 9}]))
elif "codeup-create-group" in args:
    print(json.dumps({"id": 31, "name": "新代码组", "path": "new-platform", "parentId": 9, "visibility": "private"}))
elif "codeup-list-branches" in args:
    print(json.dumps({"items": [{"name": "develop"}, {"name": "master"}]}))
elif "codeup-update-repository" in args:
    print(json.dumps({"id": 20, "defaultBranch": "develop"}))
elif "codeup-delete-branch" in args:
    print(json.dumps({"deleted": True}))
elif "codeup-list-group-repositories" in args:
    print(json.dumps([{"id": 20, "name": "已有代码库", "path": "existing-repo", "httpUrlToRepo": "https://codeup.aliyun.com/org/existing-repo.git"}]))
else:
    print(json.dumps({"error": "unexpected command", "args": args}))
'''


class CodeupDevopsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old_env = dict(os.environ)
        os.environ["LYSTAR_HOME"] = str(self.root / ".lystar")
        os.environ.pop("LYSTAR_CONFIG_HOME", None)
        os.environ.pop("LYSTAR_STATE_HOME", None)
        os.environ["ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN"] = "test-token"
        fake = self.root / "aliyun"
        fake.write_text(textwrap.dedent(FAKE_ALIYUN), encoding="utf-8")
        fake.chmod(0o755)
        os.environ["LYSTAR_CODEUP_ALIYUN_BIN"] = str(fake)

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self.old_env)
        self.temp.cleanup()

    def test_remote_orgs_can_be_saved_and_resolved(self) -> None:
        result = codeup_devops.remote_organizations(save=True)
        self.assertEqual(result[0]["organization_id"], "org-1")
        organization = codeup_devops.resolve_organization("测试组织")
        self.assertEqual(organization["organization_id"], "org-1")
        self.assertEqual(codeup_devops.config_path(), self.root / ".lystar" / "config" / "codeup-devops.toml")

    def test_pipeline_list_and_get_use_org_context(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        listed = codeup_devops.list_pipelines(
            Namespace(org="test", pipeline_name=None, page=1, per_page=30, all=False)
        )
        self.assertEqual(listed["pipelines"][0]["pipeline_id"], 10)
        detail = codeup_devops.get_pipeline(
            Namespace(org="test", pipeline_id="10", yaml_out=None)
        )
        self.assertEqual(codeup_devops.extract_yaml(detail["pipeline"]), "stages: {}\n")

    def test_run_params_merge_branch_tag_and_env(self) -> None:
        params = codeup_devops.parse_params(
            Namespace(
                params='{"envs":{"OLD":"1"}}',
                params_file=None,
                branch="develop",
                tag=None,
                repo="https://example.test/repo.git",
                env=["NEW=2"],
                comment="test",
            )
        )
        self.assertEqual(params["runningBranchs"]["https://example.test/repo.git"], "develop")
        self.assertEqual(params["envs"], {"OLD": "1", "NEW": "2"})
        self.assertEqual(params["comment"], "test")

    def test_create_dry_run_does_not_require_remote_mutation(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        yaml_path = self.root / "pipeline.yaml"
        yaml_path.write_text("stages: {}\n", encoding="utf-8")
        result = codeup_devops.create_pipeline(
            Namespace(
                org="test",
                name="新流水线",
                yaml=str(yaml_path),
                run_after=False,
                params=None,
                params_file=None,
                branch=None,
                tag=None,
                repo=None,
                env=None,
                comment=None,
                watch=False,
                interval=1,
                timeout=1,
                dry_run=True,
            )
        )
        self.assertEqual(result["status"], "dry_run")

    def test_repository_create_requires_confirmation_and_uses_official_cli(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        args = Namespace(
            org="test",
            namespace_id="123",
            name="新代码库",
            path="new-repo",
            description="测试",
            visibility="private",
            read_me_type="EMPTY",
            create_parent_path=False,
            confirm=False,
            dry_run=False,
        )
        self.assertEqual(codeup_devops.create_repository(args)["status"], "dry_run")
        args.confirm = True
        result = codeup_devops.create_repository(args)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["repository"]["repository_path"], "new-repo")

    def test_namespace_list_and_group_create_use_official_cli(self) -> None:
        organization = {"key": "test", "organization_id": "org-1", "edition": "central"}
        namespaces = codeup_devops.list_namespaces(organization, search="demo-platform", parent_id=9)
        self.assertEqual(namespaces[0]["namespace_id"], 30)
        self.assertEqual(namespaces[0]["namespace_path"], "demo-platform")
        self.assertEqual(namespaces[0]["parent_id"], 9)
        with self.assertRaisesRegex(codeup_devops.CodeupError, "需要 confirm"):
            codeup_devops.create_group(organization, name="未确认", path="unconfirmed-platform")
        created = codeup_devops.create_group(
            organization,
            name="新代码组",
            path="new-platform",
            parent_id=9,
            visibility="private",
            confirm=True,
        )
        self.assertEqual(created["namespace_id"], 31)
        self.assertEqual(created["namespace_path"], "new-platform")

    def test_repository_branch_policy_uses_official_cli(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        update_args = Namespace(
            org="test",
            repository_id="20",
            branch="develop",
            confirm=False,
            dry_run=False,
        )
        self.assertEqual(codeup_devops.update_repository_default_branch(update_args)["status"], "dry_run")
        update_args.confirm = True
        self.assertEqual(codeup_devops.update_repository_default_branch(update_args)["status"], "updated")

        delete_args = Namespace(
            org="test",
            repository_id="20",
            branch="master",
            confirm=True,
            dry_run=False,
        )
        policy_preview = codeup_devops.repository_branch_policy(
            {"key": "test", "organization_id": "org-1", "edition": "central"},
            "20",
            default_branch="develop",
            confirm=False,
        )
        self.assertEqual(policy_preview["status"], "dry_run")
        self.assertEqual(policy_preview["would_delete"], ["master"])
        policy_result = codeup_devops.repository_branch_policy(
            {"key": "test", "organization_id": "org-1", "edition": "central"},
            "20",
            default_branch="develop",
            confirm=True,
        )
        self.assertEqual(policy_result["status"], "success")
        self.assertEqual(policy_result["deleted"], ["master"])

        env = codeup_devops.command_environment(
            {"edition": "region", "api_base_url": "https://region.example"}
        )
        self.assertEqual(env["ALIBABA_CLOUD_YUNXIAO_API_BASE_URL"], "https://region.example")
        self.assertNotIn("ALIBABA_CLOUD_YUNXIAO_ORGANIZATION_ID", env)

    def test_persisted_token_is_used_without_environment_override(self) -> None:
        config = codeup_devops.load_config()
        config["credentials"] = {"access_token": "stored-token", "clone_username": "stored-user"}
        codeup_devops.save_config(config)
        os.environ.pop("ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN", None)
        self.assertEqual(codeup_devops.access_token(), "stored-token")
        self.assertEqual(codeup_devops.clone_username(), "stored-user")
        self.assertEqual(codeup_devops.command_environment()["ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN"], "stored-token")
        os.environ["ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN"] = "override-token"
        self.assertEqual(codeup_devops.access_token(), "stored-token")
        self.assertEqual(codeup_devops.credential_source(), "config")

    def test_save_credentials_uses_official_user_and_clone_username_queries(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        with patch.object(
            codeup_devops,
            "cli_call",
            side_effect=[{"userId": "user-1"}, {"username": "clone-user"}],
        ) as cli:
            result = codeup_devops.save_credentials(
                Namespace(org="test", token="stored-token", user_id=None, username=None)
            )
        self.assertEqual(result["status"], "configured")
        self.assertEqual(result["clone_username_configured"], True)
        self.assertEqual(cli.call_count, 2)
        saved = codeup_devops.load_config()["credentials"]
        self.assertEqual(saved["user_id"], "user-1")
        self.assertEqual(saved["clone_username"], "clone-user")
        self.assertEqual(codeup_devops.config_path().stat().st_mode & 0o777, 0o600)

    def test_repo_url_normalization_and_source_extraction(self) -> None:
        self.assertEqual(
            codeup_devops.normalize_repo_url(
                "https://user:token@CodeUp.Aliyun.com/org/repo.git/"
            ),
            "codeup.aliyun.com/org/repo",
        )
        self.assertEqual(
            codeup_devops.normalize_repo_url("git@codeup.aliyun.com:org/repo.git"),
            "codeup.aliyun.com/org/repo",
        )
        sources = codeup_devops.pipeline_sources(
            "sources:\n  repo_0:\n    endpoint: https://codeup.aliyun.com/org/repo.git\n    branch: develop\n"
        )
        self.assertEqual(sources[0]["repository_key"], "codeup.aliyun.com/org/repo")
        self.assertEqual(sources[0]["branch"], "develop")

    def test_find_pipeline_by_repo_returns_unique_match_without_yaml_or_credentials(self) -> None:
        organization = {"key": "test", "name": "测试组织", "edition": "central", "organization_id": "org-1"}
        listed = {"pipelines": [{"pipeline_id": 10, "pipeline_name": "API"}]}
        detail = {
            "pipeline": {
                "id": 10,
                "name": "API",
                "type": "PIPELINEASCODE",
                "pipelineConfig": {
                    "flow": (
                        "sources:\n  repo_0:\n    endpoint: https://codeup.aliyun.com/org/repo.git\n"
                        "    branch: develop\nstages:\n  stage_0:\n    jobs:\n      job_0:\n        component: VMDeploy\n"
                    )
                },
            }
        }
        with patch.object(codeup_devops, "search_organizations", return_value=[organization]), \
            patch.object(codeup_devops, "list_pipelines", return_value=listed), \
            patch.object(codeup_devops, "get_pipeline", return_value=detail):
            result = codeup_devops.find_pipelines(
                Namespace(
                    org="test",
                    repo_url="https://codeup.aliyun.com/org/repo",
                    branch="develop",
                    pipeline_name=None,
                    per_page=30,
                    save=True,
                )
            )
        self.assertEqual(result["status"], "located")
        self.assertEqual(result["matches"][0]["pipeline_id"], "10")
        self.assertTrue(result["matches"][0]["has_deployment"])
        self.assertNotIn("flow", result["matches"][0])
        self.assertEqual(result["mapping"]["pipeline_id"], "10")
        self.assertEqual(
            codeup_devops.resolve_pipeline_mapping(
                organization,
                "https://codeup.aliyun.com/org/repo.git",
                "develop",
            )["pipeline_id"],
            "10",
        )

    def test_pipeline_registry_round_trip_is_centralized(self) -> None:
        organization = {"key": "test", "name": "测试组织", "edition": "central", "organization_id": "org-1"}
        mapping = codeup_devops.save_pipeline_mapping(
            organization,
            {
                "pipeline_id": "10",
                "pipeline_name": "API",
                "pipeline_type": "PIPELINEASCODE",
                "source": {"endpoint": "https://user:token@codeup.aliyun.com/org/repo.git", "branch": "develop"},
                "has_deployment": True,
                "yaml_sha256": "abc",
            },
        )
        self.assertEqual(mapping["repo_url"], "https://codeup.aliyun.com/org/repo.git")
        self.assertNotIn("token", mapping["repo_url"])
        self.assertEqual(codeup_devops.load_config()["pipelines"][mapping["registry_key"]]["pipeline_id"], "10")
        listed = codeup_devops.list_pipeline_registry(Namespace(org=None))
        self.assertEqual(listed["status"], "ok")
        self.assertEqual(listed["pipelines"][0]["pipeline_id"], "10")

    def test_run_without_pipeline_id_requires_repo_and_branch(self) -> None:
        codeup_devops.register_organization(
            Namespace(
                key="test",
                name="测试组织",
                edition="central",
                organization_id="org-1",
                api_base_url=None,
                organization_alias=None,
                default=True,
            )
        )
        with self.assertRaisesRegex(codeup_devops.CodeupError, "--repo 和 --branch"):
            codeup_devops.resolve_pipeline_id_for_run(
                Namespace(pipeline_id=None, repo=None, branch=None),
                codeup_devops.resolve_organization("test"),
            )


if __name__ == "__main__":
    unittest.main()
