#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import registry_store


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT = 120
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COMPONENTS = ("host", "ssh", "db", "deploy", "backup")
REGISTRY_COMPONENT = "registry"
COLLECTOR_STATUSES = {"collected", "failed", "unavailable", "skipped"}


class IncidentError(RuntimeError):
    pass


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


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


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise IncidentError(f"{label} 不存在：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise IncidentError(f"{label} 无法读取：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise IncidentError(f"{label} 不是 JSON 对象：{path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_case_id(value: str) -> str:
    if not CASE_ID_RE.fullmatch(value):
        raise ValueError("case-id 只能包含字母、数字、点、下划线和短横线")
    return value


def safe_relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not value:
        raise IncidentError(f"bundle 内路径不安全：{value}")
    return path


def prepare_bundle(value: str) -> Path:
    if not value.strip():
        raise ValueError("--out 不能为空")
    bundle = Path(value).expanduser().resolve()
    if bundle.exists() and not bundle.is_dir():
        raise ValueError(f"bundle 输出路径不是目录：{bundle}")
    if bundle.exists() and any(bundle.iterdir()):
        raise ValueError(f"bundle 输出目录非空，为避免覆盖现场资料而停止：{bundle}")
    bundle.mkdir(parents=True, exist_ok=True)
    return bundle


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
            "error": trim_error(completed.stderr) or "依赖命令未返回 JSON 结果",
            "stdout": completed.stdout,
            "stderr": trim_error(completed.stderr),
            "exit_code": completed.returncode,
            "process_exit_code": completed.returncode,
        }
    if not isinstance(payload, dict):
        return {
            "transport": "unavailable",
            "error": "依赖命令返回了非对象 JSON",
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


def command_source(argv: list[str], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "cli",
        "command": argv[0],
        "argv": argv,
        "transport": result.get("transport", "unavailable"),
        "exit_code": result.get("process_exit_code"),
        "stderr": trim_error(result.get("stderr")),
    }


def dependency_error(label: str, result: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    status = "unavailable" if result.get("transport") != "ok" else "failed"
    error = trim_error(result.get("error")) or f"{label} 未返回可用 JSON"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_dependency_error",
        "status": status,
        "dependency": label,
        "error": error,
    }
    if result.get("stderr"):
        payload["stderr"] = trim_error(result.get("stderr"))
    return status, payload, error


def classify_payload(result: dict[str, Any], label: str) -> tuple[str, dict[str, Any], str]:
    if result.get("transport") != "ok":
        return dependency_error(label, result)
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return "unavailable", {
            "schema_version": SCHEMA_VERSION,
            "kind": "incident_dependency_error",
            "status": "unavailable",
            "dependency": label,
            "error": "依赖命令未返回对象 JSON",
        }, "依赖命令未返回对象 JSON"
    if payload.get("connection_status") == "unavailable" or payload.get("status") == "unavailable":
        return "unavailable", payload, trim_error(payload.get("error"))
    if payload.get("error") and result.get("process_exit_code") not in (None, 0) and not payload.get("status"):
        return "failed", payload, trim_error(payload.get("error"))
    return "collected", payload, trim_error(payload.get("error"))


def env_command(name: str, default: str) -> str:
    return os.environ.get(name, default)


class BundleWriter:
    def __init__(self, bundle: Path, case_id: str, started_at: str, required: list[str]) -> None:
        self.bundle = bundle
        self.case_id = case_id
        self.started_at = started_at
        self.required = required
        self.evidence: list[dict[str, Any]] = []
        self.collectors: list[dict[str, Any]] = []
        self._component_numbers: dict[str, int] = {}
        self.registry_context: dict[str, Any] | None = None

    def add_evidence(
        self,
        *,
        component: str,
        target: dict[str, Any],
        operation: str,
        status: str,
        data: dict[str, Any],
        source: dict[str, Any],
        collected_at: str,
        error: str = "",
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        number = self._component_numbers.get(component, 0) + 1
        self._component_numbers[component] = number
        evidence_id = f"{component}-{number}"
        relative = Path("evidence") / component / f"{evidence_id}.json"
        path = self.bundle / relative
        envelope: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kind": "incident_evidence",
            "evidence_id": evidence_id,
            "component": component,
            "target": target,
            "operation": operation,
            "collected_at": collected_at,
            "status": status,
            "redaction": {"status": "not_requested", "fields": []},
            "source": source,
            "data": data,
        }
        if error:
            envelope["error"] = error
        if warnings:
            envelope["warnings"] = warnings
        atomic_write_json(path, envelope)
        item: dict[str, Any] = {
            "evidence_id": evidence_id,
            "component": component,
            "target": target,
            "operation": operation,
            "collected_at": collected_at,
            "status": status,
            "path": relative.as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "redaction": envelope["redaction"],
        }
        if error:
            item["error"] = error
        if warnings:
            item["warnings"] = warnings
        self.evidence.append(item)
        return item

    def add_collector(
        self,
        *,
        name: str,
        target: dict[str, Any],
        operation: str,
        status: str,
        started_at: str,
        finished_at: str,
        evidence_id: str,
        error: str = "",
        warnings: list[str] | None = None,
    ) -> None:
        collector: dict[str, Any] = {
            "name": name,
            "target": target,
            "operation": operation,
            "status": status,
            "required": name in self.required,
            "started_at": started_at,
            "finished_at": finished_at,
            "evidence_ids": [evidence_id],
        }
        if error:
            collector["error"] = error
        if warnings:
            collector["warnings"] = warnings
        self.collectors.append(collector)


def add_command_evidence(
    writer: BundleWriter,
    *,
    component: str,
    target: dict[str, Any],
    operation: str,
    label: str,
    argv: list[str],
    timeout: int,
    data: dict[str, Any] | None = None,
    status: str | None = None,
    error: str = "",
    warnings: list[str] | None = None,
) -> None:
    started_at = utc_now()
    result = run_json_command(argv, timeout)
    if data is None or status is None:
        status, data, classified_error = classify_payload(result, label)
        error = error or classified_error
    finished_at = utc_now()
    collected_at = finished_at
    item = writer.add_evidence(
        component=component,
        target=target,
        operation=operation,
        status=status,
        data=data,
        source=command_source(argv, result),
        collected_at=collected_at,
        error=error,
        warnings=warnings,
    )
    writer.add_collector(
        name=component,
        target=target,
        operation=operation,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        evidence_id=str(item["evidence_id"]),
        error=error,
        warnings=warnings,
    )


def host_command(args: argparse.Namespace) -> list[str]:
    checks = ["facts", "disk:/"]
    if args.service:
        checks.append(f"service:{args.service}")
    if args.log_source:
        checks.append(f"logs:{args.log_source}")
    command = [env_command("INCIDENTX_HOSTX", "hostx"), "health", args.host, "--check", ",".join(checks)]
    if args.since:
        command.extend(["--since", args.since])
    command.extend(["--timeout", str(args.timeout), "--json"])
    return command


def collect_host(writer: BundleWriter, args: argparse.Namespace) -> None:
    add_command_evidence(
        writer,
        component="host",
        target={"alias": args.host},
        operation="host.health",
        label="hostx health",
        argv=host_command(args),
        timeout=args.timeout,
    )


def collect_ssh(writer: BundleWriter, args: argparse.Namespace) -> None:
    add_command_evidence(
        writer,
        component="ssh",
        target={"alias": args.host},
        operation="ssh.status",
        label="sshx status",
        argv=[env_command("INCIDENTX_SSHX", "sshx"), "status", args.host, "--timeout", str(args.timeout), "--json"],
        timeout=args.timeout,
    )


def source_matches(source: str, items: list[Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        aliases = item.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
        if source == str(item.get("profile", "")) or source in {str(alias) for alias in aliases}:
            matches.append(item)
    return matches


def collect_db(writer: BundleWriter, args: argparse.Namespace) -> None:
    component = "db"
    target = {"source": args.source}
    started_at = utc_now()
    argv = [env_command("INCIDENTX_DBX", "dbx"), "source", "list"]
    result = run_json_command(argv, args.timeout)
    status, payload, error = classify_payload(result, "dbx source list")
    warnings: list[str] = []
    if status == "collected":
        rows = payload.get("sources")
        rows = rows if isinstance(rows, list) else []
        matches = source_matches(args.source, rows)
        if not matches:
            status = "failed"
            error = f"dbx source list 中找不到数据源：{args.source}"
            warnings.append("只读 source list 已完成，但未找到请求的数据源")
        data = {
            "schema_version": SCHEMA_VERSION,
            "kind": "incident_db_source",
            "status": "ok" if matches else "failed",
            "requested_source": args.source,
            "source": matches[0] if matches else None,
            "sources": rows,
        }
    else:
        data = payload
    finished_at = utc_now()
    item = writer.add_evidence(
        component=component,
        target=target,
        operation="db.source_list",
        status=status,
        data=data,
        source=command_source(argv, result),
        collected_at=finished_at,
        error=error,
        warnings=warnings,
    )
    writer.add_collector(
        name=component,
        target=target,
        operation="db.source_list",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        evidence_id=str(item["evidence_id"]),
        error=error,
        warnings=warnings,
    )


def collect_deploy(writer: BundleWriter, args: argparse.Namespace) -> None:
    command = [
        env_command("INCIDENTX_DEPLOYX", "deployx"),
        "status",
        args.host,
        "--app",
        args.app,
        "--release-root",
        args.release_root,
    ]
    if args.service:
        command.extend(["--service", args.service])
    command.extend(["--timeout", str(args.timeout), "--json"])
    add_command_evidence(
        writer,
        component="deploy",
        target={"alias": args.host, "app": args.app, "release_root": args.release_root},
        operation="deploy.status",
        label="deployx status",
        argv=command,
        timeout=args.timeout,
    )


def backup_command(args: argparse.Namespace, action: str) -> list[str]:
    command = [
        env_command("INCIDENTX_BACKUPX", "backupx"),
        action,
        args.backup_id,
        "--repository",
        args.repository,
        "--json",
    ]
    if action == "verify":
        command.insert(-1, "--read-only")
    return command


def collect_backup(writer: BundleWriter, args: argparse.Namespace) -> None:
    component = "backup"
    target = {"backup_id": args.backup_id, "repository": args.repository}
    started_at = utc_now()
    inspect_argv = backup_command(args, "inspect")
    inspect_result = run_json_command(inspect_argv, args.timeout)
    inspect_status, inspect_payload, inspect_error = classify_payload(inspect_result, "backupx inspect")
    if inspect_status == "collected" and inspect_payload.get("status") in {"failed", "unavailable"}:
        inspect_status = str(inspect_payload.get("status"))
        inspect_error = trim_error(inspect_payload.get("error")) or "backupx inspect 未成功"
    warnings: list[str] = []
    verify_payload: dict[str, Any]
    verify_result: dict[str, Any] | None = None
    if inspect_status in {"unavailable", "failed"}:
        status = inspect_status
        error = inspect_error
        verify_payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": "backup_verification",
            "status": "skipped",
            "error": "manifest inspect 未成功，未执行备份校验",
        }
    else:
        verify_argv = backup_command(args, "verify")
        verify_result = run_json_command(verify_argv, args.timeout)
        verify_status, verify_payload, verify_error = classify_payload(verify_result, "backupx verify")
        status = "collected" if verify_status == "collected" else verify_status
        error = verify_error
        if verify_status != "collected":
            warnings.append("备份 manifest 已读取，但只读校验未完成")
    data = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_backup",
        "status": "ok" if status == "collected" else status,
        "inspect": inspect_payload,
        "verify": verify_payload,
    }
    source = {
        "kind": "cli",
        "commands": [command_source(inspect_argv, inspect_result)],
    }
    if verify_result is not None:
        source["commands"].append(command_source(backup_command(args, "verify"), verify_result))
    finished_at = utc_now()
    item = writer.add_evidence(
        component=component,
        target=target,
        operation="backup.inspect_verify",
        status=status,
        data=data,
        source=source,
        collected_at=finished_at,
        error=error,
        warnings=warnings,
    )
    writer.add_collector(
        name=component,
        target=target,
        operation="backup.inspect_verify",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        evidence_id=str(item["evidence_id"]),
        error=error,
        warnings=warnings,
    )


def incident_registry() -> registry_store.RegistryStore:
    registry_file = os.environ.get("INCIDENTX_REGISTRY_FILE") or None
    revision_dir = os.environ.get("INCIDENTX_REGISTRY_REVISION_DIR") or None
    return registry_store.RegistryStore(
        registry_file=registry_file,
        revision_dir=revision_dir,
    )


def registry_selection(args: argparse.Namespace) -> dict[str, str | None]:
    return {
        "project_id": str(args.project_id).strip() if args.project_id else None,
        "service_id": str(args.registry_service_id).strip() if args.registry_service_id else None,
        "deployment_id": str(args.deployment_id).strip() if args.deployment_id else None,
        "backup_asset_id": str(args.backup_asset_id).strip() if args.backup_asset_id else None,
    }


def registry_collection(document: dict[str, Any], collection: str) -> dict[str, Any]:
    raw = document.get(collection, {})
    return raw if isinstance(raw, dict) else {}


def registry_object(
    document: dict[str, Any], collection: str, object_id: str
) -> dict[str, Any] | None:
    raw = registry_collection(document, collection).get(object_id)
    if not isinstance(raw, dict):
        return None
    value = copy.deepcopy(raw)
    value["id"] = object_id
    return value


def registry_objects(document: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for object_id in sorted(registry_collection(document, collection)):
        value = registry_object(document, collection, str(object_id))
        if value is not None:
            values.append(value)
    return values


def registry_object_key(kind: Any, object_id: Any) -> tuple[str, str]:
    aliases = {
        "projects": "project",
        "services": "service",
        "deployments": "deployment",
        "backup_assets": "backup_asset",
    }
    normalized = str(kind or "").strip().lower()
    normalized = aliases.get(normalized, normalized)
    return normalized, str(object_id or "").strip()


def registry_snapshot(
    document: dict[str, Any], args: argparse.Namespace
) -> tuple[dict[str, Any], str, str, list[str]]:
    selection = registry_selection(args)
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_registry",
        "status": "ok",
        "registry_schema_version": document.get("schema_version", SCHEMA_VERSION),
        "registry_revision": document.get("revision", 0),
        "registry_updated_at": document.get("updated_at", ""),
        "selection": selection,
    }
    warnings: list[str] = []
    missing: list[str] = []

    explicit_projects: list[dict[str, Any]] = []
    explicit_services: list[dict[str, Any]] = []
    explicit_deployments: list[dict[str, Any]] = []
    explicit_assets: list[dict[str, Any]] = []
    selectors = (
        ("project", "projects", selection["project_id"], explicit_projects),
        ("service", "services", selection["service_id"], explicit_services),
        ("deployment", "deployments", selection["deployment_id"], explicit_deployments),
        ("backup_asset", "backup_assets", selection["backup_asset_id"], explicit_assets),
    )
    for kind, collection, object_id, output in selectors:
        if not object_id:
            continue
        value = registry_object(document, collection, object_id)
        if value is None:
            missing.append(f"{kind}={object_id}")
        else:
            output.append(value)
    if missing:
        data.update({
            "status": "failed",
            "missing": missing,
            "objects": {
                "projects": explicit_projects,
                "services": explicit_services,
                "deployments": explicit_deployments,
                "backup_assets": explicit_assets,
                "relations": [],
            },
            "counts": {},
        })
        error = "注册表中找不到请求对象：" + ", ".join(missing)
        data["error"] = error
        return data, "failed", error, warnings

    project_ids = {
        str(item.get("id")) for item in explicit_projects if item.get("id")
    }
    for item in explicit_services:
        project_id = str(item.get("project_id") or "")
        if project_id:
            project_ids.add(project_id)

    identity_targets: set[tuple[str, str, str]] = set()
    for item in [*explicit_deployments, *explicit_assets]:
        project_id = str(item.get("project_id") or "")
        service_id = str(item.get("service_id") or "")
        environment = str(item.get("environment") or "")
        if project_id and service_id and environment:
            identity_targets.add((project_id, service_id, environment))
        if project_id:
            project_ids.add(project_id)

    conflicts: list[str] = []
    for item in [*explicit_services, *explicit_deployments, *explicit_assets]:
        project_id = str(item.get("project_id") or "")
        service_id = str(item.get("service_id") or "")
        if selection["project_id"] and project_id and project_id != selection["project_id"]:
            conflicts.append(f"project_id 与对象 {item.get('id', '')} 不一致")
        if selection["service_id"] and service_id and service_id != selection["service_id"]:
            conflicts.append(f"service_id 与对象 {item.get('id', '')} 不一致")
    if conflicts:
        data.update({
            "status": "failed",
            "conflicts": conflicts,
            "objects": {
                "projects": explicit_projects,
                "services": explicit_services,
                "deployments": explicit_deployments,
                "backup_assets": explicit_assets,
                "relations": [],
            },
            "counts": {},
        })
        error = "注册表对象选择不一致：" + "; ".join(conflicts)
        data["error"] = error
        return data, "failed", error, warnings

    # 项目选择展开整个项目；服务选择展开该服务；deployment/asset 选择只展开
    # 同一稳定身份，避免一次事件无意间采集其它环境的对象。
    all_deployments = registry_objects(document, "deployments")
    all_assets = registry_objects(document, "backup_assets")
    if selection["project_id"]:
        selected_deployments = [
            item for item in all_deployments
            if str(item.get("project_id") or "") == selection["project_id"]
        ]
        selected_assets = [
            item for item in all_assets
            if str(item.get("project_id") or "") == selection["project_id"]
        ]
    elif selection["service_id"]:
        selected_deployments = [
            item for item in all_deployments
            if str(item.get("service_id") or "") == selection["service_id"]
            and (not project_ids or str(item.get("project_id") or "") in project_ids)
        ]
        selected_assets = [
            item for item in all_assets
            if str(item.get("service_id") or "") == selection["service_id"]
            and (not project_ids or str(item.get("project_id") or "") in project_ids)
        ]
    else:
        explicit_deployment_ids = {
            str(value.get("id")) for value in explicit_deployments
        }
        explicit_asset_ids = {str(value.get("id")) for value in explicit_assets}
        selected_deployments = [
            item for item in all_deployments
            if str(item.get("id") or "") in explicit_deployment_ids
            or (
                identity_targets
                and (
                    str(item.get("project_id") or ""),
                    str(item.get("service_id") or ""),
                    str(item.get("environment") or ""),
                ) in identity_targets
            )
        ]
        selected_assets = [
            item for item in all_assets
            if str(item.get("id") or "") in explicit_asset_ids
            or (
                identity_targets
                and (
                    str(item.get("project_id") or ""),
                    str(item.get("service_id") or ""),
                    str(item.get("environment") or ""),
                ) in identity_targets
            )
        ]

    selected_deployments = {
        str(item.get("id")): item for item in [*explicit_deployments, *selected_deployments]
    }
    selected_assets = {
        str(item.get("id")): item for item in [*explicit_assets, *selected_assets]
    }
    selected_projects = {
        str(item.get("id")): item for item in explicit_projects
    }
    selected_services = {
        str(item.get("id")): item for item in explicit_services
    }
    if selection["project_id"]:
        for item in registry_objects(document, "services"):
            if str(item.get("project_id") or "") == selection["project_id"]:
                selected_services[str(item["id"])] = item
    for item in [*selected_deployments.values(), *selected_assets.values()]:
        project_id = str(item.get("project_id") or "")
        service_id = str(item.get("service_id") or "")
        if project_id:
            project = registry_object(document, "projects", project_id)
            if project is not None:
                selected_projects[project_id] = project
            else:
                warnings.append(f"对象 {item.get('id', '')} 引用的 project 不存在：{project_id}")
        if service_id:
            service = registry_object(document, "services", service_id)
            if service is not None and (
                not project_id or str(service.get("project_id") or "") in {"", project_id}
            ):
                selected_services[service_id] = service
            elif service is None:
                warnings.append(f"对象 {item.get('id', '')} 引用的 service 不存在：{service_id}")

    selected_keys: set[tuple[str, str]] = set()
    for kind, values in (
        ("project", selected_projects.values()),
        ("service", selected_services.values()),
        ("deployment", selected_deployments.values()),
        ("backup_asset", selected_assets.values()),
    ):
        selected_keys.update((kind, str(item.get("id") or "")) for item in values)

    selected_relations: list[dict[str, Any]] = []
    for relation in registry_objects(document, "relations"):
        source = registry_object_key(relation.get("source_kind"), relation.get("source_id"))
        target = registry_object_key(relation.get("target_kind"), relation.get("target_id"))
        if source in selected_keys or target in selected_keys:
            selected_relations.append(relation)

    objects = {
        "projects": sorted(selected_projects.values(), key=lambda item: str(item.get("id", ""))),
        "services": sorted(selected_services.values(), key=lambda item: str(item.get("id", ""))),
        "deployments": sorted(selected_deployments.values(), key=lambda item: str(item.get("id", ""))),
        "backup_assets": sorted(selected_assets.values(), key=lambda item: str(item.get("id", ""))),
        "relations": sorted(selected_relations, key=lambda item: str(item.get("id", ""))),
    }
    data["objects"] = objects
    data["counts"] = {name: len(values) for name, values in objects.items()}
    if warnings:
        data["warnings"] = warnings
    return data, "collected", "", warnings


def collect_registry(writer: BundleWriter, args: argparse.Namespace) -> None:
    selection = registry_selection(args)
    target = dict(selection)
    started_at = utc_now()
    store = incident_registry()
    data: dict[str, Any]
    warnings: list[str] = []
    error = ""
    status = "collected"
    try:
        document = store.load(repair=False)
        data, status, error, warnings = registry_snapshot(document, args)
        writer.registry_context = {
            "schema_version": data.get("registry_schema_version", SCHEMA_VERSION),
            "revision": data.get("registry_revision", 0),
            "updated_at": data.get("registry_updated_at", ""),
            "selection": copy.deepcopy(selection),
        }
    except (registry_store.RegistryError, OSError, ValueError) as exc:
        status = "unavailable"
        error = str(exc)
        data = {
            "schema_version": SCHEMA_VERSION,
            "kind": "incident_registry",
            "status": status,
            "selection": selection,
            "error": error,
        }
    finished_at = utc_now()
    source = {
        "kind": "registry_store",
        "registry_file": str(store.registry_file),
        "revision_dir": str(store.revision_dir),
        "revision": data.get("registry_revision"),
    }
    item = writer.add_evidence(
        component=REGISTRY_COMPONENT,
        target=target,
        operation="registry.snapshot",
        status=status,
        data=data,
        source=source,
        collected_at=finished_at,
        error=error,
        warnings=warnings,
    )
    if writer.registry_context is not None:
        writer.registry_context["evidence_id"] = item["evidence_id"]
        writer.registry_context["counts"] = copy.deepcopy(data.get("counts", {}))
    writer.add_collector(
        name=REGISTRY_COMPONENT,
        target=target,
        operation="registry.snapshot",
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        evidence_id=str(item["evidence_id"]),
        error=error,
        warnings=warnings,
    )


def parse_required(raw_values: list[str], requested: list[str] | None = None) -> list[str]:
    required: list[str] = []
    for raw in raw_values:
        for value in raw.split(","):
            name = value.strip().lower()
            if not name:
                continue
            if name == "all":
                names = list(COMPONENTS)
            else:
                names = [name]
            for item in names:
                if item not in (*COMPONENTS, REGISTRY_COMPONENT):
                    raise ValueError(f"--require 不支持组件：{item}")
                if item not in required:
                    required.append(item)
    if "all" in {value.strip().lower() for raw in raw_values for value in raw.split(",")}:
        if requested and REGISTRY_COMPONENT in requested and REGISTRY_COMPONENT not in required:
            required.append(REGISTRY_COMPONENT)
    return required


def requested_components(args: argparse.Namespace) -> list[str]:
    requested: list[str] = []
    if args.host:
        requested.extend(["host", "ssh"])
    if args.source:
        requested.append("db")
    if args.app:
        requested.append("deploy")
    if args.backup_id:
        requested.append("backup")
    if any(
        getattr(args, name, None)
        for name in ("project_id", "registry_service_id", "deployment_id", "backup_asset_id")
    ):
        requested.append(REGISTRY_COMPONENT)
    return requested


def validate_collect_args(args: argparse.Namespace, required: list[str]) -> list[str]:
    validate_case_id(args.case_id)
    requested = requested_components(args)
    if not requested:
        raise ValueError(
            "至少提供 --host、--source、--app、--backup-id 或注册表对象选择之一"
        )
    if args.app and not args.host:
        raise ValueError("--app 必须同时提供 --host")
    if args.app and not args.release_root:
        raise ValueError("--app 必须同时提供 --release-root；incidentx 不猜测远端发布目录")
    if args.backup_id and not args.repository:
        raise ValueError("--backup-id 必须同时提供 --repository")
    if args.repository and not args.backup_id:
        raise ValueError("--repository 必须同时提供 --backup-id")
    if (args.since or args.log_source) and not args.host:
        raise ValueError("--since/--log-source 必须同时提供 --host")
    missing_required = [name for name in required if name not in requested]
    if missing_required:
        raise ValueError("--require 指定了未请求的组件：" + ",".join(missing_required))
    return requested


def event_time_for(component: str, data: dict[str, Any]) -> str | None:
    explicit = data.get("event_time")
    if isinstance(explicit, str) and explicit:
        return explicit
    if component == "deploy":
        last_result = data.get("last_result")
        if isinstance(last_result, dict):
            for key in ("finished_at", "started_at"):
                value = last_result.get(key)
                if isinstance(value, str) and value:
                    return value
    if component == "backup":
        inspect = data.get("inspect")
        if isinstance(inspect, dict):
            manifest = inspect.get("manifest")
            if isinstance(manifest, dict):
                value = manifest.get("created_at")
                if isinstance(value, str) and value:
                    return value
    return None


def summary_for(item: dict[str, Any], envelope: dict[str, Any]) -> str:
    data = envelope.get("data")
    status = envelope.get("status", "unknown")
    if isinstance(data, dict):
        if item.get("component") == "backup":
            verify = data.get("verify")
            if isinstance(verify, dict) and verify.get("status"):
                status = verify["status"]
        elif item.get("component") == REGISTRY_COMPONENT:
            status = data.get("status", status)
            revision = data.get("registry_revision", "unknown")
            counts = data.get("counts")
            count_text = ""
            if isinstance(counts, dict):
                count_text = " objects=" + ",".join(
                    f"{key}:{counts[key]}" for key in sorted(counts)
                )
            target = item.get("target") or {}
            target_text = ",".join(f"{key}={value}" for key, value in target.items())
            return (
                f"{item.get('operation', '')} status={status} revision={revision}"
                f"{count_text} {target_text}"
            ).strip()
        elif data.get("status"):
            status = data["status"]
    target = item.get("target") or {}
    target_text = ",".join(f"{key}={value}" for key, value in target.items())
    return f"{item.get('operation', '')} status={status} {target_text}".strip()


def build_timeline(bundle: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in evidence:
        path = bundle / safe_relative_path(str(item["path"]))
        envelope = read_json_object(path, f"证据文件 {item['evidence_id']}")
        observed_at = str(item.get("collected_at") or envelope.get("collected_at") or "")
        event_time = event_time_for(str(item.get("component", "")), envelope.get("data", {}))
        events.append(
            {
                "event_id": f"event-{item['evidence_id']}",
                "source": item.get("component", ""),
                "event_time": event_time,
                "observed_at": observed_at,
                "time_basis": "event_time" if event_time else "observed_at",
                "kind": item.get("operation", ""),
                "summary": summary_for(item, envelope),
                "evidence_id": item["evidence_id"],
            }
        )
        if item.get("component") == REGISTRY_COMPONENT:
            registry_data = envelope.get("data")
            if isinstance(registry_data, dict):
                event = events[-1]
                if "registry_revision" in registry_data:
                    event["registry_revision"] = registry_data.get("registry_revision")
                if "registry_updated_at" in registry_data:
                    event["registry_updated_at"] = registry_data.get("registry_updated_at")
                if isinstance(registry_data.get("selection"), dict):
                    event["registry_selection"] = registry_data["selection"]
    events.sort(key=lambda event: (str(event.get("event_time") or event.get("observed_at") or ""), str(event.get("source", "")), str(event.get("event_id", ""))))
    return events


def timeline_metadata(bundle: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    path = bundle / "timeline.jsonl"
    content = "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n" for event in events)
    atomic_write_text(path, content)
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def write_checksums(bundle: Path, evidence: list[dict[str, Any]], timeline: dict[str, Any]) -> dict[str, Any]:
    rows = [(str(item["sha256"]), str(item["path"])) for item in evidence]
    rows.append((str(timeline["sha256"]), str(timeline["path"])))
    rows.sort(key=lambda row: row[1])
    content = "".join(f"{digest}  {path}\n" for digest, path in rows)
    path = bundle / "checksums.sha256"
    atomic_write_text(path, content)
    return {"path": path.name, "entries": len(rows)}


def collect(args: argparse.Namespace) -> dict[str, Any]:
    requested = requested_components(args)
    required = parse_required(args.require, requested)
    requested = validate_collect_args(args, required)
    bundle = prepare_bundle(args.out)
    started_at = utc_now()
    writer = BundleWriter(bundle, args.case_id, started_at, required)

    if args.host:
        collect_host(writer, args)
        collect_ssh(writer, args)
    if args.source:
        collect_db(writer, args)
    if args.app:
        collect_deploy(writer, args)
    if args.backup_id:
        collect_backup(writer, args)
    if REGISTRY_COMPONENT in requested:
        collect_registry(writer, args)

    required_failed = [
        item["name"]
        for item in writer.collectors
        if item.get("required") and item.get("status") != "collected"
    ]
    optional_failed = [
        item["name"]
        for item in writer.collectors
        if not item.get("required") and item.get("status") != "collected"
    ]
    if required_failed:
        status = "failed"
    elif optional_failed:
        status = "partial"
    else:
        status = "collected"

    events = build_timeline(bundle, writer.evidence)
    timeline = timeline_metadata(bundle, events)
    checksums = write_checksums(bundle, writer.evidence, timeline)
    finished_at = utc_now()
    warnings: list[str] = []
    if optional_failed:
        warnings.append("部分非必需 collector 未完成：" + ",".join(optional_failed))
    if required_failed:
        warnings.append("必需 collector 未完成：" + ",".join(required_failed))
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_bundle",
        "case_id": args.case_id,
        "tool_version": tool_version(),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "requested_components": requested,
        "required_components": required,
        "collectors": writer.collectors,
        "evidence": writer.evidence,
        "timeline_file": timeline["path"],
        "timeline": timeline,
        "checksums_file": checksums["path"],
        "bundle_complete": True,
        "warnings": warnings,
    }
    if writer.registry_context is not None:
        manifest.update({
            "registry_revision": writer.registry_context.get("revision"),
            "registry_updated_at": writer.registry_context.get("updated_at", ""),
            "registry_evidence_id": writer.registry_context.get("evidence_id"),
            "registry_selection": writer.registry_context.get("selection", {}),
            "registry_counts": writer.registry_context.get("counts", {}),
        })
    atomic_write_json(bundle / "manifest.json", manifest)
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_collect",
        "case_id": args.case_id,
        "bundle_dir": str(bundle),
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "requested_components": requested,
        "required_components": required,
        "collectors": writer.collectors,
        "evidence": writer.evidence,
        "timeline_file": timeline["path"],
        "checksums_file": checksums["path"],
        "bundle_complete": True,
        "warnings": warnings,
    }
    if writer.registry_context is not None:
        result.update({
            "registry_revision": writer.registry_context.get("revision"),
            "registry_updated_at": writer.registry_context.get("updated_at", ""),
            "registry_evidence_id": writer.registry_context.get("evidence_id"),
            "registry_selection": writer.registry_context.get("selection", {}),
            "registry_counts": writer.registry_context.get("counts", {}),
        })
    return result


def read_timeline(bundle: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    path = bundle / safe_relative_path(str(manifest.get("timeline_file", "timeline.jsonl")))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IncidentError(f"timeline 无法读取：{path}：{exc}") from exc
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IncidentError(f"timeline 第 {number} 行不是 JSON：{exc}") from exc
        if not isinstance(event, dict):
            raise IncidentError(f"timeline 第 {number} 行不是 JSON 对象")
        events.append(event)
    events.sort(key=lambda event: (str(event.get("event_time") or event.get("observed_at") or ""), str(event.get("source", "")), str(event.get("event_id", ""))))
    return events


def show_bundle(bundle_value: str) -> dict[str, Any]:
    bundle = Path(bundle_value).expanduser().resolve()
    manifest = read_json_object(bundle / "manifest.json", "bundle manifest")
    collectors = manifest.get("collectors") if isinstance(manifest.get("collectors"), list) else []
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_show",
        "status": "ok",
        "bundle_dir": str(bundle),
        "case_id": manifest.get("case_id", ""),
        "bundle_status": manifest.get("status", "unknown"),
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "requested_components": manifest.get("requested_components", []),
        "required_components": manifest.get("required_components", []),
        "collectors": collectors,
        "evidence_count": len(manifest.get("evidence", [])) if isinstance(manifest.get("evidence"), list) else 0,
        "timeline_file": manifest.get("timeline_file", ""),
        "checksums_file": manifest.get("checksums_file", ""),
        "warnings": manifest.get("warnings", []),
    }
    for field in (
        "registry_revision",
        "registry_updated_at",
        "registry_evidence_id",
        "registry_selection",
        "registry_counts",
    ):
        if field in manifest:
            result[field] = manifest[field]
    return result


def timeline_bundle(bundle_value: str) -> dict[str, Any]:
    bundle = Path(bundle_value).expanduser().resolve()
    manifest = read_json_object(bundle / "manifest.json", "bundle manifest")
    events = read_timeline(bundle, manifest)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_timeline",
        "status": "ok",
        "bundle_dir": str(bundle),
        "case_id": manifest.get("case_id", ""),
        "events": events,
    }


def checksum_entries(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IncidentError(f"校验清单无法读取：{path}：{exc}") from exc
    entries: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            raise IncidentError(f"校验清单第 {number} 行格式错误")
        relative = parts[1].lstrip("*")
        if relative in entries:
            raise IncidentError(f"校验清单包含重复路径：{relative}")
        safe_relative_path(relative)
        entries[relative] = parts[0].lower()
    return entries


def verify_bundle(bundle_value: str) -> dict[str, Any]:
    bundle = Path(bundle_value).expanduser().resolve()
    manifest = read_json_object(bundle / "manifest.json", "bundle manifest")
    checks: list[dict[str, Any]] = []
    valid = True
    evidence = manifest.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
        checks.append({"name": "manifest_evidence", "status": "fail", "error": "manifest.evidence 不是数组"})
        valid = False
    else:
        checks.append({"name": "manifest_evidence", "status": "pass", "count": len(evidence)})

    expected_files: dict[str, str] = {}
    evidence_ids: set[str] = set()
    evidence_items: dict[str, dict[str, Any]] = {}
    for item in evidence:
        if not isinstance(item, dict):
            checks.append({"name": "evidence_item", "status": "fail", "error": "证据项不是对象"})
            valid = False
            continue
        relative_value = str(item.get("path", ""))
        try:
            relative = safe_relative_path(relative_value)
        except IncidentError as exc:
            checks.append({"name": "evidence_path", "status": "fail", "path": relative_value, "error": str(exc)})
            valid = False
            continue
        expected_files[relative.as_posix()] = str(item.get("sha256", "")).lower()
        evidence_id = str(item.get("evidence_id", ""))
        if not evidence_id or evidence_id in evidence_ids:
            checks.append({"name": "evidence_identity", "status": "fail", "path": relative.as_posix(), "error": "证据 ID 缺失或重复"})
            valid = False
        evidence_ids.add(evidence_id)
        if evidence_id:
            evidence_items[evidence_id] = item
        path = bundle / relative
        exists = path.is_file()
        file_check = {"name": "evidence", "status": "pass" if exists else "fail", "path": relative.as_posix()}
        if not exists:
            file_check["error"] = "证据文件不存在"
            valid = False
        else:
            actual_bytes = path.stat().st_size
            actual_sha256 = sha256_file(path)
            try:
                envelope = read_json_object(path, f"证据文件 {evidence_id}")
                identity_matches = all(
                    envelope.get(key) == item.get(key)
                    for key in ("evidence_id", "component", "operation", "status")
                )
                if not identity_matches:
                    file_check["status"] = "fail"
                    file_check["error"] = "证据文件元数据与 manifest 不一致"
                    valid = False
            except IncidentError as exc:
                file_check["status"] = "fail"
                file_check["error"] = str(exc)
                valid = False
            if item.get("bytes") != actual_bytes:
                file_check["status"] = "fail"
                file_check["error"] = "文件大小与 manifest 不一致"
                file_check["expected_bytes"] = item.get("bytes")
                file_check["actual_bytes"] = actual_bytes
                valid = False
            if expected_files[relative.as_posix()] != actual_sha256:
                file_check["status"] = "fail"
                file_check["error"] = "SHA-256 与 manifest 不一致"
                file_check["expected_sha256"] = expected_files[relative.as_posix()]
                file_check["actual_sha256"] = actual_sha256
                valid = False
        checks.append(file_check)

    registry_evidence_id = str(manifest.get("registry_evidence_id", ""))
    registry_fields_present = any(
        field in manifest
        for field in ("registry_revision", "registry_updated_at", "registry_selection", "registry_counts")
    )
    if registry_evidence_id or registry_fields_present:
        registry_check: dict[str, Any] = {"name": "registry_metadata", "status": "pass"}
        registry_item = evidence_items.get(registry_evidence_id)
        if not registry_evidence_id or registry_item is None:
            registry_check.update({
                "status": "fail",
                "error": "manifest 的注册表元数据没有对应的 registry 证据",
            })
            valid = False
        elif registry_item.get("component") != REGISTRY_COMPONENT:
            registry_check.update({
                "status": "fail",
                "error": "registry_evidence_id 未指向 registry 组件",
            })
            valid = False
        else:
            try:
                registry_path = bundle / safe_relative_path(str(registry_item.get("path", "")))
                registry_envelope = read_json_object(registry_path, "注册表证据文件")
                registry_data = registry_envelope.get("data")
                if not isinstance(registry_data, dict):
                    raise IncidentError("注册表证据 data 不是对象")
                expected_metadata = {
                    "registry_revision": manifest.get("registry_revision"),
                    "registry_updated_at": manifest.get("registry_updated_at"),
                    "selection": manifest.get("registry_selection"),
                    "counts": manifest.get("registry_counts"),
                }
                actual_metadata = {
                    "registry_revision": registry_data.get("registry_revision"),
                    "registry_updated_at": registry_data.get("registry_updated_at"),
                    "selection": registry_data.get("selection"),
                    "counts": registry_data.get("counts"),
                }
                if expected_metadata != actual_metadata:
                    raise IncidentError("manifest 注册表元数据与证据文件不一致")
            except IncidentError as exc:
                registry_check.update({"status": "fail", "error": str(exc)})
                valid = False
        checks.append(registry_check)

    timeline_value = str(manifest.get("timeline_file", ""))
    try:
        timeline_relative = safe_relative_path(timeline_value)
        timeline_path = bundle / timeline_relative
        if not timeline_path.is_file():
            raise IncidentError("timeline 文件不存在")
        timeline_sha = sha256_file(timeline_path)
        timeline_metadata = manifest.get("timeline")
        if isinstance(timeline_metadata, dict):
            if timeline_metadata.get("bytes") != timeline_path.stat().st_size or str(timeline_metadata.get("sha256", "")).lower() != timeline_sha:
                raise IncidentError("timeline 文件与 manifest.timeline 不一致")
        expected_files[timeline_relative.as_posix()] = timeline_sha
        events = read_timeline(bundle, manifest)
        if any(str(event.get("evidence_id", "")) not in evidence_ids for event in events):
            raise IncidentError("timeline 引用了不存在的 evidence_id")
        checks.append({"name": "timeline", "status": "pass", "path": timeline_relative.as_posix()})
    except (IncidentError, OSError) as exc:
        checks.append({"name": "timeline", "status": "fail", "path": timeline_value, "error": str(exc)})
        valid = False

    try:
        checksum_value = str(manifest.get("checksums_file", ""))
        checksum_relative = safe_relative_path(checksum_value)
        checksum_path = bundle / checksum_relative
        actual_entries = checksum_entries(checksum_path)
        if actual_entries != expected_files:
            checks.append({
                "name": "checksums",
                "status": "fail",
                "error": "校验清单与 manifest/文件集合不一致",
                "expected": expected_files,
                "actual": actual_entries,
            })
            valid = False
        else:
            checks.append({"name": "checksums", "status": "pass", "entries": len(actual_entries)})
    except IncidentError as exc:
        checks.append({"name": "checksums", "status": "fail", "error": str(exc)})
        valid = False

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "incident_verification",
        "status": "passed" if valid else "failed",
        "bundle_dir": str(bundle),
        "case_id": manifest.get("case_id", ""),
        "checks": checks,
        "checked_files": len(expected_files),
    }


def render_human(payload: dict[str, Any]) -> None:
    kind = payload.get("kind", "")
    if kind == "incident_collect":
        print(f"# kind={kind} case={payload.get('case_id', '')} status={payload.get('status', '')}")
        print(f"bundle={payload.get('bundle_dir', '')}")
        for collector in payload.get("collectors", []):
            print(f"collector={collector.get('name', '')} status={collector.get('status', '')}")
        for warning in payload.get("warnings", []):
            print(f"warning: {warning}")
        return
    if kind == "incident_show":
        print(f"# kind={kind} case={payload.get('case_id', '')} status={payload.get('bundle_status', '')}")
        print(f"bundle={payload.get('bundle_dir', '')}")
        print(f"evidence_count={payload.get('evidence_count', 0)}")
        for collector in payload.get("collectors", []):
            print(f"collector={collector.get('name', '')} status={collector.get('status', '')}")
        for warning in payload.get("warnings", []):
            print(f"warning: {warning}")
        return
    if kind == "incident_timeline":
        print(f"# kind={kind} case={payload.get('case_id', '')} status={payload.get('status', '')}")
        for event in payload.get("events", []):
            timestamp = event.get("event_time") or event.get("observed_at") or ""
            print(
                f"time={timestamp} basis={event.get('time_basis', '')} "
                f"source={event.get('source', '')} kind={event.get('kind', '')} "
                f"summary={event.get('summary', '')}"
            )
        return
    if kind == "incident_verification":
        print(f"# kind={kind} case={payload.get('case_id', '')} status={payload.get('status', '')}")
        for check in payload.get("checks", []):
            print(f"check={check.get('name', '')} status={check.get('status', '')}")
        return
    if payload.get("error"):
        print(f"error: {payload['error']}", file=sys.stderr)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def command_exit_code(payload: dict[str, Any]) -> int:
    kind = payload.get("kind")
    if kind == "incident_collect":
        return 0 if payload.get("status") in {"collected", "partial"} and payload.get("bundle_complete") else 1
    if kind == "incident_verification":
        return 0 if payload.get("status") == "passed" else 1
    return 0 if payload.get("status") in {"ok", "collected"} else 1


def add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def add_read_args(parser: argparse.ArgumentParser) -> None:
    add_json_arg(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="采集只读事件证据并生成可校验诊断包。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="按明确范围采集只读证据")
    collect_parser.add_argument("case_id")
    collect_parser.add_argument("--out", required=True, help="新的 bundle 输出目录")
    collect_parser.add_argument("--host", help="SSH/主机 alias")
    collect_parser.add_argument("--source", help="dbx 数据源 alias 或 profile")
    collect_parser.add_argument("--app", help="deployx 应用名")
    collect_parser.add_argument(
        "--project-id", "--project", dest="project_id",
        help="注册表 project ID；按项目展开相关服务、deployment 和备份资产",
    )
    collect_parser.add_argument(
        "--service-id", "--registry-service-id", "--registry-service",
        dest="registry_service_id",
        help="注册表 service ID；不同于主机检查用的 --service unit",
    )
    collect_parser.add_argument(
        "--deployment-id", "--deployment", dest="deployment_id",
        help="注册表 deployment 稳定 ID：<project>/<service>/<environment>",
    )
    collect_parser.add_argument(
        "--backup-asset-id", "--backup-asset", "--asset-id",
        dest="backup_asset_id",
        help="注册表 backup asset ID；不同于实际备份 manifest 的 --backup-id",
    )
    collect_parser.add_argument("--release-root", help="deployx 远端发布根目录，必须是绝对路径")
    collect_parser.add_argument("--service", help="服务 unit；用于部署状态和主机健康检查")
    collect_parser.add_argument("--since", help="主机日志的时间范围")
    collect_parser.add_argument("--log-source", help="主机日志 unit 或绝对文件路径")
    collect_parser.add_argument("--repository", help="backupx 本地仓库目录")
    collect_parser.add_argument("--backup-id", help="backupx 备份 ID")
    collect_parser.add_argument("--require", action="append", default=[], help="必需组件，可重复或用逗号分隔")
    collect_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(collect_parser)

    show_parser = subparsers.add_parser("show", help="离线查看 bundle 摘要")
    show_parser.add_argument("bundle_dir")
    add_read_args(show_parser)

    timeline_parser = subparsers.add_parser("timeline", help="离线查看 bundle 时间线")
    timeline_parser.add_argument("bundle_dir")
    add_read_args(timeline_parser)

    verify_parser = subparsers.add_parser("verify", help="离线校验 bundle 文件和 SHA-256")
    verify_parser.add_argument("bundle_dir")
    add_read_args(verify_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "timeout", DEFAULT_TIMEOUT) < 1:
            raise ValueError("--timeout 必须大于 0")
        if args.command == "collect":
            payload = collect(args)
        elif args.command == "show":
            payload = show_bundle(args.bundle_dir)
        elif args.command == "timeline":
            payload = timeline_bundle(args.bundle_dir)
        else:
            payload = verify_bundle(args.bundle_dir)
        if args.json:
            print_json(payload)
        else:
            render_human(payload)
        return command_exit_code(payload)
    except Exception as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": f"incident_{getattr(args, 'command', 'error')}",
            "status": "failed",
            "error": str(exc),
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print(f"error: {payload['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
