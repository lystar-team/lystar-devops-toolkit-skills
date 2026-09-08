#!/usr/bin/env python3
"""LYStar 云效 Codeup/Flow 操作运行时。

通过官方 Alibaba Cloud CLI 的云效插件执行组织、流水线和运行实例操作，
组织上下文保存在 $HOME/.lystar/config/codeup-devops.toml。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
from pathlib import Path
from typing import Any

import yaml

from config_store import lock_files, load_toml, write_toml
import paths
from result_store import canonical_project_root, save_snapshot


SCHEMA_VERSION = 1
TOOL_NAME = "codeupx"
DEFAULT_PAGE_SIZE = 30
DEFAULT_WATCH_INTERVAL = 5.0
DEFAULT_WATCH_TIMEOUT = 3600.0
TERMINAL_SUCCESS = {"SUCCESS", "SUCCEEDED", "SUCCESSFUL", "PASSED", "PASS"}
TERMINAL_FAILURE = {
    "FAIL",
    "FAILED",
    "FAILURE",
    "CANCELED",
    "CANCELLED",
    "STOPPED",
    "TIMEOUT",
    "ERROR",
}
RUNNING_STATES = {"RUNNING", "WAITING", "QUEUED", "PENDING", "PAUSED", "EXECUTING"}


class CodeupError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def trim(value: Any, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        return text[-limit:]
    return text


def config_path() -> Path:
    return paths.config_home() / "codeup-devops.toml"


def default_config() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "default_organization": "",
        "organizations": {},
        "credentials": {},
        "settings": {
            "poll_interval_seconds": DEFAULT_WATCH_INTERVAL,
            "run_timeout_seconds": DEFAULT_WATCH_TIMEOUT,
        },
    }


def load_config() -> dict[str, Any]:
    raw = load_toml(config_path())
    if not raw:
        return default_config()
    document = default_config()
    document.update({key: value for key, value in raw.items() if key != "organizations"})
    document["organizations"] = dict(raw.get("organizations", {}))
    document["credentials"] = dict(raw.get("credentials", {}))
    document["settings"] = dict(default_config()["settings"])
    document["settings"].update(dict(raw.get("settings", {})))
    if not isinstance(document["organizations"], dict):
        raise CodeupError("codeup-devops.toml 的 organizations 必须是表")
    if not isinstance(document["credentials"], dict):
        raise CodeupError("codeup-devops.toml 的 credentials 必须是表")
    return document


def save_config(document: dict[str, Any]) -> None:
    target = config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with lock_files(target):
        write_toml(target, document)


def access_token() -> str:
    """Return the persisted Yunxiao token."""

    credentials = load_config().get("credentials", {})
    return str(
        credentials.get("access_token")
        or credentials.get("yunxiao_access_token")
        or ""
    ).strip()


def clone_username() -> str:
    credentials = load_config().get("credentials", {})
    return str(
        credentials.get("clone_username")
        or credentials.get("https_clone_username")
        or ""
    ).strip()


def credential_source() -> str:
    return "config" if access_token() else "missing"


def slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return text or "organization"


def organization_key(name: Any, organization_id: Any) -> str:
    key = slugify(str(name or organization_id or "organization"))
    if key == "organization":
        key = f"org-{str(organization_id or 'unknown')[:12]}"
    return key


def normalize_edition(value: str | None, organization: dict[str, Any]) -> str:
    edition = str(value or organization.get("edition") or "").strip().lower()
    if edition in {"center", "central", "标准版", "中心版"}:
        return "central"
    if edition in {"region", "专属版", "地域版", "region版"}:
        return "region"
    if organization.get("api_base_url"):
        return "region"
    return "central"


def resolve_organization(value: str | None, *, required: bool = True) -> dict[str, Any] | None:
    config = load_config()
    organizations = config.get("organizations", {})
    key = str(value or "").strip()
    if not key:
        key = str(config.get("default_organization", "")).strip()
    if not key and len(organizations) == 1:
        key = next(iter(organizations))
    if not key:
        if required:
            raise CodeupError(
                f"未指定组织。请使用 --org <key>，或先执行 codeupx org register；配置文件：{config_path()}"
            )
        return None

    if key in organizations:
        item = dict(organizations[key])
        item["key"] = key
    else:
        matches = []
        for item_key, raw in organizations.items():
            item = dict(raw)
            if key in {
                str(item_key),
                str(item.get("name", "")),
                str(item.get("organization_id", "")),
                str(item.get("organization_alias", "")),
            }:
                item["key"] = item_key
                matches.append(item)
        if len(matches) == 1:
            item = matches[0]
        elif len(matches) > 1:
            raise CodeupError(f"组织标识不唯一：{value}", {"matches": matches})
        else:
            if required:
                raise CodeupError(f"本地未登记组织：{value}；配置文件：{config_path()}")
            return None

    item["edition"] = normalize_edition(item.get("edition"), item)
    if item["edition"] == "central" and not str(item.get("organization_id", "")).strip():
        raise CodeupError(f"中心版组织缺少 organization_id：{item.get('key', value)}")
    if item["edition"] == "region" and not str(item.get("api_base_url", "")).strip():
        raise CodeupError(f"Region 版组织缺少 api_base_url：{item.get('key', value)}")
    return item


def resolve_aliyun_bin() -> str:
    config = load_config()
    settings = config.get("settings", {})
    candidates = [
        os.environ.get("LYSTAR_CODEUP_ALIYUN_BIN", "").strip(),
        os.environ.get("LYSTAR_ALIYUN_BIN", "").strip(),
        str(settings.get("aliyun_bin", "")).strip(),
        str(paths.bin_home() / "aliyun"),
        shutil.which("aliyun") or "",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).expanduser().resolve())
    raise CodeupError(
        "未找到 aliyun CLI。请安装阿里云 CLI 和 aliyun-cli-devops 插件，"
        f"或设置 LYSTAR_CODEUP_ALIYUN_BIN；当前检查路径：{paths.bin_home() / 'aliyun'}"
    )


def command_environment(organization: dict[str, Any] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN", None)
    env.pop("ALIBABA_CLOUD_YUNXIAO_CLONE_USERNAME", None)
    token = access_token()
    if token:
        env["ALIBABA_CLOUD_YUNXIAO_ACCESS_TOKEN"] = token
    env.pop("ALIBABA_CLOUD_YUNXIAO_ORGANIZATION_ID", None)
    env.pop("ALIBABA_CLOUD_YUNXIAO_API_BASE_URL", None)
    if organization is not None:
        if organization["edition"] == "central":
            env["ALIBABA_CLOUD_YUNXIAO_ORGANIZATION_ID"] = str(organization["organization_id"])
        else:
            env["ALIBABA_CLOUD_YUNXIAO_API_BASE_URL"] = str(organization["api_base_url"])
    return env


def cli_call(
    command: list[str],
    *,
    organization: dict[str, Any] | None = None,
    dry_run: bool = False,
    timeout: float = 120.0,
) -> Any:
    binary = resolve_aliyun_bin()
    args = [binary, *command]
    if dry_run:
        args.append("--cli-dry-run")
    try:
        completed = subprocess.run(
            args,
            env=command_environment(organization),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodeupError(
            "aliyun CLI 调用超时",
            {"command": command, "timeout": timeout, "stdout": trim(exc.stdout), "stderr": trim(exc.stderr)},
        ) from exc
    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        raise CodeupError(
            "aliyun CLI 调用失败",
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": trim(stdout),
                "stderr": trim(stderr),
            },
        )
    if not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return stdout


def normalize_organization(item: Any) -> dict[str, Any]:
    raw = dict(item) if isinstance(item, dict) else {"value": item}
    organization_id = raw.get("organizationId") or raw.get("id") or raw.get("organization_id")
    name = raw.get("organizationName") or raw.get("name") or ""
    return {
        "organization_id": organization_id,
        "name": name,
        "organization_alias": raw.get("organizationAlias") or raw.get("alias") or "",
        "organization_role": raw.get("organizationRole") or raw.get("role") or "",
        "edition": "central",
        "raw": raw,
    }


def remote_organizations(*, save: bool = False) -> list[dict[str, Any]]:
    data = cli_call(["devops", "base-list-organizations"])
    if not isinstance(data, list):
        raise CodeupError("组织列表返回格式不是数组", {"response": data})
    organizations = [normalize_organization(item) for item in data]
    if save:
        config = load_config()
        table = dict(config.get("organizations", {}))
        for item in organizations:
            key_base = organization_key(item.get("name"), item.get("organization_id"))
            key = key_base
            suffix = 2
            while key in table and str(table[key].get("organization_id", "")) != str(item.get("organization_id", "")):
                key = f"{key_base}-{suffix}"
                suffix += 1
            table[key] = {
                "name": item.get("name", ""),
                "edition": "central",
                "organization_id": item.get("organization_id", ""),
                "organization_alias": item.get("organization_alias", ""),
            }
        config["organizations"] = table
        if not config.get("default_organization") and len(table) == 1:
            config["default_organization"] = next(iter(table))
        save_config(config)
    return organizations


def list_organizations(args: argparse.Namespace) -> dict[str, Any]:
    organizations = remote_organizations(save=args.save)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.organization.list",
        "status": "ok",
        "connection_status": "ok",
        "config_path": str(config_path()),
        "saved": bool(args.save),
        "organizations": organizations,
    }


def register_organization(args: argparse.Namespace) -> dict[str, Any]:
    edition = normalize_edition(args.edition, {"api_base_url": args.api_base_url})
    if edition == "central" and not args.organization_id:
        raise CodeupError("中心版组织必须提供 --organization-id")
    if edition == "region" and not args.api_base_url:
        raise CodeupError("Region 版组织必须提供 --api-base-url")
    config = load_config()
    table = dict(config.get("organizations", {}))
    item: dict[str, Any] = {
        "name": args.name or args.key,
        "edition": edition,
    }
    if args.organization_id:
        item["organization_id"] = args.organization_id
    if args.api_base_url:
        item["api_base_url"] = args.api_base_url
    if args.organization_alias:
        item["organization_alias"] = args.organization_alias
    if args.default:
        config["default_organization"] = args.key
    table[args.key] = item
    config["organizations"] = table
    save_config(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.organization",
        "status": "registered",
        "connection_status": "not_checked",
        "config_path": str(config_path()),
        "organization": {"key": args.key, **item},
    }


def show_organization(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.organization",
        "status": "ok",
        "connection_status": "not_checked",
        "config_path": str(config_path()),
        "organization": organization,
    }


def credential_status(args: argparse.Namespace) -> dict[str, Any]:
    config = load_config()
    credentials = config.get("credentials", {})
    token = access_token()
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.auth",
        "status": "configured" if token else "missing",
        "connection_status": "configured" if token else "missing_token",
        "config_path": str(config_path()),
        "source": credential_source(),
        "clone_username_configured": bool(clone_username()),
        "user_id": str(credentials.get("user_id", "")),
        "organization": str(credentials.get("organization", "")),
    }


def save_credentials(args: argparse.Namespace) -> dict[str, Any]:
    token = str(args.token or access_token()).strip()
    if not token:
        raise CodeupError(
            "没有可保存的云效 Token。请使用 --token，或先在本地配置中保存凭证"
        )

    organization = resolve_organization(args.org)
    user_id = str(args.user_id or "").strip()
    username = str(args.username or "").strip()
    lookup: Any = None
    if not user_id:
        lookup = cli_call(["devops", "base-get-user-by-token"], organization=organization)
        user_id = str(first_value(lookup, ("userId", "user_id", "id")) or "").strip()
    if not username and user_id:
        lookup = cli_call(
            [
                "devops",
                "codeup-get-member-https-clone-username",
                "--user-id",
                user_id,
            ],
            organization=organization,
        )
        username = str(
            first_value(lookup, ("username", "userName", "cloneUsername", "clone_username", "value"))
            or ""
        ).strip()
    if not username:
        raise CodeupError(
            "无法取得 Codeup HTTPS 克隆用户名；请使用 --username 显式提供",
            {"user_id": user_id},
        )

    config = load_config()
    credentials = dict(config.get("credentials", {}))
    credentials.update(
        {
            "access_token": token,
            "clone_username": username,
            "user_id": user_id,
            "organization": str(organization.get("key", "")),
        }
    )
    config["credentials"] = credentials
    save_config(config)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.auth",
        "status": "configured",
        "connection_status": "configured",
        "config_path": str(config_path()),
        "source": "config",
        "token_configured": True,
        "clone_username_configured": bool(username),
        "user_id": user_id,
        "organization": organization,
    }


def normalize_pipeline(item: Any) -> dict[str, Any]:
    raw = dict(item) if isinstance(item, dict) else {"value": item}
    return {
        "pipeline_id": raw.get("pipelineId") or raw.get("id"),
        "pipeline_name": raw.get("pipelineName") or raw.get("name") or "",
        "status": raw.get("status") or "",
        "raw": raw,
    }


def normalize_repo_url(value: Any) -> str:
    """Return a credential-free, scheme-independent Codeup repository key."""

    text = str(value or "").strip().strip("'\"")
    if not text:
        return ""
    if text.startswith("git@") and ":" in text:
        host, path = text[4:].split(":", 1)
        text = f"ssh://{host}/{path}"
    parsed = urlsplit(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower()
    if not host:
        return re.sub(r"\.git/?$", "", text).rstrip("/").lower()
    path = unquote(parsed.path or "").rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    port = f":{parsed.port}" if parsed.port else ""
    return f"{host}{port}{path}".lower()


def pipeline_sources(yaml_text: str) -> list[dict[str, Any]]:
    """Extract Codeup source declarations without retaining credentials."""

    try:
        document = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        document = {}
    sources = document.get("sources") if isinstance(document, dict) else None
    result: list[dict[str, Any]] = []
    if isinstance(sources, dict):
        for source_id, raw in sources.items():
            if not isinstance(raw, dict) or not raw.get("endpoint"):
                continue
            result.append(
                {
                    "id": str(source_id),
                    "type": raw.get("type", ""),
                    "name": raw.get("name", ""),
                    "endpoint": str(raw.get("endpoint", "")),
                    "repository_key": normalize_repo_url(raw.get("endpoint")),
                    "branch": str(raw.get("branch", "")),
                }
            )
    if result:
        return result
    # Keep matching useful for a partially invalid YAML while avoiding broad
    # string replacement in the caller.
    endpoint = ""
    branch = ""
    for line in yaml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("endpoint:"):
            endpoint = stripped.split(":", 1)[1].strip().strip("'\"")
        elif stripped.startswith("branch:"):
            branch = stripped.split(":", 1)[1].strip().strip("'\"")
        if endpoint:
            result.append(
                {
                    "id": f"repo_{len(result)}",
                    "type": "codeup",
                    "name": "",
                    "endpoint": endpoint,
                    "repository_key": normalize_repo_url(endpoint),
                    "branch": branch,
                }
            )
            endpoint = ""
            branch = ""
    return result


def pipeline_has_deployment(yaml_text: str) -> bool:
    try:
        document = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError:
        document = {}
    text = yaml_text.lower()
    if "vmdeploy" in text or "downloadartifact" in text:
        return True
    stages = document.get("stages") if isinstance(document, dict) else None
    if not isinstance(stages, dict):
        return False
    for stage in stages.values():
        if not isinstance(stage, dict) or not isinstance(stage.get("jobs"), dict):
            continue
        for job in stage["jobs"].values():
            if isinstance(job, dict) and str(job.get("component", "")).lower() == "vmdeploy":
                return True
    return False


def list_pipelines(args: argparse.Namespace) -> dict[str, Any]:
    organization = getattr(args, "organization", None) or resolve_organization(args.org)
    page = max(1, int(args.page))
    per_page = max(1, min(int(args.per_page), 30))
    pipelines: list[dict[str, Any]] = []
    pages = 0
    while True:
        command = [
            "devops",
            "flow-list-pipelines",
            "--page",
            str(page),
            "--per-page",
            str(per_page),
        ]
        if args.pipeline_name:
            command.extend(["--pipeline-name", args.pipeline_name])
        data = cli_call(command, organization=organization)
        if not isinstance(data, list):
            raise CodeupError("流水线列表返回格式不是数组", {"response": data})
        normalized = [normalize_pipeline(item) for item in data]
        pipelines.extend(normalized)
        pages += 1
        if not args.all or len(data) < per_page:
            break
        page += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.list",
        "status": "ok",
        "connection_status": "ok",
        "organization": organization,
        "page_count": pages,
        "pipelines": pipelines,
    }


def extract_yaml(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    config = data.get("pipelineConfig")
    if isinstance(config, dict):
        value = config.get("flow")
        if isinstance(value, str):
            return value
    value = data.get("content")
    return value if isinstance(value, str) else ""


def get_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    organization = getattr(args, "organization", None) or resolve_organization(args.org)
    data = cli_call(
        ["devops", "flow-get-pipeline", "--pipeline-id", str(args.pipeline_id)],
        organization=organization,
    )
    if not isinstance(data, dict):
        raise CodeupError("流水线详情返回格式不是对象", {"response": data})
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline",
        "status": "ok",
        "connection_status": "ok",
        "organization": organization,
        "pipeline": data,
    }
    yaml_text = extract_yaml(data)
    if args.yaml_out:
        target = Path(args.yaml_out).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_text, encoding="utf-8")
        os.chmod(target, 0o600)
        result["yaml_path"] = str(target)
    return result


def normalize_repository(item: Any) -> dict[str, Any]:
    raw = dict(item) if isinstance(item, dict) else {"value": item}
    return {
        "repository_id": raw.get("id") or raw.get("repositoryId"),
        "repository_name": raw.get("name") or raw.get("repositoryName") or "",
        "repository_path": raw.get("path") or raw.get("repositoryPath") or "",
        "namespace_id": raw.get("namespaceId") or raw.get("namespace_id"),
        "http_url": raw.get("httpUrlToRepo") or raw.get("http_url") or "",
        "ssh_url": raw.get("sshUrlToRepo") or raw.get("ssh_url") or "",
        "visibility": raw.get("visibility") or "",
        "description": raw.get("description") or "",
        "raw": raw,
    }


def normalize_namespace(item: Any) -> dict[str, Any]:
    raw = dict(item) if isinstance(item, dict) else {"value": item}
    namespace_id = raw.get("id") or raw.get("namespaceId") or raw.get("namespace_id")
    return {
        "namespace_id": namespace_id,
        "namespace_name": raw.get("name") or raw.get("namespaceName") or "",
        "namespace_path": raw.get("path") or "",
        "full_path": raw.get("fullPath") or raw.get("full_path") or "",
        "path_with_namespace": raw.get("pathWithNamespace") or raw.get("path_with_namespace") or "",
        "parent_id": raw.get("parentId") or raw.get("parent_id"),
        "visibility": raw.get("visibility") or "",
        "raw": raw,
    }


def list_namespaces(
    organization: dict[str, Any],
    *,
    search: str = "",
    parent_id: str | int | None = None,
    page: int = 1,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    command = [
        "devops",
        "codeup-list-namespaces",
        "--page",
        str(max(1, page)),
        "--per-page",
        str(max(1, min(per_page, 100))),
    ]
    if search:
        command.extend(["--search", search])
    if parent_id not in (None, ""):
        command.extend(["--parent-id", str(parent_id)])
    data = cli_call(command, organization=organization)
    if not isinstance(data, list):
        raise CodeupError("代码组空间列表返回格式不是数组", {"response": data})
    return [normalize_namespace(item) for item in data]


def create_group(
    organization: dict[str, Any],
    *,
    name: str,
    path: str,
    parent_id: str | int | None = None,
    visibility: str = "private",
    description: str = "",
    confirm: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not confirm and not dry_run:
        raise CodeupError("创建代码组需要 confirm")
    command = [
        "devops",
        "codeup-create-group",
        "--name",
        name,
        "--path",
        path,
        "--visibility",
        visibility,
    ]
    if parent_id not in (None, ""):
        command.extend(["--parent-id", str(parent_id)])
    if description:
        command.extend(["--description", description])
    data = cli_call(command, organization=organization, dry_run=dry_run)
    return normalize_namespace(data)


def list_group_repositories(
    organization: dict[str, Any],
    namespace_id: str | int,
    *,
    include_subgroups: bool = False,
    page: int = 1,
    per_page: int = 100,
) -> list[dict[str, Any]]:
    data = cli_call(
        [
            "devops",
            "codeup-list-group-repositories",
            "--group-id",
            str(namespace_id),
            "--include-subgroups",
            "true" if include_subgroups else "false",
            "--page",
            str(max(1, page)),
            "--per-page",
            str(max(1, min(per_page, 100))),
        ],
        organization=organization,
    )
    if not isinstance(data, list):
        raise CodeupError("代码组仓库列表返回格式不是数组", {"response": data})
    return [normalize_repository(item) for item in data]


def list_repositories(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    repositories = list_group_repositories(
        organization,
        args.namespace_id,
        include_subgroups=args.include_subgroups,
        page=args.page,
        per_page=args.per_page,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.repository.list",
        "status": "ok",
        "connection_status": "ok",
        "organization": organization,
        "namespace_id": str(args.namespace_id),
        "repositories": repositories,
    }


def create_repository(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    if not args.confirm and not args.dry_run:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "codeup.repository.create",
            "status": "dry_run",
            "connection_status": "ok",
            "organization": organization,
            "namespace_id": str(args.namespace_id),
            "repository_name": args.name,
            "repository_path": args.path,
            "error": "创建远程仓库需要 --confirm",
        }
    command = [
        "devops",
        "codeup-create-repository",
        "--name",
        args.name,
        "--path",
        args.path,
        "--namespace-id",
        str(args.namespace_id),
        "--visibility",
        args.visibility,
    ]
    if args.description:
        command.extend(["--description", args.description])
    if args.read_me_type:
        command.extend(["--read-me-type", args.read_me_type])
    if args.create_parent_path:
        command.extend(["--create-parent-path", "true"])
    data = cli_call(command, organization=organization, dry_run=args.dry_run)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.repository.create",
        "status": "dry_run" if args.dry_run else "created",
        "connection_status": "ok",
        "organization": organization,
        "namespace_id": str(args.namespace_id),
        "repository_name": args.name,
        "repository_path": args.path,
        "repository": normalize_repository(data),
    }


def update_repository_default_branch(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    if not args.confirm and not args.dry_run:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "codeup.repository.update",
            "status": "dry_run",
            "connection_status": "ok",
            "organization": organization,
            "repository_id": str(args.repository_id),
            "default_branch": args.branch,
            "error": "修改代码库默认分支需要 --confirm",
        }
    command = [
        "devops",
        "codeup-update-repository",
        "--repository-id",
        str(args.repository_id),
        "--default-branch",
        args.branch,
    ]
    data = cli_call(command, organization=organization, dry_run=args.dry_run)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.repository.update",
        "status": "dry_run" if args.dry_run else "updated",
        "connection_status": "ok",
        "organization": organization,
        "repository_id": str(args.repository_id),
        "default_branch": args.branch,
        "repository": data,
    }


def delete_repository_branch(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    if not args.confirm and not args.dry_run:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "codeup.repository.branch.delete",
            "status": "dry_run",
            "connection_status": "ok",
            "organization": organization,
            "repository_id": str(args.repository_id),
            "branch": args.branch,
            "error": "删除代码库分支需要 --confirm",
        }
    command = [
        "devops",
        "codeup-delete-branch",
        "--repository-id",
        str(args.repository_id),
        "--branch-name",
        args.branch,
    ]
    data = cli_call(command, organization=organization, dry_run=args.dry_run)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.repository.branch.delete",
        "status": "dry_run" if args.dry_run else "deleted",
        "connection_status": "ok",
        "organization": organization,
        "repository_id": str(args.repository_id),
        "branch": args.branch,
        "result": data,
    }


def list_repository_branches(organization: dict[str, Any], repository_id: str) -> list[str]:
    data = cli_call(
        [
            "devops",
            "codeup-list-branches",
            "--repository-id",
            str(repository_id),
            "--per-page",
            "100",
        ],
        organization=organization,
    )
    names: list[str] = []
    branch_keys = ("name", "branchName", "branch_name")

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in branch_keys:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    if candidate.strip() not in names:
                        names.append(candidate.strip())
                    break
            for key, child in value.items():
                if key not in branch_keys:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    return names


def repository_branch_policy(
    organization: dict[str, Any],
    repository_id: str,
    *,
    default_branch: str = "develop",
    confirm: bool,
) -> dict[str, Any]:
    branches = list_repository_branches(organization, repository_id)
    result: dict[str, Any] = {
        "repository_id": str(repository_id),
        "default_branch": default_branch,
        "branches_before": branches,
        "deleted": [],
    }
    if default_branch not in branches:
        return {
            **result,
            "status": "blocked",
            "error": f"远程仓库不存在目标分支：{default_branch}",
        }
    if not confirm:
        return {**result, "status": "dry_run", "would_delete": [name for name in ("main", "master") if name in branches and name != default_branch]}
    cli_call(
        [
            "devops",
            "codeup-update-repository",
            "--repository-id",
            str(repository_id),
            "--default-branch",
            default_branch,
        ],
        organization=organization,
    )
    stale_branches = [name for name in ("main", "master") if name in branches and name != default_branch]
    for branch in stale_branches:
        cli_call(
            [
                "devops",
                "codeup-delete-branch",
                "--repository-id",
                str(repository_id),
                "--branch-name",
                branch,
            ],
            organization=organization,
        )
    result.update({"status": "success", "deleted": stale_branches, "default_branch_set": True})
    return result


def repository_branch_policy_command(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    result = repository_branch_policy(
        organization,
        str(args.repository_id),
        default_branch=args.branch,
        confirm=bool(args.confirm and not args.dry_run),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.repository.branch-policy",
        "connection_status": "ok",
        "organization": organization,
        **result,
    }


def locate_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    if args.org:
        return get_pipeline(args)
    config = load_config()
    candidates: list[dict[str, Any]] = []
    for key, raw in dict(config.get("organizations", {})).items():
        item = dict(raw)
        item["key"] = key
        try:
            organization = resolve_organization(key)
            data = cli_call(
                ["devops", "flow-get-pipeline", "--pipeline-id", str(args.pipeline_id)],
                organization=organization,
            )
            if isinstance(data, dict):
                candidates.append({"organization": organization, "pipeline": data})
        except CodeupError:
            continue
    if not candidates:
        for remote in remote_organizations(save=False):
            organization = {
                "key": organization_key(remote.get("name"), remote.get("organization_id")),
                "name": remote.get("name", ""),
                "edition": "central",
                "organization_id": remote.get("organization_id"),
            }
            try:
                data = cli_call(
                    ["devops", "flow-get-pipeline", "--pipeline-id", str(args.pipeline_id)],
                    organization=organization,
                )
                if isinstance(data, dict):
                    candidates.append({"organization": organization, "pipeline": data})
            except CodeupError:
                continue
    status = "located" if len(candidates) == 1 else "ambiguous" if candidates else "not_found"
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.locate",
        "status": status,
        "connection_status": "ok",
        "pipeline_id": str(args.pipeline_id),
        "matches": candidates,
    }


def search_organizations(org: str | None) -> list[dict[str, Any]]:
    if org:
        organization = resolve_organization(org)
        return [organization] if organization else []
    config = load_config()
    organizations: list[dict[str, Any]] = []
    for key in dict(config.get("organizations", {})):
        try:
            organization = resolve_organization(key)
        except CodeupError:
            continue
        if organization:
            organizations.append(organization)
    if organizations:
        return organizations
    return [
        {
            "key": organization_key(item.get("name"), item.get("organization_id")),
            "name": item.get("name", ""),
            "edition": "central",
            "organization_id": item.get("organization_id", ""),
        }
        for item in remote_organizations(save=False)
    ]


def find_pipelines(args: argparse.Namespace) -> dict[str, Any]:
    target = normalize_repo_url(args.repo_url)
    if not target:
        raise CodeupError("--repo-url 不能为空")
    matches: list[dict[str, Any]] = []
    searched: list[dict[str, Any]] = []
    for organization in search_organizations(args.org):
        searched.append(organization)
        listed = list_pipelines(
            argparse.Namespace(
                org=organization["key"],
                organization=organization,
                pipeline_name=args.pipeline_name,
                page=1,
                per_page=args.per_page,
                all=True,
            )
        )
        for item in listed["pipelines"]:
            pipeline_id = str(item.get("pipeline_id") or "")
            if not pipeline_id:
                continue
            detail = get_pipeline(
                argparse.Namespace(org=organization["key"], organization=organization, pipeline_id=pipeline_id, yaml_out=None)
            )
            pipeline = detail["pipeline"]
            yaml_text = extract_yaml(pipeline)
            for source in pipeline_sources(yaml_text):
                if source["repository_key"] != target:
                    continue
                branch = str(source.get("branch") or "")
                matches.append(
                    {
                        "organization": organization,
                        "pipeline_id": pipeline_id,
                        "pipeline_name": pipeline.get("name") or item.get("pipeline_name", ""),
                        "source": source,
                        "branch_match": not args.branch or branch == args.branch,
                        "has_deployment": pipeline_has_deployment(yaml_text),
                        "yaml_sha256": hashlib.sha256(yaml_text.encode("utf-8")).hexdigest(),
                        "pipeline_type": pipeline.get("type", ""),
                    }
                )
                break
    exact_branch = [item for item in matches if item["branch_match"]]
    status = "located" if len(exact_branch) == 1 else "ambiguous" if len(exact_branch) > 1 else "not_found"
    if not exact_branch and len(matches) == 1 and not args.branch:
        status = "located"
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.find",
        "status": status,
        "connection_status": "ok",
        "repo_url": args.repo_url,
        "repository_key": target,
        "branch": args.branch or "",
        "searched_organizations": searched,
        "matches": matches,
    }


def read_yaml(path: str) -> str:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise CodeupError(f"流水线 YAML 文件不存在：{target}")
    content = target.read_text(encoding="utf-8")
    if not content.strip():
        raise CodeupError(f"流水线 YAML 文件为空：{target}")
    return content


def create_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    content = read_yaml(args.yaml)
    data = cli_call(
        ["devops", "flow-create-pipeline", "--name", args.name, "--content", content],
        organization=organization,
        dry_run=args.dry_run,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.create",
        "status": "dry_run" if args.dry_run else "created",
        "connection_status": "ok",
        "organization": organization,
        "pipeline_name": args.name,
        "pipeline": data,
    }
    if args.run_after and not args.dry_run:
        pipeline_id = extract_id(data)
        if not pipeline_id:
            listed = list_pipelines(
                argparse.Namespace(
                    org=args.org,
                    organization=organization,
                    page=1,
                    per_page=30,
                    pipeline_name=args.name,
                    all=True,
                )
            )
            matches = [
                item for item in listed.get("pipelines", [])
                if str(item.get("pipeline_name", "")) == str(args.name)
                and str(item.get("pipeline_id", "")).strip()
            ]
            if len(matches) != 1:
                raise CodeupError(
                    "流水线创建响应未返回 ID，且按精确名称无法唯一定位新流水线",
                    {"pipeline_name": args.name, "matches": matches},
                )
            pipeline_id = str(matches[0]["pipeline_id"])
        result["pipeline_id"] = pipeline_id
        result["run"] = run_pipeline(
            argparse.Namespace(
                org=args.org,
                pipeline_id=pipeline_id,
                params=args.params,
                params_file=args.params_file,
                branch=args.branch,
                tag=args.tag,
                repo=args.repo,
                env=args.env,
                comment=args.comment,
                dry_run=False,
                watch=args.watch,
                interval=args.interval,
                timeout=args.timeout,
                logs_on_failure=False,
            )
        )
    return result


def update_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    if not args.confirm and not args.dry_run:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "codeup.pipeline.update",
            "status": "dry_run",
            "connection_status": "ok",
            "organization": organization,
            "pipeline_id": str(args.pipeline_id),
            "pipeline_name": args.name,
            "error": "更新流水线需要 --confirm",
        }
    content = read_yaml(args.yaml)
    data = cli_call(
        [
            "devops",
            "flow-update-pipeline",
            "--pipeline-id",
            str(args.pipeline_id),
            "--name",
            args.name,
            "--content",
            content,
        ],
        organization=organization,
        dry_run=args.dry_run,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.update",
        "status": "dry_run" if args.dry_run else "updated",
        "connection_status": "ok",
        "organization": organization,
        "pipeline_id": str(args.pipeline_id),
        "pipeline_name": args.name,
        "pipeline": data,
    }
    if args.run_after and not args.dry_run:
        result["run"] = run_pipeline(
            argparse.Namespace(
                org=args.org,
                pipeline_id=args.pipeline_id,
                params=args.params,
                params_file=args.params_file,
                branch=args.branch,
                tag=args.tag,
                repo=args.repo,
                env=args.env,
                comment=args.comment,
                dry_run=False,
                watch=args.watch,
                interval=args.interval,
                timeout=args.timeout,
                logs_on_failure=False,
            )
        )
    return result


def parse_params(args: argparse.Namespace) -> dict[str, Any]:
    if args.params and args.params_file:
        raise CodeupError("--params 和 --params-file 只能二选一")
    params: dict[str, Any] = {}
    if args.params_file:
        try:
            loaded = json.loads(Path(args.params_file).expanduser().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodeupError(f"运行参数文件读取失败：{args.params_file}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise CodeupError("运行参数 JSON 必须是对象")
        params.update(loaded)
    if args.params:
        try:
            loaded = json.loads(args.params)
        except json.JSONDecodeError as exc:
            raise CodeupError(f"--params 不是合法 JSON：{exc}") from exc
        if not isinstance(loaded, dict):
            raise CodeupError("--params JSON 必须是对象")
        params.update(loaded)
    if args.branch or args.tag:
        if not args.repo:
            raise CodeupError("使用 --branch 或 --tag 时必须同时提供 --repo")
        if args.branch:
            params.setdefault("runningBranchs", {})[args.repo] = args.branch
        if args.tag:
            params.setdefault("runningTags", {})[args.repo] = args.tag
    if args.env:
        envs = dict(params.get("envs", {}))
        for item in args.env:
            if "=" not in item:
                raise CodeupError(f"--env 必须是 KEY=VALUE：{item}")
            key, value = item.split("=", 1)
            if not key:
                raise CodeupError("--env 的变量名不能为空")
            envs[key] = value
        params["envs"] = envs
    if args.comment:
        params["comment"] = args.comment
    return params


def extract_id(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("pipelineId", "pipelineRunId", "runId", "id"):
            value = data.get(key)
            if value is not None:
                return str(value)
        for value in data.values():
            found = extract_id(value)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = extract_id(value)
            if found:
                return found
    return None


def extract_run_id(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("pipelineRunId", "runId", "id"):
            value = data.get(key)
            if value is not None:
                return str(value)
        for value in data.values():
            found = extract_run_id(value)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = extract_run_id(value)
            if found:
                return found
    return None


def first_value(data: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(data, dict):
        for key in keys:
            if key in data and data[key] not in (None, ""):
                return data[key]
        for value in data.values():
            found = first_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for value in data:
            found = first_value(value, keys)
            if found not in (None, ""):
                return found
    return None


def normalize_run_status(data: Any) -> str:
    value = first_value(data, ("status", "pipelineRunStatus", "result", "state"))
    text = str(value or "").upper()
    if text in TERMINAL_SUCCESS:
        return "success"
    if text in TERMINAL_FAILURE:
        return "failed"
    if text in RUNNING_STATES:
        return "running"
    return "unknown"


def watch_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    started = time.monotonic()
    poll_count = 0
    last: Any = None
    while True:
        poll_count += 1
        last = cli_call(
            [
                "devops",
                "flow-get-pipeline-run",
                "--pipeline-id",
                str(args.pipeline_id),
                "--pipeline-run-id",
                str(args.run_id),
            ],
            organization=organization,
        )
        status = normalize_run_status(last)
        if status in {"success", "failed"}:
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": "codeup.pipeline.watch",
                "status": status,
                "connection_status": "ok",
                "organization": organization,
                "pipeline_id": str(args.pipeline_id),
                "pipeline_run_id": str(args.run_id),
                "poll_count": poll_count,
                "run": last,
            }
        if time.monotonic() - started >= args.timeout:
            return {
                "schema_version": SCHEMA_VERSION,
                "kind": "codeup.pipeline.watch",
                "status": "timeout",
                "connection_status": "ok",
                "organization": organization,
                "pipeline_id": str(args.pipeline_id),
                "pipeline_run_id": str(args.run_id),
                "poll_count": poll_count,
                "run": last,
            }
        time.sleep(max(0.1, args.interval))


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    organization = resolve_organization(args.org)
    params = parse_params(args)
    try:
        previous = cli_call(
            [
                "devops",
                "flow-get-latest-pipeline-run",
                "--pipeline-id",
                str(args.pipeline_id),
            ],
            organization=organization,
        )
    except CodeupError as exc:
        if "InvalidPipelineRun.NotFound" in json.dumps(exc.details, ensure_ascii=False):
            previous = {}
        else:
            raise
    previous_run_id = extract_run_id(previous)
    command = [
        "devops",
        "flow-create-pipeline-run",
        "--pipeline-id",
        str(args.pipeline_id),
    ]
    if params:
        command.extend(["--params", json.dumps(params, ensure_ascii=False, separators=(",", ":"))])
    data = cli_call(command, organization=organization, dry_run=args.dry_run)
    run_id = extract_run_id(data)
    latest = None
    if not run_id and not args.dry_run:
        latest = cli_call(
            [
                "devops",
                "flow-get-latest-pipeline-run",
                "--pipeline-id",
                str(args.pipeline_id),
            ],
            organization=organization,
        )
        candidate = extract_run_id(latest)
        if candidate and (not previous_run_id or candidate != previous_run_id):
            run_id = candidate
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.pipeline.run",
        "status": "dry_run" if args.dry_run else "running",
        "connection_status": "ok",
        "organization": organization,
        "pipeline_id": str(args.pipeline_id),
        "run_id": run_id,
        "params": params,
        "run": data,
    }
    if latest is not None:
        result["latest_run_lookup"] = latest
    if args.watch and not args.dry_run:
        if not run_id:
            raise CodeupError("流水线运行响应中没有找到 run id", {"response": data})
        watch_args = argparse.Namespace(
            org=args.org,
            pipeline_id=args.pipeline_id,
            run_id=run_id,
            interval=args.interval,
            timeout=args.timeout,
        )
        result["watch"] = watch_pipeline(watch_args)
        result["status"] = result["watch"]["status"]
    return result


def pipeline_watch_command(args: argparse.Namespace) -> dict[str, Any]:
    return watch_pipeline(args)


def apply_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    if bool(args.pipeline_id) == bool(args.create):
        raise CodeupError("apply 必须二选一：提供 --pipeline-id 更新，或提供 --create 创建")
    if args.create:
        create_args = argparse.Namespace(**vars(args))
        create_args.run_after = args.run_after
        create_args.dry_run = args.dry_run
        return create_pipeline(create_args)
    update_args = argparse.Namespace(**vars(args))
    update_args.run_after = args.run_after
    update_args.dry_run = args.dry_run
    return update_pipeline(update_args)


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    token = access_token()
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "codeup.doctor",
        "status": "ready",
        "connection_status": "unavailable",
        "config_path": str(config_path()),
        "config_exists": config_path().exists(),
        "token_configured": bool(token),
        "credential_source": credential_source(),
        "clone_username_configured": bool(clone_username()),
        "organizations_configured": len(load_config().get("organizations", {})),
    }
    try:
        binary = resolve_aliyun_bin()
        result["aliyun_bin"] = binary
        result["aliyun_version"] = cli_call(["devops", "version"])
    except CodeupError as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        result["details"] = exc.details
        return result
    if args.live:
        try:
            result["remote_organizations"] = remote_organizations(save=False)
            result["connection_status"] = "ok"
        except CodeupError as exc:
            result["status"] = "failed"
            result["connection_status"] = "failed"
            result["error"] = str(exc)
            result["details"] = exc.details
    else:
        result["connection_status"] = "configured" if result["token_configured"] else "missing_token"
        if not result["token_configured"]:
            result["status"] = "partial"
    return result


def request_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    allowed = (
        "command",
        "repository_command",
        "auth_command",
        "org",
        "pipeline_id",
        "run_id",
        "namespace_id",
        "repository_name",
        "path",
        "visibility",
        "confirm",
        "repo_url",
        "branch",
        "name",
        "yaml",
        "yaml_out",
        "create",
        "run_after",
        "watch",
        "dry_run",
        "live",
        "page",
        "per_page",
        "all",
    )
    return {key: getattr(args, key) for key in allowed if hasattr(args, key) and getattr(args, key) not in (None, False, "")}


def render_human(payload: dict[str, Any]) -> None:
    status = payload.get("status", "unknown")
    print(f"{payload.get('kind', TOOL_NAME)}: {status}")
    if payload.get("error"):
        print(f"error: {payload['error']}", file=sys.stderr)
    organization = payload.get("organization")
    if isinstance(organization, dict):
        print(f"organization: {organization.get('key') or organization.get('name') or organization.get('organization_id', '')}")
    if isinstance(payload.get("organizations"), list):
        for item in payload["organizations"]:
            print(f"- {item.get('name', '')} [{item.get('organization_id', '')}]")
    if isinstance(payload.get("pipelines"), list):
        for item in payload["pipelines"]:
            print(f"- {item.get('pipeline_id', '')}: {item.get('pipeline_name', '')}")
    if isinstance(payload.get("repositories"), list):
        for item in payload["repositories"]:
            line = f"- {item.get('repository_id', '')}: {item.get('repository_name', '')} {item.get('http_url', '')}"
            print(line.rstrip())
    repository = payload.get("repository")
    if isinstance(repository, dict):
        line = f"repository: {repository.get('repository_id', '')} {repository.get('http_url', '')}"
        print(line.rstrip())
    if payload.get("run_id"):
        print(f"run_id: {payload['run_id']}")
    if payload.get("yaml_path"):
        print(f"yaml: {payload['yaml_path']}")


def command_exit_code(payload: dict[str, Any]) -> int:
    return 0 if payload.get("status") in {
        "ok",
        "ready",
        "partial",
        "registered",
        "located",
        "created",
        "already_exists",
        "updated",
        "running",
        "success",
        "configured",
        "dry_run",
    } else 1


def add_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def add_org_arg(parser: argparse.ArgumentParser, *, required: bool = False) -> None:
    parser.add_argument("--org", required=required, help="本地组织 key、组织名或组织 ID")


def add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--params", help="流水线运行参数 JSON")
    parser.add_argument("--params-file", help="流水线运行参数 JSON 文件")
    parser.add_argument("--repo", help="--branch/--tag 对应的仓库 URL")
    parser.add_argument("--branch", help="运行指定分支")
    parser.add_argument("--tag", help="运行指定 Tag")
    parser.add_argument("--env", action="append", help="运行时变量 KEY=VALUE，可重复")
    parser.add_argument("--comment", help="运行备注")
    parser.add_argument("--watch", action="store_true", help="运行后轮询结果")
    parser.add_argument("--interval", type=float, default=DEFAULT_WATCH_INTERVAL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_WATCH_TIMEOUT)
    parser.add_argument("--dry-run", action="store_true", help="只打印 CLI 请求，不发送 API 请求")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过官方 aliyun devops CLI 管理云效组织、流水线和运行实例。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="检查本机 CLI、插件和云效连接")
    doctor_parser.add_argument("--live", action="store_true", help="实际查询远端组织")
    add_output_args(doctor_parser)
    doctor_parser.set_defaults(func=doctor)

    auth_parser = subparsers.add_parser("auth", help="保存和检查 Codeup 凭证")
    auth_sub = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_show = auth_sub.add_parser("show", help="显示凭证状态，不显示 Token")
    add_output_args(auth_show)
    auth_show.set_defaults(func=credential_status)
    auth_save = auth_sub.add_parser("save", help="保存云效 Token 和 HTTPS 克隆用户名")
    add_org_arg(auth_save, required=True)
    auth_save.add_argument("--token", help="云效个人访问令牌；未提供时读取已有本地配置")
    auth_save.add_argument("--user-id", help="可选；不提供时通过官方接口查询")
    auth_save.add_argument("--username", help="可选；不提供时通过官方接口查询 HTTPS 克隆用户名")
    add_output_args(auth_save)
    auth_save.set_defaults(func=save_credentials)

    org_parser = subparsers.add_parser("org", help="发现、登记和查看组织上下文")
    org_sub = org_parser.add_subparsers(dest="org_command", required=True)
    org_list = org_sub.add_parser("list", help="查询当前 Token 可访问的中心版组织")
    org_list.add_argument("--save", action="store_true", help="同步保存到本地组织配置")
    add_output_args(org_list)
    org_list.set_defaults(func=list_organizations)
    org_register = org_sub.add_parser("register", help="登记一个组织上下文")
    org_register.add_argument("key")
    org_register.add_argument("--name")
    org_register.add_argument("--edition", choices=("central", "region"), required=True)
    org_register.add_argument("--organization-id")
    org_register.add_argument("--api-base-url")
    org_register.add_argument("--organization-alias")
    org_register.add_argument("--default", action="store_true")
    add_output_args(org_register)
    org_register.set_defaults(func=register_organization)
    org_show = org_sub.add_parser("show", help="查看本地组织上下文")
    add_org_arg(org_show, required=True)
    add_output_args(org_show)
    org_show.set_defaults(func=show_organization)

    pipeline_parser = subparsers.add_parser("pipeline", help="管理云效流水线")
    pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)

    pipeline_list = pipeline_sub.add_parser("list", help="列出流水线")
    add_org_arg(pipeline_list, required=True)
    pipeline_list.add_argument("--pipeline-name")
    pipeline_list.add_argument("--page", type=int, default=1)
    pipeline_list.add_argument("--per-page", type=int, default=DEFAULT_PAGE_SIZE)
    pipeline_list.add_argument("--all", action="store_true", help="继续读取后续页")
    add_output_args(pipeline_list)
    pipeline_list.set_defaults(func=list_pipelines)

    pipeline_get = pipeline_sub.add_parser("get", help="读取流水线详情和 YAML")
    add_org_arg(pipeline_get, required=True)
    pipeline_get.add_argument("--pipeline-id", required=True)
    pipeline_get.add_argument("--yaml-out")
    add_output_args(pipeline_get)
    pipeline_get.set_defaults(func=get_pipeline)

    pipeline_locate = pipeline_sub.add_parser("locate", help="跨已登记/可发现组织定位流水线")
    pipeline_locate.add_argument("--pipeline-id", required=True)
    add_org_arg(pipeline_locate)
    add_output_args(pipeline_locate)
    pipeline_locate.set_defaults(func=locate_pipeline)

    pipeline_find = pipeline_sub.add_parser("find", help="按 Codeup Git 地址匹配流水线")
    pipeline_find.add_argument("--repo-url", required=True)
    pipeline_find.add_argument("--branch")
    pipeline_find.add_argument("--pipeline-name")
    pipeline_find.add_argument("--per-page", type=int, default=DEFAULT_PAGE_SIZE)
    add_org_arg(pipeline_find)
    add_output_args(pipeline_find)
    pipeline_find.set_defaults(func=find_pipelines)

    pipeline_create = pipeline_sub.add_parser("create", help="创建 YAML 流水线")
    add_org_arg(pipeline_create, required=True)
    pipeline_create.add_argument("--name", required=True)
    pipeline_create.add_argument("--yaml", required=True)
    pipeline_create.add_argument("--run-after", action="store_true")
    add_run_args(pipeline_create)
    add_output_args(pipeline_create)
    pipeline_create.set_defaults(func=create_pipeline)

    pipeline_update = pipeline_sub.add_parser("update", help="更新 YAML 流水线")
    add_org_arg(pipeline_update, required=True)
    pipeline_update.add_argument("--pipeline-id", required=True)
    pipeline_update.add_argument("--name", required=True)
    pipeline_update.add_argument("--yaml", required=True)
    pipeline_update.add_argument("--run-after", action="store_true")
    pipeline_update.add_argument("--confirm", action="store_true", help="确认执行远程更新")
    add_run_args(pipeline_update)
    add_output_args(pipeline_update)
    pipeline_update.set_defaults(func=update_pipeline)

    pipeline_apply = pipeline_sub.add_parser("apply", help="创建或更新后按需自动运行")
    add_org_arg(pipeline_apply, required=True)
    pipeline_apply.add_argument("--create", action="store_true", help="创建新流水线")
    pipeline_apply.add_argument("--pipeline-id", help="已有流水线 ID；提供后执行更新")
    pipeline_apply.add_argument("--name", required=True)
    pipeline_apply.add_argument("--yaml", required=True)
    pipeline_apply.add_argument("--run-after", action="store_true")
    pipeline_apply.add_argument("--confirm", action="store_true", help="确认执行远程创建或更新")
    add_run_args(pipeline_apply)
    add_output_args(pipeline_apply)
    pipeline_apply.set_defaults(func=apply_pipeline)

    pipeline_run = pipeline_sub.add_parser("run", help="运行流水线")
    add_org_arg(pipeline_run, required=True)
    pipeline_run.add_argument("--pipeline-id", required=True)
    add_run_args(pipeline_run)
    add_output_args(pipeline_run)
    pipeline_run.set_defaults(func=run_pipeline)

    pipeline_watch = pipeline_sub.add_parser("watch", help="轮询流水线运行实例")
    add_org_arg(pipeline_watch, required=True)
    pipeline_watch.add_argument("--pipeline-id", required=True)
    pipeline_watch.add_argument("--run-id", required=True)
    pipeline_watch.add_argument("--interval", type=float, default=DEFAULT_WATCH_INTERVAL)
    pipeline_watch.add_argument("--timeout", type=float, default=DEFAULT_WATCH_TIMEOUT)
    add_output_args(pipeline_watch)
    pipeline_watch.set_defaults(func=pipeline_watch_command)

    repository_parser = subparsers.add_parser("repository", help="管理 Codeup 代码库")
    repository_sub = repository_parser.add_subparsers(dest="repository_command", required=True)

    repository_list = repository_sub.add_parser("list", help="列出代码组下的代码库")
    add_org_arg(repository_list, required=True)
    repository_list.add_argument("--namespace-id", required=True)
    repository_list.add_argument("--include-subgroups", action="store_true")
    repository_list.add_argument("--page", type=int, default=1)
    repository_list.add_argument("--per-page", type=int, default=100)
    add_output_args(repository_list)
    repository_list.set_defaults(func=list_repositories)

    repository_create = repository_sub.add_parser("create", help="创建 Codeup 代码库")
    add_org_arg(repository_create, required=True)
    repository_create.add_argument("--namespace-id", required=True, help="父代码组 ID")
    repository_create.add_argument("--name", required=True, help="代码库名称")
    repository_create.add_argument("--path", required=True, help="代码库路径")
    repository_create.add_argument("--description")
    repository_create.add_argument("--visibility", choices=("private", "internal"), default="private")
    repository_create.add_argument("--read-me-type", choices=("EMPTY", "USER_GUIDE"))
    repository_create.add_argument("--create-parent-path", action="store_true")
    repository_create.add_argument("--confirm", action="store_true", help="确认执行远程创建")
    repository_create.add_argument("--dry-run", action="store_true", help="只打印 CLI 请求，不发送 API 请求")
    add_output_args(repository_create)
    repository_create.set_defaults(func=create_repository)

    repository_default_branch = repository_sub.add_parser("set-default-branch", help="设置代码库默认分支")
    add_org_arg(repository_default_branch, required=True)
    repository_default_branch.add_argument("--repository-id", required=True)
    repository_default_branch.add_argument("--branch", default="develop")
    repository_default_branch.add_argument("--confirm", action="store_true", help="确认执行远程修改")
    repository_default_branch.add_argument("--dry-run", action="store_true", help="只打印 CLI 请求，不发送 API 请求")
    add_output_args(repository_default_branch)
    repository_default_branch.set_defaults(func=update_repository_default_branch)

    repository_delete_branch = repository_sub.add_parser("delete-branch", help="删除代码库分支")
    add_org_arg(repository_delete_branch, required=True)
    repository_delete_branch.add_argument("--repository-id", required=True)
    repository_delete_branch.add_argument("--branch", required=True)
    repository_delete_branch.add_argument("--confirm", action="store_true", help="确认执行远程删除")
    repository_delete_branch.add_argument("--dry-run", action="store_true", help="只打印 CLI 请求，不发送 API 请求")
    add_output_args(repository_delete_branch)
    repository_delete_branch.set_defaults(func=delete_repository_branch)

    repository_policy = repository_sub.add_parser("branch-policy", help="设置 develop 默认分支并清理 main/master")
    add_org_arg(repository_policy, required=True)
    repository_policy.add_argument("--repository-id", required=True)
    repository_policy.add_argument("--branch", default="develop")
    repository_policy.add_argument("--confirm", action="store_true", help="确认执行远程修改")
    repository_policy.add_argument("--dry-run", action="store_true", help="只打印 CLI 请求，不发送 API 请求")
    add_output_args(repository_policy)
    repository_policy.set_defaults(func=repository_branch_policy_command)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = args.func(args)
        try:
            save_snapshot(
                TOOL_NAME,
                getattr(args, "command", "unknown"),
                canonical_project_root(),
                request_snapshot(args),
                payload,
                command_exit_code(payload) == 0,
                target=str(getattr(args, "pipeline_id", "") or getattr(args, "org", "")),
            )
        except OSError:
            pass
        if args.json:
            print_json(payload)
        else:
            render_human(payload)
        return command_exit_code(payload)
    except Exception as exc:
        details = getattr(exc, "details", {})
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "codeup.error",
            "status": "failed",
            "connection_status": "unknown",
            "error": str(exc),
            "details": details,
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print(f"error: {payload['error']}", file=sys.stderr)
            if details:
                print(json.dumps(details, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
