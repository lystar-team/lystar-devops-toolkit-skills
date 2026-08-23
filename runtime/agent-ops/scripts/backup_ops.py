#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path
from typing import Any

import registry_store


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT = 120
REMOTE_OUTPUT_BYTES = 64_000
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MANIFEST_SUFFIX = ".manifest.json"
BACKUP_REPOSITORY_KINDS = {"local"}
BACKUP_ASSET_KINDS = {"db", "file"}


class BackupError(RuntimeError):
    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def emit(args: argparse.Namespace, payload: dict[str, Any], exit_code: int = 0) -> int:
    if getattr(args, "json", False):
        print_json(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return exit_code


def trim_error(value: Any) -> str:
    text = str(value or "").strip()
    return text[-1000:] if len(text) > 1000 else text


def tool_version() -> str:
    value = os.environ.get("LYSTAR_VERSION", "").strip()
    if value:
        return value
    for candidate in (
        Path(__file__).resolve().parents[3] / "VERSION",
        Path.cwd() / "VERSION",
    ):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "unknown"


def backup_registry() -> registry_store.RegistryStore:
    """使用与 deployx 相同的全局注册表，允许专项测试显式隔离路径。"""

    registry_file = (
        os.environ.get("BACKUPX_REGISTRY_FILE")
        or os.environ.get("DEPLOYX_REGISTRY_FILE")
        or None
    )
    revision_dir = (
        os.environ.get("BACKUPX_REGISTRY_REVISION_DIR")
        or os.environ.get("DEPLOYX_REGISTRY_REVISION_DIR")
        or None
    )
    return registry_store.RegistryStore(
        registry_file=registry_file,
        revision_dir=revision_dir,
    )


def registry_payload(
    kind: str,
    status: str,
    store: registry_store.RegistryStore,
    **values: Any,
) -> dict[str, Any]:
    document = store.load()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": status,
        "connection_status": "not_applicable",
        "registry_revision": document.get("revision", 0),
    }
    payload.update(values)
    return payload


def repository_path_from_object(
    repository: dict[str, Any],
    *,
    create: bool,
) -> Path:
    repository_id = str(repository.get("id") or "")
    if str(repository.get("kind") or "local") not in BACKUP_REPOSITORY_KINDS:
        raise registry_store.RegistryError(
            f"不支持的备份仓库类型：{repository.get('kind')}"
        )
    if repository.get("enabled") is False:
        raise registry_store.RegistryError(f"备份仓库已停用：{repository_id}")
    raw_path = str(repository.get("path") or "").strip()
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise registry_store.RegistryError(
            f"备份仓库路径必须是绝对路径：{repository_id}"
        )
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"备份仓库不是目录：{path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise registry_store.RegistryError(f"备份仓库不存在：{path}")
    return path


def registered_repository(
    store: registry_store.RegistryStore,
    repository_id: str | None = None,
    *,
    create: bool,
) -> tuple[dict[str, Any], Path]:
    repositories = store.list("backup_repository")
    selected: dict[str, Any] | None = None
    if repository_id:
        selected = next(
            (item for item in repositories if str(item.get("id")) == repository_id),
            None,
        )
        if selected is None:
            raise registry_store.RegistryError(f"backup repository does not exist: {repository_id}")
    else:
        defaults = [item for item in repositories if item.get("default") is True]
        enabled_defaults = [item for item in defaults if item.get("enabled") is not False]
        if len(enabled_defaults) != 1:
            if not enabled_defaults:
                raise registry_store.RegistryError(
                    "未登记默认备份仓库；请先使用 backupx repository register --default"
                )
            raise registry_store.RegistryError("登记了多个默认备份仓库")
        selected = enabled_defaults[0]
    assert selected is not None
    return selected, repository_path_from_object(selected, create=create)


def ensure_repository(value: str, *, create: bool) -> Path:
    if not value.strip():
        raise ValueError("--repository 不能为空")
    repository = Path(value).expanduser().resolve()
    if repository.exists() and not repository.is_dir():
        raise ValueError(f"备份仓库不是目录：{repository}")
    if create:
        repository.mkdir(parents=True, exist_ok=True)
    elif not repository.is_dir():
        raise ValueError(f"备份仓库不存在：{repository}")
    return repository


def backup_asset(args: argparse.Namespace, expected_kind: str | None = None) -> dict[str, Any] | None:
    asset_id = str(getattr(args, "asset_id", "") or "").strip()
    if not asset_id:
        return None
    store = backup_registry()
    asset = store.get("backup_asset", asset_id)
    if asset is None:
        raise registry_store.RegistryError(f"backup asset does not exist: {asset_id}")
    actual_kind = str(asset.get("kind") or "")
    if expected_kind and actual_kind != expected_kind:
        raise registry_store.RegistryError(
            f"备份资产类型不匹配：需要 {expected_kind}，实际为 {actual_kind}"
        )
    if str(asset.get("status") or "active") not in {"", "active"}:
        raise registry_store.RegistryError(f"备份资产不可用于创建备份：{asset_id}")
    return asset


def repository_for(
    args: argparse.Namespace,
    asset: dict[str, Any] | None = None,
    *,
    create: bool,
) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None]:
    store = backup_registry()
    repository_record: dict[str, Any] | None = None
    if asset is not None:
        repository_id = str(asset.get("repository_id") or "").strip()
        if not repository_id:
            raise registry_store.RegistryError(
                f"备份资产未绑定 repository_id：{asset.get('id', '')}"
            )
        repository_record, registered_path = registered_repository(
            store, repository_id, create=True
        )
        if getattr(args, "repository", ""):
            explicit_path = ensure_repository(args.repository, create=create)
            if explicit_path != registered_path:
                raise ValueError(
                    f"--repository 与备份资产绑定仓库不一致：{explicit_path} != {registered_path}"
                )
            return explicit_path, asset, repository_record
        return registered_path, asset, repository_record
    if getattr(args, "repository", ""):
        return ensure_repository(args.repository, create=create), None, None
    repository_record, repository_path = registered_repository(store, create=create)
    return repository_path, None, repository_record


def asset_manifest_fields(asset: dict[str, Any], repository: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset.get("id", ""),
        "project_id": asset.get("project_id", ""),
        "service_id": asset.get("service_id", ""),
        "environment": asset.get("environment", ""),
        "repository_id": repository.get("id", ""),
    }


def normalize_asset_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for item in str(value).split(","):
            item = item.strip()
            if item and item not in result:
                result.append(item)
    return result


def validate_asset_binding(
    store: registry_store.RegistryStore,
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    project = store.get("project", args.project_id)
    if project is None:
        raise registry_store.RegistryError(f"project does not exist: {args.project_id}")
    service = store.get("service", args.service_id)
    if service is None:
        raise registry_store.RegistryError(f"service does not exist: {args.service_id}")
    if service.get("project_id") not in (None, args.project_id):
        raise registry_store.RegistryError(
            f"service does not belong to project: {args.project_id}/{args.service_id}"
        )
    environment = store.get("environment", args.environment)
    if environment is None:
        raise registry_store.RegistryError(f"environment does not exist: {args.environment}")
    repository_id = args.repository_id or ""
    repository, repository_path = registered_repository(
        store, repository_id or None, create=True
    )
    if repository.get("enabled") is False:
        raise registry_store.RegistryError(f"备份仓库已停用：{repository.get('id', '')}")
    if args.kind not in BACKUP_ASSET_KINDS:
        raise ValueError(f"--kind 只能是：{', '.join(sorted(BACKUP_ASSET_KINDS))}")
    if args.kind == "db":
        source = str(args.db_source or "").strip()
        if not source:
            raise ValueError("数据库备份资产必须提供 --db-source")
        if args.ssh_alias or args.remote_path:
            raise ValueError("数据库备份资产不能同时提供 --ssh-alias/--remote-path")
    else:
        alias = str(args.ssh_alias or "").strip()
        remote_path = str(args.remote_path or "").strip()
        if not alias or not remote_path:
            raise ValueError("文件备份资产必须提供 --ssh-alias 和 --remote-path")
        validate_remote_path(remote_path)
        if not remote_path.startswith("/"):
            raise ValueError("文件备份资产的 --remote-path 必须是绝对路径")
        if args.db_source:
            raise ValueError("文件备份资产不能提供 --db-source")
    if args.keep_last < 1:
        raise ValueError("--keep-last 必须大于 0")
    if args.keep_days is not None and args.keep_days < 1:
        raise ValueError("--keep-days 必须大于 0")
    return project, service, environment, {**repository, "resolved_path": str(repository_path)}


def repository_fields(args: argparse.Namespace, path: Path) -> dict[str, Any]:
    return {
        "kind": args.kind,
        "path": str(path),
        "default": bool(args.default),
        "enabled": not args.disabled,
        "description": args.description or "",
        "updated_at": utc_now(),
    }


def handle_repository_register(args: argparse.Namespace) -> int:
    if args.default and args.disabled:
        raise ValueError("停用的仓库不能设为默认")
    raw_path = Path(args.path).expanduser()
    if not raw_path.is_absolute():
        raise ValueError("--path 必须是绝对路径")
    path = raw_path.resolve()
    if args.kind not in BACKUP_REPOSITORY_KINDS:
        raise ValueError(f"--kind 只能是：{', '.join(sorted(BACKUP_REPOSITORY_KINDS))}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"备份仓库不是目录：{path}")
    path.mkdir(parents=True, exist_ok=True)
    store = backup_registry()
    existing = store.get("backup_repository", args.repository_id)
    if existing is not None and not args.upsert:
        raise registry_store.RegistryError(
            f"backup repository already exists: {args.repository_id}"
        )
    document = store.load()
    repositories = document[registry_store.collection_for("backup_repository")]
    record = dict(existing or {})
    record.update(repository_fields(args, path))
    record["id"] = args.repository_id
    repositories[args.repository_id] = record
    if args.default:
        for object_id, item in repositories.items():
            if object_id != args.repository_id:
                item["default"] = False
    committed = store.save(document)
    saved = committed[registry_store.collection_for("backup_repository")][args.repository_id]
    return emit(
        args,
        registry_payload(
            "registry_backup_repository",
            "updated" if existing else "created",
            store,
            object=saved,
            path=str(path),
        ),
    )


def handle_repository_set_default(args: argparse.Namespace) -> int:
    store = backup_registry()
    target = store.get("backup_repository", args.repository_id)
    if target is None:
        raise registry_store.RegistryError(
            f"backup repository does not exist: {args.repository_id}"
        )
    if target.get("enabled") is False:
        raise registry_store.RegistryError("停用的备份仓库不能设为默认")
    document = store.load()
    repositories = document[registry_store.collection_for("backup_repository")]
    for object_id, item in repositories.items():
        item["default"] = object_id == args.repository_id
    committed = store.save(document)
    saved = committed[registry_store.collection_for("backup_repository")][args.repository_id]
    return emit(args, registry_payload("registry_backup_repository", "updated", store, object=saved))


def handle_repository_list(args: argparse.Namespace) -> int:
    store = backup_registry()
    objects = []
    for item in store.list("backup_repository"):
        raw_path = str(item.get("path") or "").strip()
        path = Path(raw_path).expanduser() if raw_path else None
        objects.append(
            {
                **item,
                "available": bool(path and path.is_absolute() and path.is_dir()),
                "resolved_path": str(path.resolve()) if path and path.is_absolute() else "",
            }
        )
    return emit(args, registry_payload("registry_list", "ok", store, object_kind="backup_repository", objects=objects))


def handle_repository_inspect(args: argparse.Namespace) -> int:
    store = backup_registry()
    repository = store.get("backup_repository", args.repository_id)
    if repository is None:
        raise registry_store.RegistryError(
            f"backup repository does not exist: {args.repository_id}"
        )
    path = Path(str(repository.get("path") or "")).expanduser()
    return emit(
        args,
        registry_payload(
            "registry_backup_repository",
            "ok",
            store,
            object={
                **repository,
                "available": path.is_dir() if path.is_absolute() else False,
            },
        ),
    )


def handle_asset_register(args: argparse.Namespace) -> int:
    store = backup_registry()
    project, service, environment, repository = validate_asset_binding(store, args)
    asset_id = args.asset_id
    existing = store.get("backup_asset", asset_id)
    if existing is not None:
        if not args.upsert:
            raise registry_store.RegistryError(f"backup asset already exists: {asset_id}")
        if existing.get("kind") not in (None, args.kind):
            raise registry_store.RegistryError(f"备份资产类型不能变更：{asset_id}")
    fields: dict[str, Any] = {
        "kind": args.kind,
        "project_id": args.project_id,
        "service_id": args.service_id,
        "environment": args.environment,
        "repository_id": repository["id"],
        "keep_last": args.keep_last,
        "keep_days": args.keep_days,
        "restore_verify": normalize_asset_values(args.restore_verify),
        "status": "active",
        "updated_at": utc_now(),
    }
    if args.kind == "db":
        fields["db_source"] = str(args.db_source).strip()
        fields.pop("ssh_alias", None)
        fields.pop("remote_path", None)
    else:
        fields["ssh_alias"] = str(args.ssh_alias).strip()
        fields["remote_path"] = str(args.remote_path).strip()
        fields.pop("db_source", None)
    asset = store.upsert("backup_asset", asset_id, fields) if existing else store.create(
        "backup_asset", asset_id, fields
    )
    return emit(
        args,
        registry_payload(
            "registry_backup_asset",
            "updated" if existing else "created",
            store,
            object=asset,
            project=project,
            service=service,
            environment=environment,
            repository=repository,
        ),
    )


def handle_asset_list(args: argparse.Namespace) -> int:
    store = backup_registry()
    objects = store.list("backup_asset")
    for field in ("project_id", "service_id", "environment", "kind"):
        value = getattr(args, field, "")
        if value:
            objects = [item for item in objects if str(item.get(field) or "") == value]
    return emit(args, registry_payload("registry_list", "ok", store, object_kind="backup_asset", objects=objects))


def handle_asset_inspect(args: argparse.Namespace) -> int:
    store = backup_registry()
    asset = store.get("backup_asset", args.asset_id)
    if asset is None:
        raise registry_store.RegistryError(f"backup asset does not exist: {args.asset_id}")
    repository = store.get("backup_repository", str(asset.get("repository_id") or ""))
    return emit(
        args,
        registry_payload(
            "registry_backup_asset",
            "ok",
            store,
            object=asset,
            repository=repository,
        ),
    )


def validate_backup_id(value: str) -> str:
    if not BACKUP_ID_RE.fullmatch(value):
        raise ValueError("backup_id 只能包含字母、数字、点、下划线和短横线")
    return value


def new_backup_id(kind: str) -> str:
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{kind}-{stamp}-{uuid.uuid4().hex[:8]}"


def manifest_file(repository: Path, backup_id: str) -> Path:
    return repository / f"{validate_backup_id(backup_id)}{MANIFEST_SUFFIX}"


def artifact_file(repository: Path, manifest: dict[str, Any]) -> Path:
    raw = str(manifest.get("path", ""))
    if not raw:
        raise ValueError("manifest 缺少 path")
    path = Path(raw).expanduser()
    return path if path.is_absolute() else repository / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def load_manifest(repository: Path, backup_id: str) -> dict[str, Any]:
    path = manifest_file(repository, backup_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"备份 manifest 不存在：{backup_id}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(
            f"备份 manifest 损坏：{backup_id}",
            {"manifest": str(path), "reason": str(exc)},
        ) from exc
    if not isinstance(payload, dict):
        raise BackupError(f"备份 manifest 不是 JSON 对象：{backup_id}", {"manifest": str(path)})
    if str(payload.get("backup_id", "")) != backup_id:
        raise BackupError(
            f"备份 manifest 的 backup_id 不匹配：{backup_id}",
            {"manifest": str(path)},
        )
    return payload


def iter_manifests(repository: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    manifests: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for path in sorted(repository.glob(f"*{MANIFEST_SUFFIX}")):
        backup_id = path.name[: -len(MANIFEST_SUFFIX)]
        try:
            manifests.append(load_manifest(repository, backup_id))
        except (BackupError, FileNotFoundError, ValueError) as exc:
            warnings.append({"manifest": str(path), "error": str(exc)})
    return manifests, warnings


def run_json_command(argv: list[str], timeout: int) -> dict[str, Any]:
    executable = argv[0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {
            "transport": "unavailable",
            "error": f"找不到命令：{executable}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "process_exit_code": None,
        }
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout + 10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "transport": "unavailable",
            "error": "命令执行超时",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "process_exit_code": None,
        }
    except OSError as exc:
        return {
            "transport": "unavailable",
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "process_exit_code": None,
        }

    raw = completed.stdout.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {
            "transport": "unavailable",
            "error": trim_error(completed.stderr) or "命令未返回 JSON 结果",
            "stdout": completed.stdout,
            "stderr": trim_error(completed.stderr),
            "exit_code": completed.returncode,
            "process_exit_code": completed.returncode,
        }
    if not isinstance(payload, dict):
        return {
            "transport": "unavailable",
            "error": "命令返回了非对象 JSON",
            "stdout": completed.stdout,
            "stderr": trim_error(completed.stderr),
            "exit_code": completed.returncode,
            "process_exit_code": completed.returncode,
        }
    return {
        "transport": "ok",
        "error": "",
        "stdout": completed.stdout,
        "stderr": trim_error(completed.stderr),
        "exit_code": payload.get("exit_code", completed.returncode),
        "process_exit_code": completed.returncode,
        "payload": payload,
    }


def external_failure(result: dict[str, Any], label: str) -> str:
    if result.get("transport") != "ok":
        return str(result.get("error") or f"{label} 不可用")
    payload = result.get("payload", {})
    if isinstance(payload, dict) and payload.get("error"):
        return trim_error(payload.get("error"))
    if result.get("process_exit_code") not in (None, 0):
        return trim_error(result.get("stderr")) or f"{label} 执行失败"
    return ""


def payload_of(result: dict[str, Any], label: str) -> dict[str, Any]:
    error = external_failure(result, label)
    if error:
        payload = result.get("payload")
        details: dict[str, Any] = {"dependency": label}
        if isinstance(payload, dict):
            details["result"] = payload
            if isinstance(payload.get("transaction"), dict):
                details["transaction"] = payload["transaction"]
        raise BackupError(error, details)
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise BackupError(f"{label} 未返回对象 JSON", {"dependency": label})
    return payload


def run_dbx_export(args: argparse.Namespace, output: Path) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_DBX", "dbx")
    argv = [executable, "export", str(output), "--source", args.source, "--root", args.root]
    if args.tables:
        argv.extend(["--tables", args.tables])
    if args.schema:
        argv.extend(["--schema", args.schema])
    if args.schema_only:
        argv.append("--schema-only")
    if args.data_only:
        argv.append("--data-only")
    if args.include_drop:
        argv.append("--include-drop")
    argv.extend(["--batch-size", str(args.batch_size)])
    return payload_of(run_json_command(argv, args.timeout), "dbx export")


def run_dbx_import(args: argparse.Namespace, sql_file: Path) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_DBX", "dbx")
    argv = [
        executable,
        "import",
        str(sql_file),
        "--source",
        args.source,
        "--root",
        args.root,
        "--transaction",
        "commit",
    ]
    return payload_of(run_json_command(argv, args.timeout), "dbx import")


def run_dbx_query(args: argparse.Namespace, sql: str) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_DBX", "dbx")
    argv = [
        executable,
        "query",
        sql,
        "--source",
        args.source,
        "--root",
        args.root,
        "--json",
    ]
    return payload_of(run_json_command(argv, args.timeout), "dbx query")


def run_ssh_run(args: argparse.Namespace, alias: str, command: str) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_SSHX", "sshx")
    argv = [executable, "run", alias, "--timeout", str(args.timeout), "--json", "--", command]
    return payload_of(run_json_command(argv, args.timeout), "sshx run")


def run_ssh_wait(args: argparse.Namespace, alias: str, job_id: str) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_SSHX", "sshx")
    argv = [
        executable,
        "wait",
        alias,
        job_id,
        "--tail",
        "200",
        "--timeout",
        str(args.timeout),
        "--json",
    ]
    result = run_json_command(argv, args.timeout)
    if result.get("transport") != "ok":
        raise BackupError(str(result.get("error") or "sshx wait 不可用"), {"dependency": "sshx wait"})
    payload = result.get("payload")
    if not isinstance(payload, dict):
        raise BackupError("sshx wait 未返回对象 JSON", {"dependency": "sshx wait"})
    # wait 在远程任务本身退出非零时也会以非零退出，但 JSON 中仍保留
    # job 状态和退出码；这里必须把该结果交给上层区分“任务失败”和“SSH 不可用”。
    if payload.get("error") and not isinstance(payload.get("status"), dict):
        raise BackupError(trim_error(payload.get("error")), {"dependency": "sshx wait", "result": payload})
    return payload


def run_ssh_get(args: argparse.Namespace, alias: str, remote: str, local: Path) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_SSHX", "sshx")
    argv = [
        executable,
        "get",
        alias,
        remote,
        str(local),
        "--timeout",
        str(args.timeout),
        "--json",
    ]
    return payload_of(run_json_command(argv, args.timeout), "sshx get")


def run_ssh_put(args: argparse.Namespace, alias: str, local: Path, remote: str) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_SSHX", "sshx")
    argv = [
        executable,
        "put",
        alias,
        str(local),
        remote,
        "--timeout",
        str(args.timeout),
        "--json",
    ]
    return payload_of(run_json_command(argv, args.timeout), "sshx put")


def run_ssh_exec(args: argparse.Namespace, alias: str, command: str) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_SSHX", "sshx")
    argv = [
        executable,
        "exec",
        "--json",
        "--timeout",
        str(args.timeout),
        "--max-bytes",
        str(REMOTE_OUTPUT_BYTES),
        alias,
        command,
    ]
    result = run_json_command(argv, args.timeout)
    payload = payload_of(result, "sshx exec")
    try:
        exit_code = int(payload.get("exit_code", result.get("process_exit_code", 0)) or 0)
    except (TypeError, ValueError):
        exit_code = 1
    if exit_code != 0:
        raise BackupError(
            trim_error(payload.get("stderr")) or "远端命令执行失败",
            {"dependency": "sshx exec", "remote": payload},
        )
    return payload


def run_host_health(args: argparse.Namespace, alias: str, checks: list[str]) -> dict[str, Any]:
    executable = os.environ.get("BACKUPX_HOSTX", "hostx")
    argv = [executable, "health", alias]
    for check in checks:
        argv.extend(["--check", check])
    argv.extend(["--timeout", str(args.timeout), "--json"])
    result = run_json_command(argv, args.timeout)
    payload = payload_of(result, "hostx health")
    status = str(payload.get("status", "unknown"))
    if status not in {"pass", "ok"}:
        raise BackupError(
            str(payload.get("error") or f"主机健康检查状态为 {status}"),
            {"dependency": "hostx health", "health": payload},
        )
    return payload


def archive_info(path: Path) -> tuple[int, str]:
    count = 0
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isdir():
                count += 1
    return count, sha256_file(path)


def common_manifest(
    *,
    backup_id: str,
    kind: str,
    source_ref: str,
    created_at: str,
    format_name: str,
    path: str,
    artifact: Path,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "backup_id": backup_id,
        "kind": kind,
        "source_ref": source_ref,
        "created_at": created_at,
        "format": format_name,
        "path": path,
        "bytes": artifact.stat().st_size,
        "sha256": sha256_file(artifact),
        "status": "created",
        "tool_version": tool_version(),
    }


def verify_manifest(
    repository: Path,
    manifest: dict[str, Any],
    *,
    update_status: bool,
) -> dict[str, Any]:
    artifact = artifact_file(repository, manifest)
    checks: list[dict[str, Any]] = []
    valid = True

    exists = artifact.is_file()
    checks.append({"name": "exists", "status": "pass" if exists else "fail", "path": str(artifact)})
    if not exists:
        valid = False
    else:
        actual_bytes = artifact.stat().st_size
        expected_bytes = manifest.get("bytes")
        size_ok = expected_bytes in (None, actual_bytes)
        checks.append({
            "name": "bytes",
            "status": "pass" if size_ok else "fail",
            "expected": expected_bytes,
            "actual": actual_bytes,
        })
        valid = valid and size_ok

        actual_sha256 = sha256_file(artifact)
        expected_sha256 = str(manifest.get("sha256") or "")
        sha_ok = bool(expected_sha256) and actual_sha256 == expected_sha256
        checks.append({
            "name": "sha256",
            "status": "pass" if sha_ok else "fail",
            "expected": expected_sha256,
            "actual": actual_sha256,
        })
        valid = valid and sha_ok

        if manifest.get("kind") == "file":
            try:
                count, _ = archive_info(artifact)
                expected_count = manifest.get("file_count")
                count_ok = expected_count in (None, count)
                checks.append({
                    "name": "archive_readable",
                    "status": "pass" if count_ok else "fail",
                    "file_count": count,
                    "expected_file_count": expected_count,
                })
                valid = valid and count_ok
            except (OSError, tarfile.TarError) as exc:
                checks.append({"name": "archive_readable", "status": "fail", "error": str(exc)})
                valid = False

    result_status = "passed" if valid else "failed"
    if update_status:
        updated = dict(manifest)
        updated["status"] = "verified" if valid else "invalid"
        atomic_write_json(manifest_file(repository, str(manifest["backup_id"])), updated)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "backup_verification",
        "status": result_status,
        "backup_id": manifest.get("backup_id", ""),
        "manifest_status": "verified" if valid else "invalid",
        "checks": checks,
        "path": str(artifact),
    }


def db_metadata(payload: dict[str, Any], source: str) -> dict[str, Any]:
    datasource = payload.get("datasource")
    if not isinstance(datasource, dict):
        datasource = {}
    return {
        "db_type": datasource.get("db_type") or datasource.get("engine") or payload.get("db_type") or "",
        "database": datasource.get("database") or payload.get("database") or "",
        "version": datasource.get("version") or payload.get("version") or "",
        "source_profile": datasource.get("profile") or payload.get("profile") or "",
        "source_ref": source,
    }


def handle_db_create(args: argparse.Namespace) -> int:
    asset = backup_asset(args, "db")
    repository, asset, repository_record = repository_for(args, asset, create=True)
    if asset is not None:
        asset_source = str(asset.get("db_source") or "").strip()
        if args.source and args.source != asset_source:
            raise ValueError("--source 与备份资产绑定的 db_source 不一致")
        args.source = asset_source
    if not str(args.source or "").strip():
        raise ValueError("数据库备份必须提供 --source，或使用绑定了 db_source 的 --asset-id")
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".backupx-db-", suffix=".sql", dir=repository)
    os.close(temporary_fd)
    temporary = Path(temporary_name)
    final: Path | None = None
    moved = False
    try:
        payload = run_dbx_export(args, temporary)
        if not temporary.is_file():
            raise BackupError(
                "dbx export 未生成 SQL 文件",
                {"stage": "export", "dbx": payload},
            )
        backup_id = new_backup_id("db")
        final = repository / f"{backup_id}.sql"
        manifest = common_manifest(
            backup_id=backup_id,
            kind="db",
            source_ref=args.source,
            created_at=utc_now(),
            format_name="sql",
            path=final.name,
            artifact=temporary,
        )
        manifest.update(db_metadata(payload, args.source))
        if asset is not None and repository_record is not None:
            manifest.update(asset_manifest_fields(asset, repository_record))
        os.replace(temporary, final)
        moved = True
        atomic_write_json(manifest_file(repository, backup_id), manifest)
        return emit(args, {"schema_version": SCHEMA_VERSION, "kind": "backup_create", "status": "created", "manifest": manifest})
    except BackupError as exc:
        raise BackupError(str(exc), {"kind": "db", "stage": "export", **exc.details}) from exc
    except (OSError, ValueError) as exc:
        raise BackupError(str(exc), {"kind": "db", "stage": "repository"}) from exc
    finally:
        if final is not None and moved and not manifest_file(repository, final.stem).exists():
            try:
                final.unlink()
            except FileNotFoundError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def validate_remote_path(value: str) -> str:
    if not value.strip() or "\n" in value or "\t" in value:
        raise ValueError("远端路径不能为空，且不能包含换行或制表符")
    return value


def validate_target_dir(value: str) -> str:
    if not value.startswith("/") or "\n" in value or "\t" in value:
        raise ValueError("--target-dir 必须是明确的绝对远端目录")
    return value.rstrip("/") or "/"


def handle_file_create(args: argparse.Namespace) -> int:
    asset = backup_asset(args, "file")
    repository, asset, repository_record = repository_for(args, asset, create=True)
    if asset is not None:
        asset_alias = str(asset.get("ssh_alias") or "").strip()
        asset_remote_path = str(asset.get("remote_path") or "").strip()
        if args.alias and args.alias != asset_alias:
            raise ValueError("位置参数 alias 与备份资产绑定的 ssh_alias 不一致")
        if args.remote_path and args.remote_path != asset_remote_path:
            raise ValueError("位置参数 remote_path 与备份资产绑定的 remote_path 不一致")
        args.alias = asset_alias
        args.remote_path = asset_remote_path
    if not args.alias or not args.remote_path:
        raise ValueError("文件备份必须提供 alias 和 remote_path，或使用 --asset-id")
    remote_path = validate_remote_path(args.remote_path)
    backup_id = new_backup_id("file")
    remote_archive = f"/tmp/agent-ops-backup-{uuid.uuid4().hex}.tar.gz"
    command = (
        "set -eu; "
        f"source={shlex.quote(remote_path)}; "
        f"archive={shlex.quote(remote_archive)}; "
        "test -e \"$source\"; "
        "if [ -d \"$source\" ]; then "
        "  (cd \"$source\" && tar -czf \"$archive\" .); "
        "else "
        "  parent=$(dirname \"$source\"); base=$(basename \"$source\"); "
        "  (cd \"$parent\" && tar -czf \"$archive\" \"$base\"); "
        "fi"
    )
    job_id = ""
    temporary = repository / f".{backup_id}.download"
    final = repository / f"{backup_id}.tar.gz"
    moved = False
    try:
        try:
            run_payload = run_ssh_run(args, args.alias, command)
        except BackupError as exc:
            raise BackupError(str(exc), {"stage": "remote_create", **exc.details}) from exc
        job_id = str(run_payload.get("job_id", ""))
        if not job_id:
            raise BackupError("sshx run 未返回 job_id", {"stage": "remote_create"})
        try:
            wait_payload = run_ssh_wait(args, args.alias, job_id)
        except BackupError as exc:
            raise BackupError(str(exc), {"stage": "remote_job", "remote_job_id": job_id, **exc.details}) from exc
        status = wait_payload.get("status")
        if not isinstance(status, dict):
            status = {}
        state = str(status.get("state", "unknown"))
        try:
            exit_code = int(status.get("exit_code") or 0)
        except (TypeError, ValueError):
            exit_code = 1
        if state != "finished" or exit_code != 0:
            raise BackupError(
                "远端归档任务失败",
                {
                    "stage": "remote_job",
                    "remote_job_id": job_id,
                    "job_status": status,
                    "stdout": trim_error(wait_payload.get("stdout")),
                    "stderr": trim_error(wait_payload.get("stderr")),
                },
            )

        try:
            get_payload = run_ssh_get(args, args.alias, remote_archive, temporary)
        except BackupError as exc:
            raise BackupError(str(exc), {"stage": "download", "remote_job_id": job_id, **exc.details}) from exc
        if not temporary.is_file():
            raise BackupError(
                "sshx get 未在本地生成归档文件",
                {"stage": "download", "remote_job_id": job_id, "get": get_payload},
            )
        try:
            file_count, digest = archive_info(temporary)
        except (OSError, tarfile.TarError) as exc:
            raise BackupError(
                f"下载的归档不可读取：{exc}",
                {"stage": "archive_verify", "remote_job_id": job_id},
            ) from exc
        manifest = common_manifest(
            backup_id=backup_id,
            kind="file",
            source_ref=remote_path,
            created_at=utc_now(),
            format_name="tar.gz",
            path=final.name,
            artifact=temporary,
        )
        manifest.update({
            "sha256": digest,
            "ssh_profile": args.alias,
            "remote_path": remote_path,
            "file_count": file_count,
            "remote_job_id": job_id,
        })
        if asset is not None and repository_record is not None:
            manifest.update(asset_manifest_fields(asset, repository_record))
        os.replace(temporary, final)
        moved = True
        atomic_write_json(manifest_file(repository, backup_id), manifest)
        return emit(args, {"schema_version": SCHEMA_VERSION, "kind": "backup_create", "status": "created", "manifest": manifest})
    except BackupError as exc:
        raise BackupError(str(exc), {"kind": "file", **exc.details}) from exc
    except (OSError, ValueError) as exc:
        raise BackupError(str(exc), {"kind": "file", "stage": "repository"}) from exc
    finally:
        if job_id:
            try:
                run_ssh_exec(args, args.alias, f"rm -f -- {shlex.quote(remote_archive)}")
            except BackupError:
                pass
        if moved and not manifest_file(repository, backup_id).exists():
            try:
                final.unlink()
            except FileNotFoundError:
                pass
        for path in (temporary,):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def handle_list(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    manifests, warnings = iter_manifests(repository)
    if args.kind:
        manifests = [item for item in manifests if item.get("kind") == args.kind]
    manifests.sort(key=lambda item: (str(item.get("created_at", "")), str(item.get("backup_id", ""))), reverse=True)
    backups = []
    for item in manifests:
        backups.append({
            "backup_id": item.get("backup_id", ""),
            "kind": item.get("kind", ""),
            "source_ref": item.get("source_ref", ""),
            "created_at": item.get("created_at", ""),
            "format": item.get("format", ""),
            "path": item.get("path", ""),
            "bytes": item.get("bytes"),
            "sha256": item.get("sha256", ""),
            "status": item.get("status", "unknown"),
        })
    status = "partial" if warnings else "ok"
    return emit(args, {
        "schema_version": SCHEMA_VERSION,
        "kind": "backup_list",
        "status": status,
        "repository": str(repository),
        "backups": backups,
        "warnings": warnings,
    })


def handle_inspect(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    manifest = load_manifest(repository, args.backup_id)
    return emit(args, {
        "schema_version": SCHEMA_VERSION,
        "kind": "backup_inspect",
        "status": "ok",
        "repository": str(repository),
        "manifest": manifest,
    })


def handle_verify(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    manifest = load_manifest(repository, args.backup_id)
    result = verify_manifest(repository, manifest, update_status=not args.read_only)
    result["repository"] = str(repository)
    result["manifest_updated"] = not args.read_only
    return emit(args, result, 0 if result["status"] == "passed" else 1)


def restore_failure(
    *,
    backup_id: str,
    target: dict[str, Any],
    stages: list[dict[str, Any]],
    verification: dict[str, Any],
    error: str,
    transaction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "restore_result",
        "status": "failed",
        "backup_id": backup_id,
        "target": target,
        "stages": stages,
        "transaction": transaction,
        "verification": verification,
        "error": error,
        "started_at": utc_now(),
        "finished_at": utc_now(),
    }


def handle_restore(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    manifest = load_manifest(repository, args.backup_id)
    kind = str(manifest.get("kind", ""))
    verification = verify_manifest(repository, manifest, update_status=True)
    target: dict[str, Any]
    if kind == "db":
        if not args.source:
            raise ValueError("数据库恢复必须显式指定 --source")
        target = {"kind": "db", "source": args.source}
    elif kind == "file":
        if not args.alias:
            raise ValueError("文件恢复必须显式指定 SSH profile")
        target = {"kind": "file", "ssh_profile": args.alias, "target_dir": validate_target_dir(args.target_dir or "")}
    else:
        raise ValueError(f"不支持的备份类型：{kind}")

    stages: list[dict[str, Any]] = [{"name": "verified", "status": verification["status"]}]
    if verification["status"] != "passed":
        return emit(args, restore_failure(
            backup_id=args.backup_id,
            target=target,
            stages=stages,
            verification=verification,
            error="备份校验未通过，未执行恢复",
        ), 1)

    artifact = artifact_file(repository, manifest)
    if kind == "db":
        try:
            payload = run_dbx_import(args, artifact)
        except BackupError as exc:
            stages.append({"name": "restored", "status": "failed"})
            transaction = exc.details.get("transaction") if isinstance(exc.details, dict) else None
            result = restore_failure(
                backup_id=args.backup_id,
                target=target,
                stages=stages,
                verification=verification,
                error=str(exc),
                transaction=transaction if isinstance(transaction, dict) else None,
            )
            return emit(args, result, 1)
        transaction = payload.get("transaction")
        if not isinstance(transaction, dict):
            transaction = {"mode": "commit", "state": "unknown", "status": "not_returned"}
        committed = str(transaction.get("state", "")) == "committed"
        stages.append({"name": "restored", "status": "passed" if committed or not payload.get("transaction") else "failed"})
        success = stages[-1]["status"] == "passed"
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": "restore_result",
            "status": "passed" if success else "failed",
            "backup_id": args.backup_id,
            "target": target,
            "stages": stages,
            "transaction": transaction,
            "verification": verification,
            "dbx": {key: value for key, value in payload.items() if key != "rows"},
            "started_at": utc_now(),
            "finished_at": utc_now(),
        }
        return emit(args, result, 0 if success else 1)

    remote_archive = f"/tmp/agent-ops-restore-{uuid.uuid4().hex}.tar.gz"
    try:
        put_payload = run_ssh_put(args, args.alias, artifact, remote_archive)
        stages.append({"name": "uploaded", "status": "passed", "result": put_payload})
        target_dir = target["target_dir"]
        command = (
            "set -eu; "
            f"mkdir -p -- {shlex.quote(target_dir)}; "
            f"tar -xzf {shlex.quote(remote_archive)} -C {shlex.quote(target_dir)}; "
            f"rm -f -- {shlex.quote(remote_archive)}"
        )
        run_ssh_exec(args, args.alias, command)
        stages.append({"name": "restored", "status": "passed"})
        result = {
            "schema_version": SCHEMA_VERSION,
            "kind": "restore_result",
            "status": "passed",
            "backup_id": args.backup_id,
            "target": target,
            "stages": stages,
            "transaction": None,
            "verification": verification,
            "started_at": utc_now(),
            "finished_at": utc_now(),
        }
        return emit(args, result)
    except BackupError as exc:
        stages.append({"name": "restored", "status": "failed"})
        result = restore_failure(
            backup_id=args.backup_id,
            target=target,
            stages=stages,
            verification=verification,
            error=str(exc),
        )
        return emit(args, result, 1)
    finally:
        try:
            run_ssh_exec(args, args.alias, f"rm -f -- {shlex.quote(remote_archive)}")
        except BackupError:
            pass


def read_only_sql(sql: str) -> bool:
    normalized = re.sub(r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*", "", sql, flags=re.DOTALL).lstrip().lower()
    return normalized.startswith(("select", "with", "show", "describe", "desc", "explain", "pragma"))


def query_summary(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    return {
        "columns": payload.get("columns", []),
        "rows": len(rows) if isinstance(rows, list) else None,
        "has_more": bool(payload.get("has_more", False)),
    }


def remote_check_path(target_dir: str, requested: str) -> str:
    if not requested or "\n" in requested or "\t" in requested:
        raise ValueError("--check-path 不能为空，且不能包含换行或制表符")
    if requested.startswith("/"):
        return requested
    return posixpath.join(target_dir, requested)


def handle_restore_verify(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    manifest = load_manifest(repository, args.backup_id)
    kind = str(manifest.get("kind", ""))
    checks: list[dict[str, Any]] = []
    requested = False

    if kind == "db":
        if args.sql:
            requested = True
            if not args.source:
                raise ValueError("数据库恢复验证必须显式指定 --source")
            for sql in args.sql:
                if not read_only_sql(sql):
                    checks.append({"kind": "sql", "status": "failed", "error": "只允许以 SELECT/WITH/SHOW/DESCRIBE/EXPLAIN/PRAGMA 开头的只读 SQL", "sql": sql})
                    continue
                try:
                    payload = run_dbx_query(args, sql)
                    checks.append({"kind": "sql", "status": "passed", "sql": sql, "result": query_summary(payload)})
                except BackupError as exc:
                    checks.append({"kind": "sql", "status": "failed", "sql": sql, "error": str(exc)})
    elif kind == "file":
        requested = bool(args.check_path or args.health_check)
        if requested and not args.alias:
            raise ValueError("文件恢复验证必须显式指定 SSH profile")
        if args.check_path and not args.target_dir:
            raise ValueError("使用 --check-path 时必须显式指定 --target-dir")
        target_dir = validate_target_dir(args.target_dir) if args.target_dir else ""
        for requested_path in args.check_path:
            remote_path = remote_check_path(target_dir, requested_path)
            try:
                run_ssh_exec(args, args.alias, f"test -e -- {shlex.quote(remote_path)}")
                checks.append({"kind": "file", "status": "passed", "path": remote_path})
            except (BackupError, ValueError) as exc:
                checks.append({"kind": "file", "status": "failed", "path": remote_path, "error": str(exc)})
        if args.health_check:
            try:
                health = run_host_health(args, args.alias, args.health_check)
                checks.append({"kind": "health", "status": "passed", "result": {"status": health.get("status"), "checks": health.get("checks", [])}})
            except BackupError as exc:
                checks.append({"kind": "health", "status": "failed", "error": str(exc)})
    else:
        raise ValueError(f"不支持的备份类型：{kind}")

    if not requested:
        status = "not_requested"
    else:
        status = "passed" if checks and all(item.get("status") == "passed" for item in checks) else "failed"
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "restore_verification",
        "status": status,
        "backup_id": args.backup_id,
        "target": ({"kind": "db", "source": args.source} if kind == "db" else {"kind": "file", "ssh_profile": args.alias, "target_dir": args.target_dir or ""}),
        "checks": checks,
    }
    return emit(args, result, 0 if status != "failed" else 1)


def manifest_sort_key(item: dict[str, Any]) -> tuple[str, str]:
    return str(item.get("created_at", "")), str(item.get("backup_id", ""))


def handle_prune(args: argparse.Namespace) -> int:
    repository, _, _ = repository_for(args, create=False)
    if args.keep_last < 0:
        raise ValueError("--keep-last 不能小于 0")
    if args.keep_days is not None and args.keep_days <= 0:
        raise ValueError("--keep-days 必须大于 0")
    manifests, warnings = iter_manifests(repository)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in manifests:
        grouped.setdefault(str(item.get("kind", "unknown")), []).append(item)
    keep: set[str] = set()
    cutoff: dt.datetime | None = None
    if args.keep_days is not None:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=args.keep_days)
    for items in grouped.values():
        items.sort(key=manifest_sort_key, reverse=True)
        for index, item in enumerate(items):
            backup_id = str(item.get("backup_id", ""))
            if index < args.keep_last:
                keep.add(backup_id)
                continue
            if cutoff is not None:
                try:
                    created = dt.datetime.fromisoformat(str(item.get("created_at", "")).replace("Z", "+00:00"))
                except ValueError:
                    created = None
                if created is not None and created >= cutoff:
                    keep.add(backup_id)
    candidates = [item for item in manifests if str(item.get("backup_id", "")) not in keep]
    deleted: list[dict[str, Any]] = []
    if args.confirm:
        for item in candidates:
            backup_id = str(item.get("backup_id", ""))
            data_path = artifact_file(repository, item)
            manifest_path = manifest_file(repository, backup_id)
            try:
                data_path.unlink()
            except FileNotFoundError:
                pass
            try:
                manifest_path.unlink()
            except FileNotFoundError:
                pass
            deleted.append({"backup_id": backup_id, "path": str(data_path)})
        status = "pruned"
    else:
        status = "dry_run"
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "backup_prune",
        "status": status,
        "repository": str(repository),
        "keep_last": args.keep_last,
        "keep_days": args.keep_days,
        "kept": sorted(keep),
        "candidates": [str(item.get("backup_id", "")) for item in candidates],
        "deleted": deleted,
        "warnings": warnings,
    }
    return emit(args, result)


def add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def add_repository_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repository",
        default="",
        help="本地备份仓库目录；省略时使用登记的默认仓库",
    )


def add_asset_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--asset-id",
        "--asset",
        dest="asset_id",
        default="",
        help="已登记的 backup asset；create 时可从资产补全来源和仓库",
    )


def add_dependency_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(parser)


def add_db_options(parser: argparse.ArgumentParser, *, source_required: bool) -> None:
    parser.add_argument("--source", required=source_required, default="", help="dbx source、alias 或 profile")
    parser.add_argument("--root", default=str(Path.cwd()), help="dbx 项目根目录")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="本地数据库与文件备份客户端。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db = subparsers.add_parser("db", help="数据库备份")
    db_commands = db.add_subparsers(dest="db_command", required=True)
    db_create = db_commands.add_parser("create")
    add_repository_arg(db_create)
    add_asset_arg(db_create)
    add_db_options(db_create, source_required=False)
    db_create.add_argument("--tables", default="")
    db_create.add_argument("--schema", default="public")
    db_create.add_argument("--schema-only", action="store_true")
    db_create.add_argument("--data-only", action="store_true")
    db_create.add_argument("--include-drop", action="store_true")
    db_create.add_argument("--batch-size", type=int, default=200)
    add_dependency_args(db_create)
    db_create.set_defaults(func=handle_db_create, result_kind="backup_create")

    file_parser = subparsers.add_parser("file", help="文件备份")
    file_commands = file_parser.add_subparsers(dest="file_command", required=True)
    file_create = file_commands.add_parser("create")
    file_create.add_argument("alias", nargs="?", help="SSH profile alias")
    file_create.add_argument("remote_path", nargs="?", help="远端文件或目录")
    add_repository_arg(file_create)
    add_asset_arg(file_create)
    add_dependency_args(file_create)
    file_create.set_defaults(func=handle_file_create, result_kind="backup_create")

    list_parser = subparsers.add_parser("list", help="列出备份")
    add_repository_arg(list_parser)
    list_parser.add_argument("--kind", choices=["db", "file"])
    add_json_arg(list_parser)
    list_parser.set_defaults(func=handle_list, result_kind="backup_list")

    inspect_parser = subparsers.add_parser("inspect", help="查看 manifest")
    inspect_parser.add_argument("backup_id")
    add_repository_arg(inspect_parser)
    add_json_arg(inspect_parser)
    inspect_parser.set_defaults(func=handle_inspect, result_kind="backup_inspect")

    verify_parser = subparsers.add_parser("verify", help="校验备份")
    verify_parser.add_argument("backup_id")
    add_repository_arg(verify_parser)
    verify_parser.add_argument("--read-only", action="store_true", help="只校验，不更新 manifest 状态")
    add_json_arg(verify_parser)
    verify_parser.set_defaults(func=handle_verify, result_kind="backup_verification")

    restore_parser = subparsers.add_parser("restore", help="恢复数据库或文件备份")
    restore_parser.add_argument("backup_id")
    add_repository_arg(restore_parser)
    restore_parser.add_argument("--source", default="", help="数据库恢复目标 dbx source")
    restore_parser.add_argument("--alias", default="", help="文件恢复目标 SSH profile")
    restore_parser.add_argument("--target-dir", default="", help="文件恢复目标绝对远端目录")
    restore_parser.add_argument("--root", default=str(Path.cwd()), help="dbx 项目根目录")
    add_dependency_args(restore_parser)
    restore_parser.set_defaults(func=handle_restore, result_kind="restore_result")

    restore_verify_parser = subparsers.add_parser("restore-verify", help="执行用户明确提供的恢复后只读验证")
    restore_verify_parser.add_argument("backup_id")
    add_repository_arg(restore_verify_parser)
    restore_verify_parser.add_argument("--source", default="", help="数据库验证目标 dbx source")
    restore_verify_parser.add_argument("--alias", default="", help="文件/主机验证目标 SSH profile")
    restore_verify_parser.add_argument("--target-dir", default="", help="文件验证目标绝对远端目录")
    restore_verify_parser.add_argument("--root", default=str(Path.cwd()), help="dbx 项目根目录")
    restore_verify_parser.add_argument("--sql", action="append", default=[], help="数据库只读验证 SQL，可重复")
    restore_verify_parser.add_argument("--check-path", action="append", default=[], help="文件验证路径，可重复")
    restore_verify_parser.add_argument("--health-check", action="append", default=[], help="主机健康检查项，可重复")
    add_dependency_args(restore_verify_parser)
    restore_verify_parser.set_defaults(func=handle_restore_verify, result_kind="restore_verification")

    prune_parser = subparsers.add_parser("prune", help="按保留策略清理备份")
    add_repository_arg(prune_parser)
    prune_parser.add_argument("--keep-last", type=int, required=True, help="每种备份类型至少保留最近 N 个")
    prune_parser.add_argument("--keep-days", type=int, help="每种备份类型保留最近 D 天")
    mode = prune_parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只列出待清理项（默认）")
    mode.add_argument("--confirm", action="store_true", help="确认删除待清理项")
    add_json_arg(prune_parser)
    prune_parser.set_defaults(func=handle_prune, result_kind="backup_prune")

    repository_parser = subparsers.add_parser("repository", help="登记和查看备份仓库")
    repository_commands = repository_parser.add_subparsers(
        dest="repository_command", required=True
    )
    repository_register = repository_commands.add_parser("register", help="登记本地备份仓库")
    repository_register.add_argument("repository_id")
    repository_register.add_argument("--path", required=True, help="备份仓库绝对路径")
    repository_register.add_argument("--kind", choices=sorted(BACKUP_REPOSITORY_KINDS), default="local")
    repository_register.add_argument("--default", action="store_true", help="设为唯一默认仓库")
    repository_register.add_argument("--disabled", action="store_true", help="登记但停用仓库")
    repository_register.add_argument("--description")
    repository_register.add_argument("--upsert", action="store_true")
    add_json_arg(repository_register)
    repository_register.set_defaults(func=handle_repository_register, result_kind="registry_backup_repository")

    repository_default = repository_commands.add_parser("set-default", help="设置默认备份仓库")
    repository_default.add_argument("repository_id")
    add_json_arg(repository_default)
    repository_default.set_defaults(func=handle_repository_set_default, result_kind="registry_backup_repository")

    repository_default_alias = repository_commands.add_parser("default", help="设置默认备份仓库")
    repository_default_alias.add_argument("repository_id")
    add_json_arg(repository_default_alias)
    repository_default_alias.set_defaults(func=handle_repository_set_default, result_kind="registry_backup_repository")

    repository_list = repository_commands.add_parser("list", help="列出备份仓库")
    add_json_arg(repository_list)
    repository_list.set_defaults(func=handle_repository_list, result_kind="registry_list")

    repository_inspect = repository_commands.add_parser("inspect", help="查看备份仓库")
    repository_inspect.add_argument("repository_id")
    add_json_arg(repository_inspect)
    repository_inspect.set_defaults(func=handle_repository_inspect, result_kind="registry_backup_repository")

    asset_parser = subparsers.add_parser("asset", help="登记和查看备份资产")
    asset_commands = asset_parser.add_subparsers(dest="asset_command", required=True)
    asset_register = asset_commands.add_parser("register", help="登记数据库或文件备份资产")
    asset_register.add_argument("asset_id")
    asset_register.add_argument("--kind", choices=sorted(BACKUP_ASSET_KINDS), required=True)
    asset_register.add_argument("--project-id", "--project", dest="project_id", required=True)
    asset_register.add_argument("--service-id", "--service", dest="service_id", required=True)
    asset_register.add_argument("--environment", required=True)
    asset_register.add_argument("--repository-id", "--repository", dest="repository_id")
    asset_register.add_argument("--db-source", "--source", dest="db_source")
    asset_register.add_argument("--ssh-alias", "--alias", dest="ssh_alias")
    asset_register.add_argument("--remote-path")
    asset_register.add_argument("--keep-last", type=int, default=5)
    asset_register.add_argument("--keep-days", type=int)
    asset_register.add_argument("--restore-verify", action="append")
    asset_register.add_argument("--upsert", action="store_true")
    add_json_arg(asset_register)
    asset_register.set_defaults(func=handle_asset_register, result_kind="registry_backup_asset")

    asset_list = asset_commands.add_parser("list", help="列出备份资产")
    asset_list.add_argument("--project-id", "--project", dest="project_id")
    asset_list.add_argument("--service-id", "--service", dest="service_id")
    asset_list.add_argument("--environment")
    asset_list.add_argument("--kind", choices=sorted(BACKUP_ASSET_KINDS))
    add_json_arg(asset_list)
    asset_list.set_defaults(func=handle_asset_list, result_kind="registry_list")

    asset_inspect = asset_commands.add_parser("inspect", help="查看备份资产")
    asset_inspect.add_argument("asset_id")
    add_json_arg(asset_inspect)
    asset_inspect.set_defaults(func=handle_asset_inspect, result_kind="registry_backup_asset")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except BackupError as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": getattr(args, "result_kind", "backup_error"),
            "status": "failed",
            "error": str(exc),
            **exc.details,
        }
        return emit(args, payload, 1)
    except Exception as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": getattr(args, "result_kind", "backup_error"),
            "status": "failed",
            "error": str(exc),
        }
        return emit(args, payload, 1)


if __name__ == "__main__":
    sys.exit(main())
