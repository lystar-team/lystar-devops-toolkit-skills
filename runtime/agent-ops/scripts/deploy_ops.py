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
import uuid
from pathlib import Path
from typing import Any

import registry_store


SCHEMA_VERSION = 1
DEFAULT_TIMEOUT = 120
DEFAULT_UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024
REMOTE_OUTPUT_BYTES = 64_000
APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
PATH_KINDS = {"release", "config", "data", "log", "temporary", "external"}
PORT_PROTOCOLS = {"tcp", "udp"}
RECIPE_STAGE_NAMES = ("prepare", "install", "configure", "activate", "verify", "rollback")
BUILTIN_RECIPE_ID = "tar.gz-systemd"
STRATEGIES = {"versioned-link", "directory-swap"}
SERVICE_TYPES = {"systemd", "nginx-static"}


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def trim_error(value: Any) -> str:
    text = str(value or "").strip()
    return text[-1000:] if len(text) > 1000 else text


def target_ref(alias: str) -> dict[str, str]:
    return {"alias": alias}


def validate_remote_path(value: str, option: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("/") or "\n" in text or "\t" in text:
        raise ValueError(f"{option} 必须是绝对远端目录，且不能包含换行或制表符")
    return posixpath.normpath(text)


def validate_spec(app: str, release_root: str) -> None:
    if not APP_NAME_RE.fullmatch(app):
        raise ValueError("--app 必须是单级路径名，只能包含字母、数字、点、下划线和短横线")
    validate_remote_path(release_root, "--release-root")


def layout(app: str, release_root: str) -> dict[str, str]:
    validate_spec(app, release_root)
    app_root = posixpath.join(release_root.rstrip("/") or "/", app)
    return {
        "app_root": app_root,
        "releases_root": posixpath.join(app_root, "releases"),
        "current_link": posixpath.join(app_root, "current"),
        "deployments_root": posixpath.join(app_root, "deployments"),
    }


def directory_swap_layout(app: str, live_path: str, state_root: str | None) -> dict[str, str]:
    if not APP_NAME_RE.fullmatch(app):
        raise ValueError("--app 必须是单级路径名，只能包含字母、数字、点、下划线和短横线")
    live = validate_remote_path(live_path, "--live-path")
    if live == "/":
        raise ValueError("--live-path 不能是根目录")
    state = validate_remote_path(
        state_root or posixpath.join(posixpath.dirname(live), ".deployx", posixpath.basename(live)),
        "--state-root",
    )
    if state == live or state.startswith(live.rstrip("/") + "/") or live.startswith(state.rstrip("/") + "/"):
        raise ValueError("--state-root 必须与 --live-path 相互独立，不能互相包含")
    return {
        "live_path": live,
        "state_root": state,
        "releases_root": posixpath.join(state, "releases"),
        "staging_root": posixpath.join(state, "staging"),
        "failed_root": posixpath.join(state, "failed"),
        "manifests_root": posixpath.join(state, "manifests"),
        "deployments_root": posixpath.join(state, "deployments"),
        "deployment_file": posixpath.join(state, "deployment.json"),
        "lock_root": posixpath.join(state, "lock"),
    }


def safe_tar_member(member: tarfile.TarInfo) -> str | None:
    name = str(member.name or "")
    if not name or name.startswith("/"):
        return "制品包含空路径或绝对路径"
    normalized = posixpath.normpath(name)
    if normalized == ".." or normalized.startswith("../"):
        return f"制品成员越过解压目录：{name}"
    if member.isdev() or member.isfifo():
        return f"制品包含不允许的特殊文件：{name}"
    if member.issym() or member.islnk():
        link = str(member.linkname or "")
        if not link or link.startswith("/"):
            return f"制品链接目标不安全：{name} -> {link}"
        base = posixpath.dirname(normalized) if member.issym() else ""
        target = posixpath.normpath(posixpath.join(base, link))
        if target == ".." or target.startswith("../"):
            return f"制品链接越过解压目录：{name} -> {link}"
    return None


def artifact_info(path_text: str, expected_sha256: str | None) -> dict[str, Any]:
    path = Path(path_text).expanduser()
    info: dict[str, Any] = {
        "path": str(path),
        "name": path.name,
        "format": "tar.gz",
        "exists": path.is_file(),
        "bytes": None,
        "sha256": None,
        "expected_sha256": expected_sha256.lower() if expected_sha256 else None,
        "status": "missing",
        "error": None,
        "unpacked_bytes": None,
        "unsafe_members": [],
    }
    if not path.is_file():
        info["error"] = "本地制品不存在或不是普通文件"
        return info
    if not path.name.endswith(".tar.gz"):
        info["status"] = "invalid"
        info["error"] = "首版制品格式固定为 .tar.gz"
        return info
    try:
        info["bytes"] = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        info["sha256"] = digest.hexdigest()
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            info["member_count"] = len(members)
            info["unpacked_bytes"] = sum(max(0, int(member.size or 0)) for member in members)
            unsafe = [error for member in members if (error := safe_tar_member(member))]
            if unsafe:
                info["status"] = "invalid"
                info["unsafe_members"] = unsafe[:20]
                info["error"] = unsafe[0]
                return info
    except (OSError, tarfile.TarError) as exc:
        info["status"] = "invalid"
        info["error"] = f"无法读取 tar.gz 制品：{exc}"
        return info
    if expected_sha256 and not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        info["status"] = "invalid"
        info["error"] = "--artifact-sha256 必须是 64 位十六进制 SHA-256"
        return info
    if expected_sha256 and info["sha256"] != expected_sha256.lower():
        info["status"] = "mismatch"
        info["error"] = "本地制品 SHA-256 与 --artifact-sha256 不一致"
        return info
    info["status"] = "ok"
    return info


def release_id_for(artifact: dict[str, Any]) -> str:
    name = str(artifact.get("name") or "release")
    stem = name[:-7] if name.endswith(".tar.gz") else name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "release"
    digest = str(artifact.get("sha256") or "unknown")[:12]
    return f"{stem[:48]}-{digest}"


def inspect_command(paths: dict[str, str], release_root: str) -> str:
    quoted = {key: shlex.quote(value) for key, value in paths.items()}
    root = shlex.quote(release_root)
    return f"""# deployx:inspect
app_root={quoted['app_root']}
releases_root={quoted['releases_root']}
current_link={quoted['current_link']}
deployments_root={quoted['deployments_root']}
release_root={root}
printf 'app_root\\t%s\\n' "$app_root"
printf 'releases_root\\t%s\\n' "$releases_root"
printf 'current_link\\t%s\\n' "$current_link"
printf 'app_root_exists\\t%s\\n' "$(test -d "$app_root" && echo true || echo false)"
printf 'release_root_exists\\t%s\\n' "$(test -d "$release_root" && echo true || echo false)"
printf 'release_root_is_dir\\t%s\\n' "$(test -d "$release_root" && echo true || echo false)"
printf 'release_root_parent_exists\\t%s\\n' "$(test -d "$(dirname "$release_root")" && echo true || echo false)"
printf 'releases_root_exists\\t%s\\n' "$(test -d "$releases_root" && echo true || echo false)"
if [ -L "$current_link" ] || [ -e "$current_link" ]; then
    printf 'current_target\\t%s\\n' "$(readlink "$current_link" 2>/dev/null || true)"
else
    printf 'current_target\\t\\n'
fi
disk_path=$release_root
if [ ! -d "$disk_path" ]; then disk_path=$(dirname "$release_root"); fi
if [ -d "$disk_path" ]; then
    df -Pk "$disk_path" 2>/dev/null | awk 'NR == 2 {{printf "available_bytes\\t%s\\n", $4 * 1024}}'
fi
if [ -r "$app_root/deployment.json" ]; then
    printf 'deployment_spec\\t'
    tr '\\n' ' ' < "$app_root/deployment.json"
    printf '\\n'
fi
if [ -r "$deployments_root/last.json" ]; then
    printf 'last_result\\t'
    tr '\\n' ' ' < "$deployments_root/last.json"
    printf '\\n'
fi
for manifest in "$releases_root"/*/manifest.json; do
    [ -f "$manifest" ] || continue
    printf 'manifest\\t%s\\t' "$(basename "$(dirname "$manifest")")"
    tr '\\n' ' ' < "$manifest"
    printf '\\n'
done
"""


def run_json_command(argv: list[str], timeout: int) -> dict[str, Any]:
    executable = argv[0]
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {
            "transport": "unavailable",
            "error": f"找不到命令：{executable}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
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
        }
    except OSError as exc:
        return {
            "transport": "unavailable",
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }
    raw = completed.stdout.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {
            "transport": "unavailable",
            "error": trim_error(completed.stderr) or "命令未返回 JSON 结果",
            "stdout": "",
            "stderr": trim_error(completed.stderr),
            "exit_code": completed.returncode,
        }
    if not isinstance(payload, dict):
        return {
            "transport": "unavailable",
            "error": "命令返回了非对象 JSON",
            "stdout": "",
            "stderr": trim_error(completed.stderr),
            "exit_code": completed.returncode,
        }
    return {
        "transport": "ok",
        "error": "",
        "stdout": str(payload.get("stdout", "")),
        "stderr": trim_error(payload.get("stderr")),
        "exit_code": payload.get("exit_code", completed.returncode),
        "payload": payload,
    }


def run_ssh(alias: str, command: str, timeout: int) -> dict[str, Any]:
    executable = os.environ.get("DEPLOYX_SSHX", "sshx")
    result = run_json_command(
        [
            executable,
            "exec",
            "--json",
            "--timeout",
            str(timeout),
            "--max-bytes",
            str(REMOTE_OUTPUT_BYTES),
            alias,
            command,
        ],
        timeout,
    )
    payload = result.get("payload")
    if result.get("transport") == "ok" and isinstance(payload, dict) and payload.get("error") and "stdout" not in payload:
        result.update(
            {
                "transport": "unavailable",
                "error": trim_error(payload.get("error")),
                "stdout": "",
                "stderr": trim_error(payload.get("stderr")),
            }
        )
    return result


def run_host_service(alias: str, service: str, timeout: int) -> dict[str, Any]:
    executable = os.environ.get("DEPLOYX_HOSTX", "hostx")
    result = run_json_command(
        [
            executable,
            "service",
            alias,
            "status",
            service,
            "--timeout",
            str(timeout),
            "--json",
        ],
        timeout,
    )
    if result.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": result.get("error") or "hostx 不可用",
        }
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": "hostx 未返回对象 JSON",
        }
    if payload.get("error") and not payload.get("kind"):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": trim_error(payload.get("error")),
        }
    return payload


def run_host_service_inspect(alias: str, service: str, timeout: int) -> dict[str, Any]:
    executable = os.environ.get("DEPLOYX_HOSTX", "hostx")
    result = run_json_command(
        [
            executable,
            "service",
            alias,
            "inspect",
            service,
            "--timeout",
            str(timeout),
            "--json",
        ],
        timeout,
    )
    if result.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": result.get("error") or "hostx 不可用",
        }
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": "hostx 未返回对象 JSON",
        }
    if payload.get("error") and not payload.get("kind"):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": trim_error(payload.get("error")),
        }
    return payload


def run_host_ports(alias: str, timeout: int) -> dict[str, Any]:
    executable = os.environ.get("DEPLOYX_HOSTX", "hostx")
    result = run_json_command(
        [executable, "ports", alias, "--timeout", str(timeout), "--json"],
        timeout,
    )
    if result.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": result.get("error") or "hostx 不可用",
        }
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": "hostx 未返回对象 JSON",
        }
    if payload.get("error") and not payload.get("kind"):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": trim_error(payload.get("error")),
        }
    return payload


def run_host_health(alias: str, checks: list[str], timeout: int) -> dict[str, Any]:
    executable = os.environ.get("DEPLOYX_HOSTX", "hostx")
    argv = [executable, "health", alias]
    for check in checks:
        argv.extend(["--check", check])
    argv.extend(["--timeout", str(timeout), "--json"])
    result = run_json_command(argv, timeout)
    if result.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": result.get("error") or "hostx 不可用",
        }
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": "hostx 未返回对象 JSON",
        }
    if payload.get("error") and not payload.get("kind"):
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": trim_error(payload.get("error")),
        }
    return payload


def run_ssh_put(
    alias: str,
    local: str,
    remote: str,
    timeout: int,
    chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
) -> dict[str, Any]:
    if chunk_size < 1:
        raise ValueError("--upload-chunk-size 必须大于 0")
    executable = os.environ.get("DEPLOYX_SSHX", "sshx")
    result = run_json_command(
        [
            executable,
            "put",
            "--json",
            alias,
            local,
            remote,
            "--timeout",
            str(timeout),
            "--resume",
            "--chunk-size",
            str(chunk_size),
        ],
        timeout,
    )
    if result.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": result.get("error") or "sshx 不可用",
        }
    payload = result.get("payload", {})
    if not isinstance(payload, dict):
        return {
            "status": "failed",
            "connection_status": "ok",
            "error": "sshx put 未返回对象 JSON",
        }
    if payload.get("error"):
        return {
            "status": "failed",
            "connection_status": "ok",
            "error": trim_error(payload.get("error")),
            "result": payload,
        }
    return {"status": "ok", "connection_status": "ok", "result": payload}


def remote_step(alias: str, command: str, timeout: int, name: str) -> dict[str, Any]:
    remote = run_ssh(alias, command, timeout)
    if remote.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "name": name,
            "error": remote.get("error") or "sshx 不可用",
            "stdout": str(remote.get("stdout", "")),
            "stderr": str(remote.get("stderr", "")),
        }
    try:
        exit_code = int(remote.get("exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 1
    if exit_code not in (0, None):
        return {
            "status": "failed",
            "connection_status": "ok",
            "name": name,
            "exit_code": exit_code,
            "error": trim_error(remote.get("stderr")) or f"远端 {name} 阶段失败",
            "stdout": str(remote.get("stdout", "")),
            "stderr": str(remote.get("stderr", "")),
        }
    return {
        "status": "ok",
        "connection_status": "ok",
        "name": name,
        "exit_code": 0,
        "stdout": str(remote.get("stdout", "")),
        "stderr": str(remote.get("stderr", "")),
    }


def marker_json(text: str, marker: str) -> dict[str, Any] | None:
    prefix = marker + "\t"
    for line in text.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            value = json.loads(line[len(prefix):])
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def parse_inspection(text: str, paths: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "app_root": paths["app_root"],
        "releases_root": paths["releases_root"],
        "current_link": paths["current_link"],
        "app_root_exists": False,
        "release_root_exists": False,
        "release_root_is_dir": False,
        "release_root_parent_exists": False,
        "releases_root_exists": False,
        "current_target": "",
        "available_bytes": None,
        "deployment_spec": {},
        "last_result": {},
        "releases": [],
        "warnings": [],
    }
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        key = parts[0]
        if key in {"app_root", "releases_root", "current_link", "current_target"} and len(parts) >= 2:
            result[key] = parts[1]
        elif key in {
            "app_root_exists",
            "release_root_exists",
            "release_root_is_dir",
            "release_root_parent_exists",
            "releases_root_exists",
        } and len(parts) >= 2:
            result[key] = parse_bool(parts[1])
        elif key == "available_bytes" and len(parts) >= 2:
            try:
                result[key] = int(parts[1])
            except ValueError:
                result["warnings"].append(f"无法解析可用空间：{parts[1][:100]}")
        elif key in {"deployment_spec", "last_result"} and len(parts) >= 2:
            try:
                value = json.loads(parts[1])
            except json.JSONDecodeError:
                result["warnings"].append(f"{key} 不是有效 JSON")
                continue
            if isinstance(value, dict):
                result[key] = value
            else:
                result["warnings"].append(f"{key} 不是 JSON 对象")
        elif key == "manifest" and len(parts) >= 3:
            release_dir = parts[1]
            try:
                value = json.loads(parts[2])
            except json.JSONDecodeError:
                result["warnings"].append(f"release manifest 无法解析：{release_dir}")
                result["releases"].append(
                    normalize_release({}, release_dir, result["releases_root"], state="invalid")
                )
                continue
            if not isinstance(value, dict):
                result["warnings"].append(f"release manifest 不是对象：{release_dir}")
                value = {}
            result["releases"].append(normalize_release(value, release_dir, result["releases_root"]))
    return result


def normalize_release(
    value: dict[str, Any],
    release_dir: str,
    releases_root: str,
    state: str | None = None,
) -> dict[str, Any]:
    release_id = str(value.get("release_id") or release_dir)
    return {
        "release_id": release_id,
        "path": str(value.get("path") or posixpath.join(releases_root, release_dir)),
        "created_at": str(value.get("created_at") or ""),
        "artifact_name": str(value.get("artifact_name") or ""),
        "artifact_sha256": str(value.get("artifact_sha256") or ""),
        "state": state or str(value.get("state") or "available"),
        "previous_release": value.get("previous_release"),
        "service": str(value.get("service") or ""),
    }


def sort_releases(releases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        releases,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("release_id") or "")),
        reverse=True,
    )


def current_release_id(current_target: str) -> str | None:
    target = current_target.strip().rstrip("/")
    return posixpath.basename(target) if target else None


def inspect_target(alias: str, app: str, release_root: str, timeout: int) -> dict[str, Any]:
    paths = layout(app, release_root)
    remote = run_ssh(alias, inspect_command(paths, release_root), timeout)
    if remote.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": remote.get("error") or "sshx 不可用",
            "inspection": None,
        }
    try:
        exit_code = int(remote.get("exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 1
    if exit_code not in (0, None):
        return {
            "status": "failed",
            "connection_status": "ok",
            "error": trim_error(remote.get("stderr")) or "远端部署目录检查失败",
            "inspection": None,
        }
    inspection = parse_inspection(str(remote.get("stdout", "")), paths)
    return {
        "status": "partial" if inspection["warnings"] else "ok",
        "connection_status": "ok",
        "error": None,
        "inspection": inspection,
    }


def directory_inspect_command(paths: dict[str, str], nginx_server_name: str | None) -> str:
    quoted = {key: shlex.quote(value) for key, value in paths.items()}
    server = shlex.quote(nginx_server_name or "")
    return f"""# deployx:directory-inspect
live_path={quoted['live_path']}
state_root={quoted['state_root']}
releases_root={quoted['releases_root']}
manifests_root={quoted['manifests_root']}
deployments_root={quoted['deployments_root']}
deployment_file={quoted['deployment_file']}
lock_root={quoted['lock_root']}
nginx_server_name={server}
printf 'live_path\t%s\n' "$live_path"
printf 'state_root\t%s\n' "$state_root"
printf 'live_exists\t%s\n' "$(test -e "$live_path" && echo true || echo false)"
printf 'live_is_dir\t%s\n' "$(test -d "$live_path" && echo true || echo false)"
printf 'state_exists\t%s\n' "$(test -d "$state_root" && echo true || echo false)"
if [ -d "$live_path" ]; then
    printf 'live_owner\t%s\n' "$(stat -c '%U:%G' "$live_path" 2>/dev/null || true)"
    printf 'live_mode\t%s\n' "$(stat -c '%a' "$live_path" 2>/dev/null || true)"
    printf 'live_device\t%s\n' "$(stat -c '%d' "$live_path" 2>/dev/null || true)"
    printf 'live_baseline\t%s\n' "$(stat -c '%d:%i:%Y:%s' "$live_path" 2>/dev/null || true)"
    du -sk "$live_path" 2>/dev/null | awk '{{printf "live_bytes\t%s\n", $1 * 1024}}'
    if [ -r "$live_path/index.html" ] && command -v sha256sum >/dev/null 2>&1; then
        printf 'entry_sha256\t%s\n' "$(sha256sum "$live_path/index.html" | sed 's/[[:space:]].*$//')"
    fi
fi
state_parent=$(dirname "$state_root")
if [ -d "$state_root" ]; then state_probe=$state_root; else state_probe=$state_parent; fi
while [ ! -d "$state_probe" ] && [ "$state_probe" != "/" ]; do state_probe=$(dirname "$state_probe"); done
if [ -d "$state_probe" ]; then
    printf 'state_device\t%s\n' "$(stat -c '%d' "$state_probe" 2>/dev/null || true)"
    df -Pk "$state_probe" 2>/dev/null | awk 'END {{printf "available_bytes\\t%s\\n", $4 * 1024}}'
fi
if [ -r "$deployment_file" ]; then
    printf 'deployment_spec\t'
    tr '\n' ' ' < "$deployment_file"
    printf '\n'
fi
if [ -r "$deployments_root/last.json" ]; then
    printf 'last_result\t'
    tr '\n' ' ' < "$deployments_root/last.json"
    printf '\n'
fi
if [ -r "$lock_root/active/info.json" ]; then
    printf 'active_lock\t'
    tr '\n' ' ' < "$lock_root/active/info.json"
    printf '\n'
fi
for manifest in "$manifests_root"/*.json; do
    [ -f "$manifest" ] || continue
    printf 'manifest\t%s\t' "$(basename "$manifest" .json)"
    tr '\n' ' ' < "$manifest"
    printf '\n'
done
for release_path in "$releases_root"/*; do
    [ -d "$release_path" ] || continue
    printf 'release_path\t%s\n' "$(basename "$release_path")"
done
printf 'nginx_available\t%s\n' "$(command -v nginx >/dev/null 2>&1 && echo true || echo false)"
if command -v nginx >/dev/null 2>&1; then
    if nginx -t >/dev/null 2>&1; then printf 'nginx_test\tpass\n'; else printf 'nginx_test\tfail\n'; fi
    if [ -n "$nginx_server_name" ]; then
        nginx_dump=$(nginx -T 2>&1 || true)
        if printf '%s' "$nginx_dump" | grep -Fq "server_name $nginx_server_name"; then
            printf 'nginx_server_found\ttrue\n'
        else
            printf 'nginx_server_found\tfalse\n'
        fi
        if printf '%s' "$nginx_dump" | grep -Fq "root $live_path" || printf '%s' "$nginx_dump" | grep -Fq "alias $live_path"; then
            printf 'nginx_root_found\ttrue\n'
        else
            printf 'nginx_root_found\tfalse\n'
        fi
    fi
fi
"""


def parse_directory_inspection(text: str, paths: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        **paths,
        "live_exists": False,
        "live_is_dir": False,
        "state_exists": False,
        "live_owner": "",
        "live_mode": "",
        "live_device": "",
        "state_device": "",
        "live_baseline": "",
        "live_bytes": None,
        "available_bytes": None,
        "entry_sha256": "",
        "deployment_spec": {},
        "last_result": {},
        "active_lock": {},
        "manifests": [],
        "release_paths": [],
        "nginx_available": False,
        "nginx_test": "unknown",
        "nginx_server_found": False,
        "nginx_root_found": False,
        "warnings": [],
    }
    bool_keys = {
        "live_exists", "live_is_dir", "state_exists", "nginx_available",
        "nginx_server_found", "nginx_root_found",
    }
    int_keys = {"live_bytes", "available_bytes"}
    text_keys = {
        "live_path", "state_root", "live_owner", "live_mode", "live_device",
        "state_device", "live_baseline", "entry_sha256", "nginx_test",
    }
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        key = parts[0]
        if key in bool_keys and len(parts) >= 2:
            result[key] = parse_bool(parts[1])
        elif key in int_keys and len(parts) >= 2:
            try:
                result[key] = int(parts[1])
            except ValueError:
                result["warnings"].append(f"无法解析 {key}：{parts[1][:100]}")
        elif key in text_keys and len(parts) >= 2:
            result[key] = parts[1]
        elif key in {"deployment_spec", "last_result", "active_lock"} and len(parts) >= 2:
            try:
                value = json.loads(parts[1])
            except json.JSONDecodeError:
                result["warnings"].append(f"{key} 不是有效 JSON")
                continue
            if isinstance(value, dict):
                result[key] = value
        elif key == "manifest" and len(parts) >= 3:
            release_id = parts[1]
            try:
                value = json.loads(parts[2])
            except json.JSONDecodeError:
                result["warnings"].append(f"release manifest 无法解析：{release_id}")
                continue
            if isinstance(value, dict):
                value = dict(value)
                value.setdefault("release_id", release_id)
                result["manifests"].append(value)
        elif key == "release_path" and len(parts) >= 2:
            result["release_paths"].append(parts[1])
    known_ids = {str(item.get("release_id") or "") for item in result["manifests"]}
    result["unmanaged_release_paths"] = [
        release_id for release_id in result["release_paths"] if release_id not in known_ids
    ]
    if result["unmanaged_release_paths"]:
        result["warnings"].append("发现没有 deployx manifest 的历史目录，保留现场不清理")
    return result


def inspect_directory_target(
    alias: str,
    app: str,
    live_path: str,
    state_root: str | None,
    nginx_server_name: str | None,
    timeout: int,
) -> dict[str, Any]:
    paths = directory_swap_layout(app, live_path, state_root)
    remote = run_ssh(alias, directory_inspect_command(paths, nginx_server_name), timeout)
    if remote.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "error": remote.get("error") or "sshx 不可用",
            "inspection": None,
            "paths": paths,
        }
    try:
        exit_code = int(remote.get("exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 1
    if exit_code not in (0, None):
        return {
            "status": "failed",
            "connection_status": "ok",
            "error": trim_error(remote.get("stderr")) or "历史目录检查失败",
            "inspection": None,
            "paths": paths,
        }
    inspection = parse_directory_inspection(str(remote.get("stdout", "")), paths)
    return {
        "status": "partial" if inspection["warnings"] else "ok",
        "connection_status": "ok",
        "error": None,
        "inspection": inspection,
        "paths": paths,
    }


def directory_base_payload(kind: str, alias: str, app: str, paths: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "target": target_ref(alias),
        "app": app,
        "strategy": "directory-swap",
        "service_type": "nginx-static",
        "live_path": paths["live_path"],
        "state_root": paths["state_root"],
        "collected_at": utc_now(),
    }


def parse_http_check(spec: str) -> dict[str, Any]:
    parts = [part.strip() for part in str(spec).split("|") if part.strip()]
    if not parts:
        raise ValueError("HTTP 健康检查不能为空")
    url = parts[0]
    if url.startswith("http:") and not url.startswith("http://"):
        url = url.split(":", 1)[1]
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"不支持的 HTTP 健康检查：{spec}")
    result: dict[str, Any] = {"spec": spec, "url": url, "status": 200}
    for option in parts[1:]:
        key, separator, value = option.partition("=")
        if not separator:
            raise ValueError(f"HTTP 健康检查选项格式错误：{option}")
        if key == "status":
            result["status"] = int(value)
        elif key == "contains":
            result["contains"] = value
        elif key == "json":
            path, marker, expected = value.partition(":")
            if not marker or not path:
                raise ValueError(f"JSON 检查格式应为 json=字段:期望值：{option}")
            try:
                expected_value = json.loads(expected)
            except json.JSONDecodeError:
                expected_value = expected
            result["json_path"] = path
            result["json_expected"] = expected_value
        else:
            raise ValueError(f"不支持的 HTTP 健康检查选项：{key}")
    return result


def application_health_specs(raw: list[str] | None) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in raw or []:
        for spec in (part.strip() for part in item.split(",") if part.strip()):
            if spec.startswith(("http://", "https://", "http:http://", "http:https://")):
                values.append(parse_http_check(spec))
    return values


def host_health_specs(raw: list[str] | None) -> list[str]:
    values: list[str] = []
    for item in raw or []:
        for spec in (part.strip() for part in item.split(",") if part.strip()):
            if not spec.startswith(("http://", "https://", "http:http://", "http:https://")):
                values.append(spec)
    return values


def json_path_value(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def run_application_health(
    alias: str,
    checks: list[dict[str, Any]],
    host_header: str | None,
    timeout: int,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        header = f"-H {shlex.quote('Host: ' + host_header)}" if host_header else ""
        command = f"""set -eu
body=$(mktemp /tmp/deployx-http.XXXXXX)
trap 'rm -f "$body"' EXIT HUP INT TERM
code=$(curl --location --silent --show-error --output "$body" --write-out '%{{http_code}}' {header} {shlex.quote(str(check['url']))})
printf 'DEPLOYX_HTTP_CODE\t%s\n' "$code"
printf 'DEPLOYX_HTTP_BODY_BEGIN\n'
head -c 32768 "$body"
printf '\nDEPLOYX_HTTP_BODY_END\n'
"""
        step = remote_step(alias, command, timeout, f"http-{index + 1}")
        if step.get("status") != "ok":
            results.append({**check, "status": step.get("status"), "error": step.get("error")})
            continue
        output = str(step.get("stdout", ""))
        code_match = re.search(r"^DEPLOYX_HTTP_CODE\t(\d+)$", output, re.MULTILINE)
        body_match = re.search(
            r"DEPLOYX_HTTP_BODY_BEGIN\n(.*?)\nDEPLOYX_HTTP_BODY_END",
            output,
            re.DOTALL,
        )
        actual_code = int(code_match.group(1)) if code_match else None
        body = body_match.group(1) if body_match else ""
        errors: list[str] = []
        if actual_code != check["status"]:
            errors.append(f"HTTP 状态码为 {actual_code}，期望 {check['status']}")
        expected_text = check.get("contains")
        if expected_text is not None and str(expected_text) not in body:
            errors.append("响应体不包含期望内容")
        if check.get("json_path"):
            try:
                json_body = json.loads(body)
            except json.JSONDecodeError:
                errors.append("响应体不是有效 JSON")
            else:
                found, actual = json_path_value(json_body, str(check["json_path"]))
                if not found or actual != check.get("json_expected"):
                    errors.append(
                        f"JSON 字段 {check['json_path']}={actual!r}，期望 {check.get('json_expected')!r}"
                    )
        results.append(
            {
                **check,
                "status": "pass" if not errors else "fail",
                "actual_status": actual_code,
                "errors": errors,
            }
        )
    statuses = {str(item.get("status")) for item in results}
    if "unavailable" in statuses:
        status = "unavailable"
        connection_status = "unavailable"
    elif "failed" in statuses or "fail" in statuses:
        status = "fail"
        connection_status = "ok"
    else:
        status = "pass"
        connection_status = "ok"
    return {
        "status": status,
        "connection_status": connection_status,
        "checks": results,
    }


def base_payload(kind: str, alias: str, app: str, release_root: str) -> dict[str, Any]:
    paths = layout(app, release_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "target": target_ref(alias),
        "app": app,
        "release_root": release_root,
        "app_root": paths["app_root"],
        "current_link": paths["current_link"],
        "collected_at": utc_now(),
    }


def check_status(name: str, status: str, detail: str = "") -> dict[str, str]:
    item = {"name": name, "status": status}
    if detail:
        item["detail"] = detail
    return item


def health_specs(raw: list[str] | None) -> list[str]:
    if not raw:
        return ["facts", "disk:/"]
    values: list[str] = []
    for item in raw:
        values.extend(value.strip() for value in item.split(",") if value.strip())
    return values or ["facts", "disk:/"]


def deploy_registry() -> registry_store.RegistryStore:
    registry_file = os.environ.get("DEPLOYX_REGISTRY_FILE") or None
    revision_dir = os.environ.get("DEPLOYX_REGISTRY_REVISION_DIR") or None
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


def registry_aliases(raw: list[str] | None) -> list[str]:
    values: list[str] = []
    for value in raw or []:
        text = str(value).strip()
        if text and text not in values:
            values.append(text)
    return values


def register_project(args: argparse.Namespace) -> dict[str, Any]:
    store = deploy_registry()
    fields = {
        "name": args.name,
        "aliases": registry_aliases(args.alias),
        "local_path": str(Path(args.local_path).expanduser()) if args.local_path else "",
        "repository_source": args.repository_source or "",
        "description": args.description or "",
    }
    existing = store.get("project", args.project_id)
    project = store.upsert("project", args.project_id, fields) if args.upsert else store.create(
        "project", args.project_id, fields
    )
    return registry_payload(
        "registry_project",
        "updated" if existing and args.upsert else "created",
        store,
        object=project,
    )


def register_environment(args: argparse.Namespace) -> dict[str, Any]:
    store = deploy_registry()
    fields = {
        "name": args.name or args.environment_id,
        "description": args.description or "",
    }
    existing = store.get("environment", args.environment_id)
    environment = (
        store.upsert("environment", args.environment_id, fields)
        if args.upsert
        else store.create("environment", args.environment_id, fields)
    )
    return registry_payload(
        "registry_environment",
        "updated" if existing and args.upsert else "created",
        store,
        object=environment,
    )


def register_service(args: argparse.Namespace) -> dict[str, Any]:
    store = deploy_registry()
    if store.get("project", args.project_id) is None:
        raise registry_store.RegistryError(f"project does not exist: {args.project_id}")
    fields = {
        "name": args.name,
        "aliases": registry_aliases(args.alias),
        "project_id": args.project_id,
        "service_type": args.service_type,
        "description": args.description or "",
    }
    existing = store.get("service", args.service_id)
    if existing and existing.get("project_id") not in (None, args.project_id):
        raise registry_store.RegistryError(
            f"service belongs to another project: {args.service_id}"
        )
    service = store.upsert("service", args.service_id, fields) if args.upsert else store.create(
        "service", args.service_id, fields
    )
    return registry_payload(
        "registry_service",
        "updated" if existing and args.upsert else "created",
        store,
        object=service,
    )


def list_registry_objects(args: argparse.Namespace) -> dict[str, Any]:
    store = deploy_registry()
    objects = store.list(args.object_kind)
    if args.object_kind == "service" and args.project_id:
        objects = [item for item in objects if item.get("project_id") == args.project_id]
    return registry_payload(
        "registry_list",
        "ok",
        store,
        object_kind=args.object_kind,
        objects=objects,
    )


def doctor_strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for item in values:
        text = str(item).strip()
        if text and text not in result:
            result.append(text)
    return result


def backup_repository_path(repository: dict[str, Any]) -> Path | None:
    raw_path = str(repository.get("path") or "").strip()
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return None
    return path.resolve()


def backup_manifest_records(repository: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    path = backup_repository_path(repository)
    if path is None or not path.is_dir():
        return [], ["备份仓库路径不存在或不是目录"]
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    for manifest_path in sorted(path.glob("*.manifest.json")):
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(record, dict):
                raise ValueError("manifest 不是对象")
            records.append(record)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"{manifest_path.name}: {exc}")
    return records, warnings


def backup_path_covers(asset_path: str, target_path: str) -> bool:
    asset = posixpath.normpath(asset_path)
    target = posixpath.normpath(target_path)
    if not asset.startswith("/") or not target.startswith("/"):
        return False
    return target == asset or target.startswith(asset.rstrip("/") + "/")


def backup_asset_manifest_check(
    asset: dict[str, Any],
    repository: dict[str, Any] | None,
) -> dict[str, Any]:
    asset_id = str(asset.get("id") or "")
    if repository is None:
        return {
            "name": f"asset:{asset_id}:repository",
            "status": "missing",
            "asset_id": asset_id,
            "detail": "备份资产引用的 repository 不存在",
        }
    records, warnings = backup_manifest_records(repository)
    matches = [
        item
        for item in records
        if str(item.get("asset_id") or "") == asset_id
        and str(item.get("kind") or "") == str(asset.get("kind") or "")
    ]
    matches.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("backup_id") or "")), reverse=True)
    if not matches:
        return {
            "name": f"asset:{asset_id}:latest_backup",
            "status": "missing",
            "asset_id": asset_id,
            "repository_id": repository.get("id"),
            "warnings": warnings,
            "detail": "没有找到绑定该资产的备份 manifest",
        }
    latest = matches[0]
    manifest_status = str(latest.get("status") or "unknown")
    if manifest_status == "verified":
        status = "pass"
        detail = "最近一次绑定备份已通过校验"
    elif manifest_status == "created":
        status = "warn"
        detail = "最近一次绑定备份已生成，但尚未记录通过校验"
    elif manifest_status in {"invalid", "failed"}:
        status = "fail"
        detail = f"最近一次绑定备份状态为 {manifest_status}"
    else:
        status = "unknown"
        detail = f"无法解释最近一次备份状态：{manifest_status}"
    return {
        "name": f"asset:{asset_id}:latest_backup",
        "status": status,
        "asset_id": asset_id,
        "repository_id": repository.get("id"),
        "latest_backup": {
            "backup_id": latest.get("backup_id"),
            "kind": latest.get("kind"),
            "created_at": latest.get("created_at"),
            "status": manifest_status,
            "sha256": latest.get("sha256"),
        },
        "warnings": warnings,
        "detail": detail,
    }


def deployment_backup_coverage(
    store: registry_store.RegistryStore,
    deployment: dict[str, Any] | None,
) -> dict[str, Any]:
    if deployment is None:
        return {
            "status": "unknown",
            "checks": [],
            "assets": [],
            "repositories": [],
            "detail": "deployment 尚未登记，无法确定备份覆盖范围",
        }

    project_id = str(deployment.get("project_id") or "")
    service_id = str(deployment.get("service_id") or "")
    environment = str(deployment.get("environment") or registry_store.DEFAULT_ENVIRONMENT)
    assets = [
        item
        for item in store.list("backup_asset")
        if str(item.get("project_id") or "") == project_id
        and str(item.get("service_id") or "") == service_id
        and str(item.get("environment") or "") == environment
        and str(item.get("status") or "active") not in {"orphaned", "disabled"}
    ]
    assets_by_id = {str(item.get("id")): item for item in assets}
    checks: list[dict[str, Any]] = []
    repository_checks: list[dict[str, Any]] = []
    referenced_ids = doctor_strings(deployment.get("backup_assets"))
    for asset_id in referenced_ids:
        if asset_id not in assets_by_id:
            checks.append({
                "name": f"asset:{asset_id}:binding",
                "status": "missing",
                "asset_id": asset_id,
                "detail": "deployment 引用的备份资产不存在或绑定不匹配",
            })

    db_sources = doctor_strings(
        deployment.get("db_sources")
        or deployment.get("database_sources")
        or deployment.get("db_source")
    )
    for asset_id in referenced_ids:
        asset = assets_by_id.get(asset_id)
        if asset and str(asset.get("kind") or "") == "db":
            source = str(asset.get("db_source") or "")
            if source and source not in db_sources:
                db_sources.append(source)

    for source in db_sources:
        candidates = [
            item for item in assets
            if str(item.get("kind") or "") == "db"
            and str(item.get("db_source") or "") == source
        ]
        if not candidates:
            checks.append({
                "name": f"database:{source}",
                "kind": "db",
                "target": source,
                "status": "missing",
                "detail": "没有匹配 project/service/environment 的数据库备份资产",
            })
        else:
            checks.append({
                "name": f"database:{source}",
                "kind": "db",
                "target": source,
                "status": "pass",
                "asset_ids": [item.get("id") for item in candidates],
            })

    deployment_paths = registry_paths(deployment.get("paths"))
    file_targets: list[dict[str, str]] = [
        item for item in deployment_paths if item.get("kind") in {"data", "config"}
    ]
    for value in doctor_strings(deployment.get("environment_file")):
        if value.startswith("/"):
            file_targets.append({"kind": "config", "path": value})
    baseline = deployment.get("systemd_baseline")
    if isinstance(baseline, dict):
        for value in doctor_strings(baseline.get("environment_files")):
            if value.startswith("/"):
                file_targets.append({"kind": "config", "path": value})

    seen_targets: set[tuple[str, str]] = set()
    for target in file_targets:
        key = (target["kind"], target["path"])
        if key in seen_targets:
            continue
        seen_targets.add(key)
        candidates = [
            item for item in assets
            if str(item.get("kind") or "") == "file"
            and backup_path_covers(str(item.get("remote_path") or ""), target["path"])
        ]
        if not candidates:
            checks.append({
                "name": f"file:{target['kind']}:{target['path']}",
                "kind": "file",
                "target": target,
                "status": "missing",
                "detail": "没有覆盖该路径的文件备份资产",
            })
        else:
            checks.append({
                "name": f"file:{target['kind']}:{target['path']}",
                "kind": "file",
                "target": target,
                "status": "pass",
                "asset_ids": [item.get("id") for item in candidates],
            })

    required_asset_ids: list[str] = []
    for check in checks:
        required_asset_ids.extend(str(item) for item in check.get("asset_ids", []) if item)
    required_asset_ids.extend(asset_id for asset_id in referenced_ids if asset_id in assets_by_id)
    for asset_id in sorted(set(required_asset_ids)):
        asset = assets_by_id[asset_id]
        repository_id = str(asset.get("repository_id") or "")
        repository = store.get("backup_repository", repository_id)
        if repository is None:
            repository_checks.append({
                "name": f"repository:{repository_id or 'missing'}",
                "status": "missing",
                "asset_id": asset_id,
                "repository_id": repository_id,
                "detail": "备份资产引用的仓库不存在",
            })
            continue
        path = backup_repository_path(repository)
        if repository.get("enabled") is False:
            repository_checks.append({
                "name": f"repository:{repository_id}",
                "status": "fail",
                "asset_id": asset_id,
                "repository_id": repository_id,
                "detail": "备份仓库已停用",
            })
        elif path is None or not path.is_dir():
            repository_checks.append({
                "name": f"repository:{repository_id}",
                "status": "unknown",
                "asset_id": asset_id,
                "repository_id": repository_id,
                "detail": "备份仓库路径不存在或不是绝对路径目录",
            })
        else:
            repository_checks.append({
                "name": f"repository:{repository_id}",
                "status": "pass",
                "asset_id": asset_id,
                "repository_id": repository_id,
                "path": str(path),
            })
        checks.append(backup_asset_manifest_check(asset, repository))

    statuses = [str(item.get("status")) for item in checks + repository_checks]
    if not statuses:
        status = "not_requested"
    elif any(item in {"missing", "fail"} for item in statuses):
        status = "missing"
    elif "unknown" in statuses:
        status = "unknown"
    elif "warn" in statuses:
        status = "partial"
    else:
        status = "ok"
    return {
        "status": status,
        "project_id": project_id,
        "service_id": service_id,
        "environment": environment,
        "checks": checks,
        "assets": assets,
        "repositories": repository_checks,
        "detail": "已检查数据库、数据/配置路径、仓库可用性和最近备份校验状态",
    }


def doctor(args: argparse.Namespace) -> dict[str, Any]:
    store, project, service, environment = registered_service_context(args)
    deployment_id = registry_store.deployment_identity(
        args.project_id, args.service_id, args.environment
    )
    deployment = store.get("deployment", deployment_id)
    coverage = deployment_backup_coverage(store, deployment)
    if deployment is None:
        status = "missing"
    elif coverage.get("status") in {"missing", "unknown", "partial"}:
        status = coverage["status"]
    else:
        status = "healthy"
    return registry_payload(
        "deployment_doctor",
        status,
        store,
        deployment_id=deployment_id,
        project=project,
        service=service,
        environment=environment,
        deployment=deployment,
        backup_coverage=coverage,
        remote_write=False,
        collected_at=utc_now(),
    )


def builtin_recipe() -> dict[str, Any]:
    return {
        "id": BUILTIN_RECIPE_ID,
        "name": "tar.gz + release/current + systemd",
        "strategy": BUILTIN_RECIPE_ID,
        "stages": list(RECIPE_STAGE_NAMES),
        "checksum": "builtin",
        "managed": False,
        "builtin": True,
    }


def recipe_home() -> Path:
    configured = os.environ.get("DEPLOYX_RECIPE_HOME")
    if configured:
        return Path(configured).expanduser()
    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return data_home / "agent-ops" / "recipes"


def available_recipe(store: registry_store.RegistryStore, recipe_id: str) -> dict[str, Any]:
    if recipe_id == BUILTIN_RECIPE_ID:
        return builtin_recipe()
    recipe = store.get("recipe", recipe_id)
    if recipe is None:
        raise registry_store.RegistryError(f"recipe does not exist: {recipe_id}")
    return recipe


def parse_stage_spec(value: str) -> tuple[str, Path]:
    stage, separator, source_text = str(value).partition("=")
    stage = stage.strip()
    source = Path(source_text).expanduser() if separator else Path()
    if stage not in RECIPE_STAGE_NAMES:
        raise ValueError(f"--stage 阶段必须是：{', '.join(RECIPE_STAGE_NAMES)}")
    if not separator or not source.is_file():
        raise ValueError(f"--stage 必须指向存在的脚本文件：{value}")
    return stage, source


def register_recipe(args: argparse.Namespace) -> dict[str, Any]:
    if args.recipe_id == BUILTIN_RECIPE_ID:
        raise registry_store.RegistryError(f"内置 recipe 不允许覆盖：{BUILTIN_RECIPE_ID}")
    stages: dict[str, dict[str, str]] = {}
    for raw_stage in args.stage or []:
        stage, source = parse_stage_spec(raw_stage)
        if stage in stages:
            raise ValueError(f"--stage 不允许重复：{stage}")
        stages[stage] = {"source": str(source)}
    if not stages:
        raise ValueError("托管脚本 recipe 至少需要一个 --stage name=/path/to/script")

    store = deploy_registry()
    if store.get("recipe", args.recipe_id) is not None:
        if not args.upsert:
            raise registry_store.RegistryError(f"recipe already exists: {args.recipe_id}")
        raise registry_store.RegistryError("当前阶段不支持覆盖已有托管 recipe")

    target_root = recipe_home() / args.recipe_id
    if target_root.exists():
        raise registry_store.RegistryError(f"recipe 托管目录已存在：{target_root}")
    target_root.mkdir(parents=True, exist_ok=False)
    digest = hashlib.sha256()
    copied: dict[str, dict[str, str]] = {}
    try:
        for stage in sorted(stages):
            source = Path(stages[stage]["source"])
            target = target_root / stage
            shutil.copy2(source, target)
            content_digest = hashlib.sha256(target.read_bytes()).hexdigest()
            copied[stage] = {"path": str(target), "sha256": content_digest}
            digest.update(stage.encode("utf-8"))
            digest.update(b"\0")
            digest.update(target.read_bytes())
        recipe = store.create(
            "recipe",
            args.recipe_id,
            {
                "name": args.name,
                "strategy": "managed-script",
                "stages": copied,
                "checksum": digest.hexdigest(),
                "managed": True,
                "recipe_root": str(target_root),
            },
        )
    except Exception:
        shutil.rmtree(target_root)
        raise
    return registry_payload("registry_recipe", "created", store, object=recipe)


def list_recipes(args: argparse.Namespace) -> dict[str, Any]:
    store = deploy_registry()
    recipes = [builtin_recipe(), *store.list("recipe")]
    return registry_payload("registry_list", "ok", store, object_kind="recipe", objects=recipes)


def parse_path_spec(value: str) -> dict[str, str]:
    raw = str(value).strip()
    kind, separator, path_text = raw.partition("=")
    if not separator:
        kind, separator, path_text = raw.partition(":")
    kind = kind.strip().lower()
    path_text = path_text.strip()
    if kind not in PATH_KINDS:
        raise ValueError(f"--path 类型必须是：{', '.join(sorted(PATH_KINDS))}")
    if not separator or not path_text.startswith("/") or "\n" in path_text or "\t" in path_text:
        raise ValueError(f"--path 必须是 kind=/absolute/path：{value}")
    return {
        "kind": kind,
        "path": path_text,
        "ownership": "external" if kind == "external" else "managed",
    }


def parse_port_spec(value: str) -> dict[str, Any]:
    parts = [item.strip() for item in str(value).split(":")]
    if len(parts) < 3:
        raise ValueError("--port 格式必须是 protocol:bind_address:port[:purpose[:public]]")
    protocol, bind_address, port_text = parts[:3]
    protocol = protocol.lower()
    if protocol not in PORT_PROTOCOLS:
        raise ValueError("--port protocol 只能是 tcp 或 udp")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"--port 不是有效端口：{port_text}") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"--port 必须在 1-65535 范围内：{port}")
    purpose = parts[3] if len(parts) > 3 and parts[3] else ""
    public = parse_bool(parts[4]) if len(parts) > 4 and parts[4] else False
    return {
        "protocol": protocol,
        "bind_address": bind_address or "*",
        "port": port,
        "purpose": purpose,
        "public": public,
    }


def service_paths(args: argparse.Namespace) -> list[dict[str, str]]:
    app_root = layout(args.service_id, args.release_root)["app_root"]
    paths = [{"kind": "release", "path": app_root, "ownership": "managed"}]
    seen = {("release", app_root)}
    for raw_path in args.path or []:
        item = parse_path_spec(raw_path)
        key = (item["kind"], item["path"])
        if item["kind"] == "release" and item["path"] != app_root:
            raise ValueError("release 路径由 --release-root 和 service ID 确定，不允许覆盖")
        if key not in seen:
            seen.add(key)
            paths.append(item)
    return paths


def service_ports(args: argparse.Namespace) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw_port in args.port or []:
        item = parse_port_spec(raw_port)
        key = (item["protocol"], item["bind_address"], item["port"])
        if key in seen:
            raise ValueError(f"--port 不允许重复：{raw_port}")
        seen.add(key)
        ports.append(item)
    return ports


def path_inspect_command(paths: list[dict[str, str]]) -> str:
    lines = ["set -u"]
    for item in paths:
        if item["kind"] == "release":
            continue
        path = shlex.quote(item["path"])
        lines.extend(
            [
                f"path={path}",
                'if [ -L "$path" ]; then type=symlink; exists=true; '
                'elif [ -d "$path" ]; then type=directory; exists=true; '
                'elif [ -f "$path" ]; then type=file; exists=true; '
                'elif [ -e "$path" ]; then type=other; exists=true; '
                'else type=missing; exists=false; fi',
                f"printf 'DEPLOYX_PATH\\t%s\\t%s\\t%s\\t%s\\n' {shlex.quote(item['kind'])} \"$path\" \"$exists\" \"$type\"",
            ]
        )
    return "\n".join(lines) + "\n"


def inspect_paths(alias: str, paths: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    remote_paths = [item for item in paths if item["kind"] != "release"]
    if not remote_paths:
        return {"status": "ok", "connection_status": "ok", "paths": []}
    remote = run_ssh(alias, path_inspect_command(paths), timeout)
    if remote.get("transport") != "ok":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "paths": [],
            "error": remote.get("error") or "sshx 不可用",
        }
    try:
        exit_code = int(remote.get("exit_code", 0))
    except (TypeError, ValueError):
        exit_code = 1
    if exit_code not in (0, None):
        return {
            "status": "unknown",
            "connection_status": "ok",
            "paths": [],
            "error": trim_error(remote.get("stderr")) or "无法读取目标路径事实",
        }
    facts: list[dict[str, Any]] = []
    for line in str(remote.get("stdout", "")).splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5 or parts[0] != "DEPLOYX_PATH":
            continue
        facts.append(
            {
                "kind": parts[1],
                "path": parts[2],
                "exists": parse_bool(parts[3]),
                "type": parts[4],
            }
        )
    expected = {(item["kind"], item["path"]) for item in remote_paths}
    observed = {(item["kind"], item["path"]) for item in facts}
    if expected != observed:
        return {
            "status": "unknown",
            "connection_status": "ok",
            "paths": facts,
            "error": "目标路径事实返回不完整",
        }
    return {"status": "ok", "connection_status": "ok", "paths": facts}


def port_protocol(value: Any) -> str:
    text = str(value or "").lower()
    if text.startswith("tcp"):
        return "tcp"
    if text.startswith("udp"):
        return "udp"
    return text


def port_addresses_overlap(requested: str, observed: str) -> bool:
    requested = requested.strip().lower()
    observed = observed.strip().lower()
    wildcards = {"", "*", "0.0.0.0", "::", "[::]"}
    return requested in wildcards or observed in wildcards or requested == observed


def find_port_conflicts(
    requested: list[dict[str, Any]], observed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for wanted in requested:
        for current in observed:
            try:
                current_port = int(current.get("port"))
            except (TypeError, ValueError):
                continue
            if (
                port_protocol(wanted["protocol"]) == port_protocol(current.get("protocol"))
                and wanted["port"] == current_port
                and port_addresses_overlap(wanted["bind_address"], str(current.get("local_address") or ""))
            ):
                conflicts.append({"requested": wanted, "observed": current})
    return conflicts


def service_unit_absent(result: dict[str, Any]) -> bool:
    service = result.get("service") if isinstance(result.get("service"), dict) else {}
    load_state = str(service.get("load_state") or "").lower()
    error = str(result.get("error") or service.get("error") or "").lower()
    return load_state in {"not-found", "not_found", "absent"} or "不存在" in error or "not-found" in error


def registered_service_context(args: argparse.Namespace) -> tuple[
    registry_store.RegistryStore, dict[str, Any], dict[str, Any], dict[str, Any]
]:
    if hasattr(args, "release_root"):
        validate_spec(args.service_id, args.release_root)
    store = deploy_registry()
    project = store.get("project", args.project_id)
    service = store.get("service", args.service_id)
    environment = store.get("environment", args.environment)
    if project is None:
        raise registry_store.RegistryError(f"project does not exist: {args.project_id}")
    if service is None:
        raise registry_store.RegistryError(f"service does not exist: {args.service_id}")
    if service.get("project_id") != args.project_id:
        raise registry_store.RegistryError(
            f"service does not belong to project: {args.project_id}/{args.service_id}"
        )
    if environment is None:
        raise registry_store.RegistryError(f"environment does not exist: {args.environment}")
    return store, project, service, environment


def service_context(args: argparse.Namespace) -> tuple[
    registry_store.RegistryStore, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    store, project, service, environment = registered_service_context(args)
    recipe = available_recipe(store, args.recipe)
    return store, project, service, environment, recipe


def binding_path_specs(raw_paths: list[str] | None) -> list[dict[str, str]]:
    paths: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_path in raw_paths or []:
        item = parse_path_spec(raw_path)
        key = (item["kind"], item["path"])
        if key in seen:
            raise ValueError(f"--path 不允许重复：{raw_path}")
        seen.add(key)
        paths.append(item)
    return paths


def binding_port_specs(raw_ports: list[str] | None) -> list[dict[str, Any]]:
    ports: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for raw_port in raw_ports or []:
        item = parse_port_spec(raw_port)
        key = (item["protocol"], item["bind_address"], item["port"])
        if key in seen:
            raise ValueError(f"--port 不允许重复：{raw_port}")
        seen.add(key)
        ports.append(item)
    return ports


def registry_paths(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    paths: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        paths.append(
            {
                "kind": str(item.get("kind") or "external"),
                "path": str(item["path"]),
                "ownership": str(item.get("ownership") or "managed"),
            }
        )
    return paths


def registry_ports(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    ports: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        ports.append(
            {
                "protocol": port_protocol(item.get("protocol")),
                "bind_address": str(item.get("bind_address") or item.get("local_address") or "*"),
                "port": port,
                "purpose": str(item.get("purpose") or ""),
                "public": item.get("public"),
            }
        )
    return ports


def observed_path_specs(service_result: dict[str, Any]) -> list[dict[str, str]]:
    paths = service_result.get("paths")
    if not isinstance(paths, dict):
        return []
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_kind, values in paths.items():
        if raw_kind == "evidence" or not isinstance(values, list):
            continue
        kind = "external" if raw_kind == "other" else str(raw_kind)
        for value in values:
            path = str(value).strip()
            if not path:
                continue
            key = (kind, path)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "kind": kind,
                    "path": path,
                    "ownership": "external",
                    "observed_kind": str(raw_kind),
                }
            )
    return result


def observed_port_specs(service_result: dict[str, Any]) -> list[dict[str, Any]]:
    raw_ports = service_result.get("ports")
    if not isinstance(raw_ports, list):
        return []
    ports: list[dict[str, Any]] = []
    for item in raw_ports:
        if not isinstance(item, dict):
            continue
        try:
            port = int(item.get("port"))
        except (TypeError, ValueError):
            continue
        ports.append(
            {
                "protocol": port_protocol(item.get("protocol")),
                "bind_address": str(item.get("local_address") or "*"),
                "port": port,
                "purpose": "",
                "public": None,
                "pid": item.get("pid"),
                "process": item.get("process"),
                "observation_status": "observed",
            }
        )
    return ports


def service_systemd_facts(service_result: dict[str, Any]) -> dict[str, Any]:
    service = service_result.get("service") if isinstance(service_result.get("service"), dict) else {}
    unit_file = service_result.get("unit_file") if isinstance(service_result.get("unit_file"), dict) else {}
    exec_start = service_result.get("exec_start") if isinstance(service_result.get("exec_start"), dict) else {}
    process = service_result.get("process") if isinstance(service_result.get("process"), dict) else {}
    environment_files = service_result.get("environment_files")
    if not isinstance(environment_files, list):
        environment_files = []
    environment_paths = sorted(
        str(item.get("path"))
        for item in environment_files
        if isinstance(item, dict) and item.get("path")
    )
    systemd = service_result.get("systemd") if isinstance(service_result.get("systemd"), dict) else {}
    drop_ins = service_result.get("drop_ins")
    drop_in_paths = sorted(
        str(item.get("path"))
        for item in (drop_ins if isinstance(drop_ins, list) else [])
        if isinstance(item, dict) and item.get("path")
    )
    return {
        "unit": str(service.get("name") or ""),
        "service_manager": str(service.get("manager") or ""),
        "load_state": str(service.get("load_state") or ""),
        "active": str(service.get("active") or ""),
        "substate": str(service.get("substate") or ""),
        "unit_file_path": unit_file.get("path"),
        "drop_in_paths": drop_in_paths,
        "exec_start_main": str(exec_start.get("main") or ""),
        "user": str(service_result.get("user") or process.get("user") or ""),
        "working_directory": service_result.get("working_directory") or process.get("cwd"),
        "environment_files": environment_paths,
        "dependencies": service_result.get("dependencies") if isinstance(service_result.get("dependencies"), dict) else {},
        "control_group": systemd.get("control_group"),
    }


def compare_expected_values(
    expected: dict[str, Any], observed: dict[str, Any], fields: list[tuple[str, str]]
) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    checked = False
    for expected_key, observed_key in fields:
        value = expected.get(expected_key)
        if value in (None, "", []):
            continue
        checked = True
        actual = observed.get(observed_key)
        if isinstance(value, list):
            expected_value = sorted(str(item) for item in value)
            actual_value = sorted(str(item) for item in (actual or []))
            equal = expected_value == actual_value
        else:
            expected_value = str(value)
            actual_value = str(actual or "")
            equal = expected_value == actual_value
        if not equal:
            differences.append(
                {
                    "field": expected_key,
                    "expected": value,
                    "observed": actual,
                }
            )
    return {
        "status": "drifted" if differences else ("in_sync" if checked else "not_requested"),
        "expected": expected,
        "observed": observed,
        "differences": differences,
    }


def service_systemd_drift(
    args: argparse.Namespace,
    deployment: dict[str, Any] | None,
    service_result: dict[str, Any],
) -> dict[str, Any]:
    expected: dict[str, Any] = {
        "unit": args.unit,
        "service_manager": args.service_manager,
    }
    if deployment:
        for key in ("unit", "service_manager", "run_user", "working_directory", "start_command"):
            if deployment.get(key) not in (None, ""):
                expected[key] = deployment[key]
        baseline = deployment.get("systemd_baseline")
        if isinstance(baseline, dict):
            expected.update(baseline)
        if deployment.get("environment_file"):
            expected["environment_files"] = [str(deployment["environment_file"])]
    observed = service_systemd_facts(service_result)
    if service_result.get("status") not in {"ok", "partial"}:
        return {
            "status": "unknown",
            "expected": expected,
            "observed": observed,
            "differences": [],
            "detail": service_result.get("error") or "systemd 服务事实不可用",
        }
    return compare_expected_values(
        expected,
        observed,
        [
            ("unit", "unit"),
            ("service_manager", "service_manager"),
            ("unit_file_path", "unit_file_path"),
            ("drop_in_paths", "drop_in_paths"),
            ("exec_start_main", "exec_start_main"),
            ("start_command", "exec_start_main"),
            ("user", "user"),
            ("run_user", "user"),
            ("working_directory", "working_directory"),
            ("environment_files", "environment_files"),
        ],
    )


def service_paths_drift(
    expected_paths: list[dict[str, str]], service_result: dict[str, Any]
) -> dict[str, Any]:
    observed = observed_path_specs(service_result)
    if not expected_paths:
        return {"status": "not_requested", "expected": [], "observed": observed, "differences": []}
    if service_result.get("status") not in {"ok", "partial"}:
        return {
            "status": "unknown",
            "expected": expected_paths,
            "observed": observed,
            "differences": [],
            "detail": service_result.get("error") or "路径事实不可用",
        }
    observed_keys = {(item["kind"], item["path"]) for item in observed}
    missing = [
        item for item in expected_paths if (item.get("kind"), item.get("path")) not in observed_keys
    ]
    return {
        "status": "drifted" if missing else "in_sync",
        "expected": expected_paths,
        "observed": observed,
        "differences": [
            {"type": "missing", "expected": item} for item in missing
        ],
    }


def service_ports_drift(
    expected_ports: list[dict[str, Any]], service_result: dict[str, Any]
) -> dict[str, Any]:
    observed = observed_port_specs(service_result)
    if not expected_ports:
        return {"status": "not_requested", "expected": [], "observed": observed, "differences": []}
    if service_result.get("status") not in {"ok", "partial"} or service_result.get("ports_status") == "unknown":
        return {
            "status": "unknown",
            "expected": expected_ports,
            "observed": observed,
            "differences": [],
            "detail": service_result.get("error") or "监听端口事实不可用",
        }
    differences: list[dict[str, Any]] = []
    for expected in expected_ports:
        matches = find_port_conflicts([expected], observed)
        if not matches:
            differences.append({"type": "missing", "expected": expected})
    return {
        "status": "drifted" if differences else "in_sync",
        "expected": expected_ports,
        "observed": observed,
        "differences": differences,
    }


def service_drift(
    args: argparse.Namespace,
    deployment: dict[str, Any] | None,
    service_result: dict[str, Any],
    explicit_paths: list[dict[str, str]],
    explicit_ports: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_paths = explicit_paths or registry_paths(deployment.get("paths") if deployment else None)
    expected_ports = explicit_ports or registry_ports(deployment.get("ports") if deployment else None)
    checks = {
        "systemd": service_systemd_drift(args, deployment, service_result),
        "paths": service_paths_drift(expected_paths, service_result),
        "ports": service_ports_drift(expected_ports, service_result),
    }
    statuses = [str(item.get("status")) for item in checks.values()]
    if "drifted" in statuses:
        status = "drifted"
    elif "unknown" in statuses:
        status = "unknown"
    elif all(item in {"not_requested", "in_sync"} for item in statuses):
        status = "in_sync" if deployment or explicit_paths or explicit_ports else "not_registered"
    else:
        status = "unknown"
    differences: list[dict[str, Any]] = []
    for name, check in checks.items():
        for difference in check.get("differences", []):
            differences.append({"check": name, **difference})
    return {
        "status": status,
        "checks": checks,
        "differences": differences,
        "expected_paths": expected_paths,
        "expected_ports": expected_ports,
    }


def classify_service_layout(
    target_result: dict[str, Any], service_result: dict[str, Any]
) -> dict[str, Any]:
    inspection = target_result.get("inspection") if isinstance(target_result, dict) else None
    if inspection is None:
        return {
            "mode": "unknown",
            "status": "unknown",
            "source_path": None,
            "evidence": [],
            "detail": target_result.get("error") or "部署目录事实不可用",
        }
    app_root = str(inspection.get("app_root") or "")
    app_exists = bool(inspection.get("app_root_exists"))
    has_current = bool(str(inspection.get("current_target") or "").strip())
    has_releases = bool(inspection.get("releases_root_exists")) or bool(inspection.get("releases"))
    if app_exists and has_current and has_releases:
        return {
            "mode": "versioned",
            "status": "ok",
            "source_path": app_root,
            "evidence": ["app_root_exists", "current_target", "releases_root_exists"],
        }
    if app_exists and not has_current and not has_releases:
        return {
            "mode": "legacy_single_directory",
            "status": "ok",
            "source_path": app_root,
            "evidence": ["app_root_exists", "current_target_empty", "releases_root_absent"],
            "detail": "目标应用目录存在，但没有 current 链接和 releases 目录",
        }
    if app_exists and has_releases and not has_current:
        return {
            "mode": "partial_versioned",
            "status": "partial",
            "source_path": app_root,
            "evidence": ["app_root_exists", "releases_root_exists", "current_target_empty"],
            "detail": "存在 releases 目录但缺少 current 链接",
        }
    service_status = str(service_result.get("status") or "")
    if service_status in {"ok", "partial"}:
        return {
            "mode": "external_or_unmapped",
            "status": "partial",
            "source_path": None,
            "evidence": ["service_present", "app_root_absent"],
            "detail": "服务存在，但未发现约定的版本化应用目录",
        }
    return {
        "mode": "unknown",
        "status": "unknown",
        "source_path": app_root or None,
        "evidence": [],
        "detail": "无法确认服务目录布局",
    }


def registered_fact_conflicts(
    store: registry_store.RegistryStore,
    current_id: str,
    ssh_alias: str,
    observed_paths: list[dict[str, str]],
    observed_ports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    observed_path_keys = {(item["kind"], item["path"]) for item in observed_paths}
    for deployment in store.list("deployment"):
        deployment_id = str(deployment.get("id") or "")
        if deployment_id == current_id or str(deployment.get("ssh_alias") or "") != ssh_alias:
            continue
        lifecycle = str(deployment.get("management_status") or deployment.get("status") or "")
        if lifecycle == "orphaned":
            continue
        for path in registry_paths(deployment.get("paths")):
            if (path["kind"], path["path"]) in observed_path_keys:
                conflicts.append(
                    {
                        "type": "path",
                        "deployment_id": deployment_id,
                        "path": path,
                        "detail": "监听服务事实与其它已登记 deployment 共享路径",
                    }
                )
        for port in find_port_conflicts(registry_ports(deployment.get("ports")), observed_ports):
            conflicts.append(
                {
                    "type": "port",
                    "deployment_id": deployment_id,
                    "requested": port["requested"],
                    "observed": port["observed"],
                    "detail": "监听端口与其它已登记 deployment 冲突",
                }
            )
    return conflicts


def service_observation(args: argparse.Namespace) -> dict[str, Any]:
    store, project, service, environment = registered_service_context(args)
    deployment_id = registry_store.deployment_identity(
        args.project_id, args.service_id, args.environment
    )
    deployment = store.get("deployment", deployment_id)
    explicit_paths = binding_path_specs(args.path)
    explicit_ports = binding_port_specs(args.port)
    target_result = inspect_target(args.ssh_alias, args.service_id, args.release_root, args.timeout)
    service_result = run_host_service_inspect(args.ssh_alias, args.unit, args.timeout)
    drift = service_drift(args, deployment, service_result, explicit_paths, explicit_ports)
    layout = classify_service_layout(target_result, service_result)
    conflicts = registered_fact_conflicts(
        store,
        deployment_id,
        args.ssh_alias,
        observed_path_specs(service_result),
        observed_port_specs(service_result),
    )
    return {
        "store": store,
        "project": project,
        "service": service,
        "environment": environment,
        "ssh_alias": args.ssh_alias,
        "release_root": args.release_root,
        "deployment_id": deployment_id,
        "deployment": deployment,
        "target_result": target_result,
        "service_result": service_result,
        "drift": drift,
        "layout": layout,
        "conflicts": conflicts,
        "explicit_paths": explicit_paths,
        "explicit_ports": explicit_ports,
    }


def observation_connection_status(observation: dict[str, Any]) -> str:
    for key in ("target_result", "service_result"):
        result = observation[key]
        if result.get("connection_status") == "unavailable":
            return "unavailable"
    return "ok"


def observation_status(observation: dict[str, Any]) -> str:
    target_result = observation["target_result"]
    service_result = observation["service_result"]
    if observation_connection_status(observation) == "unavailable":
        return "unavailable"
    if service_result.get("status") == "failed":
        return "failed"
    if target_result.get("status") == "failed":
        return "partial"
    if observation["conflicts"] or observation["drift"].get("status") == "drifted":
        return "drifted"
    if observation["drift"].get("status") == "unknown" or observation["layout"].get("status") == "unknown":
        return "partial"
    if observation["deployment"] is None:
        return "observed"
    if target_result.get("status") == "partial" or service_result.get("status") == "partial":
        return "partial"
    return "ok"


def service_observation_payload(
    observation: dict[str, Any], kind: str = "service_inspect"
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "status": observation_status(observation),
        "connection_status": observation_connection_status(observation),
        "target": target_ref(observation["ssh_alias"]),
        "project": observation["project"],
        "service": observation["service"],
        "environment": observation["environment"],
        "deployment_id": observation["deployment_id"],
        "registered_deployment": observation["deployment"],
        "registration_status": "registered" if observation["deployment"] else "unregistered",
        "release_root": observation["release_root"],
        "ssh_alias": observation["ssh_alias"],
        "unit": observation["service_result"].get("service", {}).get("name")
        if isinstance(observation["service_result"].get("service"), dict)
        else None,
        "legacy_mode": observation["layout"].get("mode"),
        "layout": observation["layout"],
        "drift": observation["drift"],
        "conflicts": observation["conflicts"],
        "observed": {
            "deployment_directory": observation["target_result"],
            "service_inspect": observation["service_result"],
        },
        "remote_write": False,
        "registry_write": False,
        "collected_at": utc_now(),
    }


def adopted_systemd_baseline(service_result: dict[str, Any]) -> dict[str, Any]:
    facts = service_systemd_facts(service_result)
    return {
        key: facts.get(key)
        for key in (
            "unit",
            "service_manager",
            "unit_file_path",
            "drop_in_paths",
            "exec_start_main",
            "user",
            "working_directory",
            "environment_files",
        )
        if facts.get(key) not in (None, "", [])
    }


def adopted_fields(observation: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    service_result = observation["service_result"]
    paths = observed_path_specs(service_result)
    ports = observed_port_specs(service_result)
    return {
        "project_id": args.project_id,
        "service_id": args.service_id,
        "environment": args.environment,
        "ssh_alias": args.ssh_alias,
        "strategy": "legacy-single-directory" if observation["layout"].get("mode") == "legacy_single_directory" else "observed-systemd",
        "service_manager": args.service_manager,
        "unit": args.unit,
        "paths": paths,
        "ports": ports,
        "health_checks": [],
        "status": "adopted",
        "management_status": "adopted",
        "legacy_mode": observation["layout"].get("mode"),
        "systemd_baseline": adopted_systemd_baseline(service_result),
        "observed": {
            "deployment_directory": observation["target_result"],
            "service_inspect": service_result,
            "drift": observation["drift"],
            "conflicts": observation["conflicts"],
        },
        "remote_write_performed": False,
        "adoption_confirmed": True,
        "adopted_at": utc_now(),
    }


def service_inspect(args: argparse.Namespace) -> dict[str, Any]:
    observation = service_observation(args)
    return service_observation_payload(observation)


def service_adopt(args: argparse.Namespace) -> dict[str, Any]:
    observation = service_observation(args)
    payload = service_observation_payload(observation, kind="service_adopt")
    payload["confirmation_required"] = True
    payload["remote_write"] = False
    if not args.confirm:
        payload.update(
            {
                "status": "blocked",
                "error": "既有服务接管会写入本地注册表；请显式提供 --confirm，远端仍不会写入",
                "registry_write": False,
            }
        )
        return payload
    if payload["connection_status"] == "unavailable" or observation["service_result"].get("status") not in {"ok", "partial"}:
        payload.update(
            {
                "status": "blocked",
                "error": observation["service_result"].get("error") or "无法确认既有服务事实",
            }
        )
        return payload
    if observation["conflicts"]:
        payload.update({"status": "blocked", "error": "发现已登记的路径或端口冲突，未写入注册表"})
        return payload
    if observation["drift"].get("status") in {"drifted", "unknown"}:
        payload.update({"status": "blocked", "error": "当前事实与已有 deployment 不一致或无法确认，未写入注册表"})
        return payload
    existing = observation["deployment"]
    existing_status = str(existing.get("management_status") or existing.get("status") or "") if existing else ""
    if existing and existing_status not in {"adopted", "observed", "external"}:
        payload.update(
            {
                "status": "blocked",
                "error": f"deployment 已存在且状态为 {existing_status or 'unknown'}，接管不会覆盖它",
            }
        )
        return payload
    fields = adopted_fields(observation, args)
    if existing:
        adopted = observation["store"].update("deployment", observation["deployment_id"], fields)
        operation = "updated"
    else:
        adopted = observation["store"].create("deployment", observation["deployment_id"], fields)
        operation = "created"
    payload.update(
        {
            "status": "adopted",
            "registry_write": {
                "status": "ok",
                "operation": operation,
                "revision": observation["store"].load().get("revision", 0),
            },
            "deployment": adopted,
            "management_status": "adopted",
            "finished_at": utc_now(),
        }
    )
    return payload


def service_migration_plan(args: argparse.Namespace) -> dict[str, Any]:
    observation = service_observation(args)
    payload = service_observation_payload(observation, kind="service_migration_plan")
    layout_facts = observation["layout"]
    preconditions: list[dict[str, str]] = []
    service_result = observation["service_result"]
    if service_result.get("status") in {"ok", "partial"}:
        preconditions.append(check_status("service", "pass", "已读取 systemd、进程、端口和路径事实"))
    elif service_result.get("connection_status") == "unavailable":
        preconditions.append(check_status("service", "unknown", "hostx/SSH 不可用"))
    else:
        preconditions.append(check_status("service", "fail", str(service_result.get("error") or "服务事实不可用")))
    mode = layout_facts.get("mode")
    if mode == "legacy_single_directory":
        preconditions.append(check_status("legacy_layout", "pass", "已确认传统单目录布局"))
    elif mode == "versioned":
        preconditions.append(check_status("legacy_layout", "pass", "目标目录已经是版本化布局，无需迁移"))
    elif mode == "external_or_unmapped":
        preconditions.append(check_status("legacy_layout", "unknown", str(layout_facts.get("detail") or "未发现可迁移的目标目录")))
    else:
        preconditions.append(check_status("legacy_layout", "unknown", str(layout_facts.get("detail") or "无法确认目标目录布局")))
    drift_status = observation["drift"].get("status")
    if drift_status == "drifted":
        preconditions.append(check_status("drift", "fail", "systemd、路径或端口事实与注册表不一致"))
    elif drift_status == "unknown":
        preconditions.append(check_status("drift", "unknown", "部分事实不可确认"))
    else:
        preconditions.append(check_status("drift", "pass", "未发现已登记事实冲突"))
    if observation["conflicts"]:
        preconditions.append(check_status("registry_conflicts", "fail", "发现其它 deployment 已占用相同路径或端口"))
    else:
        preconditions.append(check_status("registry_conflicts", "pass", "未发现其它 deployment 的路径或端口冲突"))
    statuses = {item["status"] for item in preconditions}
    if mode == "versioned":
        status = "not_applicable"
    elif observation_connection_status(observation) == "unavailable":
        status = "unavailable"
    elif "fail" in statuses or "unknown" in statuses:
        status = "blocked"
    else:
        status = "ready"
    source_path = layout_facts.get("source_path")
    target_paths = layout(args.service_id, args.release_root)
    payload.update(
        {
            "status": status,
            "preconditions": preconditions,
            "migration": {
                "source_layout": mode,
                "source_path": source_path,
                "target_layout": target_paths,
                "preserve_paths": observed_path_specs(service_result),
                "requires_artifact": True,
                "remote_write": False,
                "stages": [
                    "确认备份和恢复验证覆盖",
                    "准备可校验的 tar.gz release 制品",
                    "在新 releases 目录落盘并保留 legacy 现场",
                    "生成 current 链接并执行同一套服务/端口/健康检查",
                    "确认稳定后再由用户单独执行迁移切换",
                ] if mode == "legacy_single_directory" else [],
            },
            "remote_write": False,
            "registry_write": False,
            "actions": [
                "本轮只保存迁移计划，不移动、覆盖或删除 legacy 目录",
                "迁移前必须明确备份资产、配置、数据和日志路径",
                "迁移执行需要后续显式命令和用户确认",
            ] if mode == "legacy_single_directory" else [],
            "finished_at": utc_now(),
        }
    )
    return payload


def creation_stages(preflight_status: str) -> list[dict[str, Any]]:
    return [
        {
            "name": "draft",
            "status": "ready",
            "remote_write": False,
            "action": "登记 draft deployment",
        },
        {
            "name": "preflight",
            "status": preflight_status,
            "remote_write": False,
            "action": "检查 SSH、systemd、端口、路径、磁盘和制品",
        },
        {"name": "prepare", "status": "not_started", "remote_write": True, "action": "创建受管目录"},
        {"name": "install", "status": "not_started", "remote_write": True, "action": "上传并校验制品"},
        {"name": "configure", "status": "not_started", "remote_write": True, "action": "写入配置、环境文件和 service unit"},
        {"name": "activate", "status": "not_started", "remote_write": True, "action": "daemon-reload 并启动服务"},
        {"name": "verify", "status": "not_started", "remote_write": False, "action": "检查进程、端口和健康"},
        {"name": "rollback", "status": "not_started", "remote_write": True, "action": "失败时回退本轮新建资源"},
        {"name": "managed", "status": "not_started", "remote_write": False, "action": "写入 managed 状态并建立资产关系"},
    ]


def service_create_preflight(args: argparse.Namespace) -> dict[str, Any]:
    store, project, service, environment, recipe = service_context(args)
    artifact = artifact_info(args.artifact, args.artifact_sha256)
    paths = service_paths(args)
    ports = service_ports(args)
    inspection_result = inspect_target(args.ssh_alias, args.service_id, args.release_root, args.timeout)
    inspection = inspection_result.get("inspection")
    service_result = run_host_service_inspect(args.ssh_alias, args.unit, args.timeout)
    port_result = run_host_ports(args.ssh_alias, args.timeout) if ports else {
        "status": "not_requested",
        "connection_status": "not_requested",
        "ports": [],
    }
    path_result = inspect_paths(args.ssh_alias, paths, args.timeout)
    preconditions: list[dict[str, str]] = []

    if artifact["status"] == "ok":
        preconditions.append(check_status("artifact", "pass", "本地 tar.gz 制品可读且校验通过"))
    else:
        preconditions.append(check_status("artifact", "fail", str(artifact.get("error") or "制品不可用")))

    if inspection is None:
        preconditions.append(check_status("remote_target", "unknown", "无法读取目标发布目录"))
        preconditions.append(check_status("disk_space", "unknown", "无法读取目标主机可用空间"))
    else:
        if inspection.get("app_root_exists"):
            preconditions.append(check_status("remote_target", "fail", "受管应用目录已存在，create 不覆盖既有目录"))
        elif inspection.get("release_root_is_dir") or inspection.get("release_root_parent_exists"):
            preconditions.append(check_status("remote_target", "pass", "目标发布根目录可用，应用目录尚不存在"))
        else:
            preconditions.append(check_status("remote_target", "fail", "发布根目录及其父目录均不可用"))
        available = inspection.get("available_bytes")
        artifact_bytes = artifact.get("bytes")
        if available is None:
            preconditions.append(check_status("disk_space", "unknown", "目标主机未返回可用空间"))
        elif artifact_bytes is not None and available < artifact_bytes:
            preconditions.append(check_status("disk_space", "fail", f"可用空间 {available} 小于制品大小 {artifact_bytes}"))
        else:
            preconditions.append(check_status("disk_space", "pass", f"available_bytes={available}"))

    if service_result.get("connection_status") == "unavailable":
        preconditions.append(check_status("service_unit", "unknown", str(service_result.get("error") or "hostx 不可用")))
    elif service_unit_absent(service_result):
        preconditions.append(check_status("service_unit", "pass", "service unit 尚未安装，等待 create 阶段安装"))
    elif service_result.get("status") in {"ok", "partial"}:
        preconditions.append(check_status("service_unit", "fail", "service unit 已存在，新服务 create 不覆盖既有服务"))
    else:
        preconditions.append(check_status("service_unit", "unknown", str(service_result.get("error") or "service unit 状态未知")))

    if ports:
        if port_result.get("connection_status") == "unavailable":
            preconditions.append(check_status("ports", "unknown", str(port_result.get("error") or "无法读取监听端口")))
        elif port_result.get("status") not in {"ok", "partial"}:
            preconditions.append(check_status("ports", "unknown", str(port_result.get("error") or "监听端口状态未知")))
        else:
            conflicts = find_port_conflicts(ports, port_result.get("ports", []))
            if conflicts:
                detail = "; ".join(
                    f"{item['observed'].get('protocol')}:{item['observed'].get('local_address')}:{item['observed'].get('port')}"
                    for item in conflicts
                )
                preconditions.append(check_status("ports", "fail", f"请求端口已被占用：{detail}"))
            else:
                preconditions.append(check_status("ports", "pass", f"已检查 {len(ports)} 个请求端口"))
    else:
        preconditions.append(check_status("ports", "pass", "未声明端口"))

    if path_result.get("connection_status") == "unavailable":
        preconditions.append(check_status("paths", "unknown", str(path_result.get("error") or "无法读取目标路径")))
    elif path_result.get("status") != "ok":
        preconditions.append(check_status("paths", "unknown", str(path_result.get("error") or "目标路径事实未知")))
    else:
        path_facts = {(item["kind"], item["path"]): item for item in path_result.get("paths", [])}
        path_statuses: list[str] = []
        for item in paths:
            if item["kind"] == "release":
                continue
            fact = path_facts.get((item["kind"], item["path"]))
            if fact and fact.get("exists"):
                path_statuses.append("warn")
                preconditions.append(check_status(
                    f"path:{item['kind']}",
                    "warn",
                    f"路径已存在，将保留现场不覆盖：{item['path']}",
                ))
            else:
                path_statuses.append("pass")
                preconditions.append(check_status(f"path:{item['kind']}", "pass", f"路径可由 create 阶段准备：{item['path']}"))
        if not path_statuses and not paths:
            preconditions.append(check_status("paths", "pass", "未声明额外路径"))

    local_project_path = args.local_project_path or str(project.get("local_path") or "")
    if local_project_path and Path(local_project_path).expanduser().is_dir():
        preconditions.append(check_status("project_source", "pass", f"local_path={local_project_path}"))
    elif local_project_path:
        preconditions.append(check_status("project_source", "warn", f"项目源码路径当前不可读：{local_project_path}"))
    else:
        preconditions.append(check_status("project_source", "unknown", "未登记项目源码路径；制品仍可单独作为来源"))

    statuses = {item["status"] for item in preconditions}
    if inspection_result.get("connection_status") == "unavailable":
        result_status = "unavailable"
    elif "fail" in statuses or "unknown" in statuses:
        result_status = "blocked"
    else:
        result_status = "ready"
    connection_status = "unavailable" if any(
        item.get("connection_status") == "unavailable"
        for item in (inspection_result, service_result, port_result, path_result)
    ) else "ok"
    deployment_id = registry_store.deployment_identity(
        args.project_id, args.service_id, args.environment
    )
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "service_create_plan",
        "status": result_status,
        "connection_status": connection_status,
        "target": target_ref(args.ssh_alias),
        "deployment_id": deployment_id,
        "project": project,
        "service": service,
        "environment": environment,
        "recipe": recipe,
        "artifact": artifact,
        "release_root": args.release_root,
        "unit": args.unit,
        "service_manager": args.service_manager,
        "paths": paths,
        "ports": ports,
        "health_checks": health_specs(args.health_check),
        "preconditions": preconditions,
        "observed": {
            "deployment_directory": inspection_result,
            "service_inspect": service_result,
            "ports": port_result,
            "paths": path_result,
        },
        "creation_stages": creation_stages(result_status),
        "remote_write": False,
        "registry_write": False,
        "actions": [
            "登记 draft deployment",
            "创建受管目录并保留已有路径现场",
            "上传并校验 tar.gz 制品",
            "写入配置、环境文件和 systemd unit",
            "daemon-reload、启动服务并检查进程/端口/健康",
            "成功后写入 managed 状态并建立备份资产关系",
        ],
        "warnings": list(inspection.get("warnings", [])) if inspection else [],
        "collected_at": utc_now(),
    }
    return payload


def service_plan(args: argparse.Namespace) -> dict[str, Any]:
    return service_create_preflight(args)


def service_draft_fields(args: argparse.Namespace, plan_payload: dict[str, Any]) -> dict[str, Any]:
    project = plan_payload["project"]
    artifact = plan_payload["artifact"]
    local_project_path = args.local_project_path or str(project.get("local_path") or "")
    fields: dict[str, Any] = {
        "project_id": args.project_id,
        "service_id": args.service_id,
        "environment": args.environment,
        "ssh_alias": args.ssh_alias,
        "local_project_path": local_project_path,
        "artifact_path": artifact.get("path"),
        "artifact_format": artifact.get("format"),
        "artifact_sha256": artifact.get("sha256"),
        "strategy": plan_payload["recipe"].get("strategy"),
        "recipe_id": plan_payload["recipe"].get("id"),
        "service_manager": args.service_manager,
        "unit": args.unit,
        "paths": plan_payload["paths"],
        "ports": plan_payload["ports"],
        "health_checks": plan_payload["health_checks"],
        "retention": {"keep_releases": args.keep_releases},
        "status": "draft",
        "management_status": "draft",
        "plan_status": plan_payload["status"],
        "creation_stages": plan_payload["creation_stages"],
        "preconditions": plan_payload["preconditions"],
        "observed": plan_payload["observed"],
        "remote_write_performed": False,
        "drafted_at": utc_now(),
    }
    for field in ("start_command", "run_user", "working_directory", "environment_file"):
        value = getattr(args, field, None)
        if value:
            fields[field] = value
    if args.db_source:
        fields["db_sources"] = list(args.db_source)
    if args.backup_asset:
        fields["backup_assets"] = list(args.backup_asset)
    return fields


def service_create(args: argparse.Namespace) -> dict[str, Any]:
    plan_payload = service_create_preflight(args)
    store = deploy_registry()
    deployment_id = plan_payload["deployment_id"]
    existing = store.get("deployment", deployment_id)
    if existing and str(existing.get("status") or existing.get("management_status") or "") not in {"", "draft"}:
        raise registry_store.RegistryError(
            f"deployment 已存在且不是 draft，不能由 service create 覆盖：{deployment_id}"
        )
    stages = [dict(stage) for stage in plan_payload["creation_stages"]]
    stages[0]["status"] = "completed"
    plan_stage = next(stage for stage in stages if stage["name"] == "preflight")
    plan_stage["status"] = plan_payload["status"]
    plan_payload["creation_stages"] = stages
    fields = service_draft_fields(args, plan_payload)
    draft = store.upsert("deployment", deployment_id, fields)
    payload = dict(plan_payload)
    payload.update(
        {
            "kind": "service_create",
            "status": "drafted",
            "plan_status": plan_payload["status"],
            "registry_write": {
                "status": "ok",
                "operation": "updated" if existing else "created",
                "revision": store.load().get("revision", 0),
            },
            "draft": draft,
            "remote_write": False,
            "finished_at": utc_now(),
        }
    )
    return payload


def directory_current_release(inspection: dict[str, Any]) -> dict[str, Any] | None:
    spec = inspection.get("deployment_spec")
    if not isinstance(spec, dict):
        return None
    release_id = str(spec.get("current_release") or spec.get("release_id") or "")
    if not release_id:
        return None
    manifest = next(
        (
            item for item in inspection.get("manifests", [])
            if str(item.get("release_id") or "") == release_id
        ),
        None,
    )
    if manifest:
        return {**manifest, "path": inspection.get("live_path"), "state": "active"}
    return {
        "release_id": release_id,
        "path": inspection.get("live_path"),
        "state": "active",
        "artifact_sha256": str(spec.get("artifact_sha256") or ""),
        "created_at": str(spec.get("finished_at") or spec.get("started_at") or ""),
    }


def legacy_release_id(inspection: dict[str, Any]) -> str:
    source = str(inspection.get("entry_sha256") or inspection.get("live_baseline") or utc_now())
    return "legacy-" + hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]


def directory_plan(args: argparse.Namespace) -> dict[str, Any]:
    if args.service_type != "nginx-static":
        raise ValueError("directory-swap 首版只支持 --service-type nginx-static")
    if not args.live_path:
        raise ValueError("directory-swap 必须提供 --live-path")
    if not args.nginx_server_name:
        raise ValueError("nginx-static 必须提供 --nginx-server-name")
    artifact = artifact_info(args.artifact, args.artifact_sha256)
    checks = application_health_specs(args.health_check)
    inspection_result = inspect_directory_target(
        args.alias,
        args.app,
        args.live_path,
        args.state_root,
        args.nginx_server_name,
        args.timeout,
    )
    paths = inspection_result["paths"]
    inspection = inspection_result.get("inspection")
    preconditions: list[dict[str, str]] = []
    preconditions.append(
        check_status(
            "artifact",
            "pass" if artifact.get("status") == "ok" else "fail",
            "本地 tar.gz 制品可读且安全检查通过"
            if artifact.get("status") == "ok"
            else str(artifact.get("error") or "制品不可用"),
        )
    )
    if inspection is None:
        for name, detail in (
            ("live_path", "无法读取历史线上目录"),
            ("filesystem", "无法确认线上目录与状态目录的文件系统"),
            ("disk_space", "无法读取可用空间"),
            ("nginx_binding", "无法读取 Nginx 绑定"),
        ):
            preconditions.append(check_status(name, "unknown", detail))
    else:
        if inspection.get("live_exists") and inspection.get("live_is_dir"):
            preconditions.append(check_status("live_path", "pass", paths["live_path"]))
        else:
            preconditions.append(check_status("live_path", "fail", "历史线上路径不存在或不是目录"))
        live_device = str(inspection.get("live_device") or "")
        state_device = str(inspection.get("state_device") or "")
        if live_device and state_device and live_device == state_device:
            preconditions.append(check_status("filesystem", "pass", f"device={live_device}"))
        elif live_device and state_device:
            preconditions.append(check_status("filesystem", "fail", "live_path 与 state_root 不在同一文件系统"))
        else:
            preconditions.append(check_status("filesystem", "unknown", "无法确认目录设备号"))
        required_bytes = int(artifact.get("unpacked_bytes") or 0) + int(inspection.get("live_bytes") or 0)
        required_bytes += max(int(artifact.get("bytes") or 0), required_bytes // 10)
        available = inspection.get("available_bytes")
        if available is None:
            preconditions.append(check_status("disk_space", "unknown", "目标主机未返回可用空间"))
        elif int(available) < required_bytes:
            preconditions.append(
                check_status("disk_space", "fail", f"available_bytes={available}, required_bytes={required_bytes}")
            )
        else:
            preconditions.append(
                check_status("disk_space", "pass", f"available_bytes={available}, required_bytes={required_bytes}")
            )
        if not inspection.get("nginx_available"):
            preconditions.append(check_status("nginx_binding", "fail", "目标主机未安装 nginx 命令"))
        elif inspection.get("nginx_test") != "pass":
            preconditions.append(check_status("nginx_binding", "fail", "nginx -t 未通过"))
        elif not inspection.get("nginx_server_found"):
            preconditions.append(
                check_status("nginx_binding", "fail", f"未找到 server_name {args.nginx_server_name}")
            )
        elif not inspection.get("nginx_root_found"):
            preconditions.append(
                check_status("nginx_binding", "fail", f"Nginx root/alias 未指向 {paths['live_path']}")
            )
        else:
            preconditions.append(check_status("nginx_binding", "pass", "Nginx 线上目录绑定一致"))
        if inspection.get("active_lock"):
            preconditions.append(check_status("deployment_lock", "fail", "已有部署操作持有目录锁"))
        else:
            preconditions.append(check_status("deployment_lock", "pass", "当前没有进行中的目录切换"))
    if checks:
        preconditions.append(check_status("application_health", "pass", f"已配置 {len(checks)} 个 HTTP 检查"))
    else:
        preconditions.append(check_status("application_health", "fail", "directory-swap 至少需要一个 HTTP 健康检查"))
    statuses = {item["status"] for item in preconditions}
    if inspection_result.get("connection_status") == "unavailable":
        status = "unavailable"
    elif "fail" in statuses or "unknown" in statuses:
        status = "blocked"
    else:
        status = "ready"
    candidate_release = release_id_for(artifact) if artifact.get("sha256") else None
    current = directory_current_release(inspection) if inspection else None
    management_status = "managed" if current else "legacy_unmanaged"
    payload = directory_base_payload("deployment_plan", args.alias, args.app, paths)
    payload.update(
        {
            "status": status,
            "connection_status": inspection_result.get("connection_status"),
            "management_status": management_status,
            "artifact": artifact,
            "current_release": current,
            "candidate_release": {
                "release_id": candidate_release,
                "artifact_name": artifact.get("name"),
                "artifact_sha256": artifact.get("sha256"),
                "state": "candidate" if artifact.get("status") == "ok" else "invalid",
            },
            "baseline": {
                "live": inspection.get("live_baseline") if inspection else None,
                "entry_sha256": inspection.get("entry_sha256") if inspection else None,
                "current_release": current.get("release_id") if current else None,
            },
            "paths": paths,
            "nginx": {
                "server_name": args.nginx_server_name,
                "binding_status": next(
                    (item["status"] for item in preconditions if item["name"] == "nginx_binding"),
                    "unknown",
                ),
            },
            "application_health_checks": checks,
            "preconditions": preconditions,
            "actions": [
                f"上传并安全解压到 {paths['staging_root']}",
                f"保留当前线上目录到 {paths['releases_root']}",
                f"将新版本切换为原路径 {paths['live_path']}",
                "执行 nginx -t 和应用 HTTP 检查",
                "验证失败时恢复原线上目录",
            ],
            "warnings": list(inspection.get("warnings", [])) if inspection else [],
        }
    )
    return payload


def directory_lock_command(paths: dict[str, str], operation_id: str, started_at: str) -> str:
    info = {
        "schema_version": SCHEMA_VERSION,
        "operation_id": operation_id,
        "started_at": started_at,
    }
    return f"""set -eu
lock_root={shlex.quote(paths['lock_root'])}
active="$lock_root/active"
mkdir -p "$lock_root"
if ! mkdir "$active" 2>/dev/null; then
    if [ -r "$active/info.json" ]; then cat "$active/info.json" >&2; fi
    echo '已有部署操作持有目录锁' >&2
    exit 73
fi
printf '%s\n' {shlex.quote(json.dumps(info, ensure_ascii=False, separators=(',', ':')))} > "$active/info.json"
printf 'DEPLOYX_LOCK_ACQUIRED\t%s\n' {shlex.quote(operation_id)}
"""


def acquire_directory_lock(
    alias: str, paths: dict[str, str], operation_id: str, started_at: str, timeout: int
) -> dict[str, Any]:
    return remote_step(alias, directory_lock_command(paths, operation_id, started_at), timeout, "lock")


def release_directory_lock(alias: str, paths: dict[str, str], operation_id: str, timeout: int) -> dict[str, Any]:
    command = f"""set -eu
active={shlex.quote(posixpath.join(paths['lock_root'], 'active'))}
if [ ! -d "$active" ]; then exit 0; fi
if [ -r "$active/info.json" ] && ! grep -Fq {shlex.quote(operation_id)} "$active/info.json"; then
    echo '目录锁不属于当前操作，拒绝释放' >&2
    exit 74
fi
rm -rf -- "$active"
printf 'DEPLOYX_LOCK_RELEASED\t%s\n' {shlex.quote(operation_id)}
"""
    return remote_step(alias, command, timeout, "unlock")


def directory_stage_command(
    paths: dict[str, str],
    artifact_remote: str,
    artifact_sha256: str,
    release_id: str,
    operation_id: str,
) -> str:
    stage_path = posixpath.join(paths["staging_root"], f"release-{release_id}.{operation_id}")
    return f"""set -eu
artifact={shlex.quote(artifact_remote)}
stage_path={shlex.quote(stage_path)}
live_path={shlex.quote(paths['live_path'])}
mkdir -p {shlex.quote(paths['staging_root'])} {shlex.quote(paths['releases_root'])} {shlex.quote(paths['failed_root'])} {shlex.quote(paths['manifests_root'])} {shlex.quote(paths['deployments_root'])}
test -f "$artifact"
actual_sha=$(sha256sum "$artifact" | sed 's/[[:space:]].*$//')
test "$actual_sha" = {shlex.quote(artifact_sha256)}
tar -tzf "$artifact" >/dev/null
rm -rf -- "$stage_path"
mkdir -p "$stage_path"
tar -xzf "$artifact" -C "$stage_path"
test -s "$stage_path/index.html"
owner_group=$(stat -c '%U:%G' "$live_path")
mode=$(stat -c '%a' "$live_path")
chown -R "$owner_group" "$stage_path"
chmod "$mode" "$stage_path"
if command -v restorecon >/dev/null 2>&1; then restorecon -R "$stage_path" >/dev/null 2>&1 || true; fi
printf 'DEPLOYX_DIRECTORY_STAGE\t%s\n' "$stage_path"
"""


def directory_switch_command(
    paths: dict[str, str],
    candidate_path: str,
    previous_id: str,
    expected_baseline: str,
) -> str:
    previous_path = posixpath.join(paths["releases_root"], previous_id)
    return f"""set -eu
live_path={shlex.quote(paths['live_path'])}
candidate_path={shlex.quote(candidate_path)}
previous_path={shlex.quote(previous_path)}
test -d "$live_path"
test -d "$candidate_path"
actual_baseline=$(stat -c '%d:%i:%Y:%s' "$live_path")
if [ "$actual_baseline" != {shlex.quote(expected_baseline)} ]; then
    echo "线上目录基线已经变化：$actual_baseline" >&2
    exit 75
fi
if [ -e "$previous_path" ]; then
    echo '保护性回退目录已经存在，拒绝覆盖' >&2
    exit 76
fi
mv "$live_path" "$previous_path"
if ! mv "$candidate_path" "$live_path"; then
    mv "$previous_path" "$live_path" || true
    exit 77
fi
printf 'DEPLOYX_DIRECTORY_SWITCH\t%s\t%s\n' "$live_path" "$previous_path"
"""


def directory_recovery_command(
    paths: dict[str, str], previous_id: str, operation_id: str
) -> str:
    previous_path = posixpath.join(paths["releases_root"], previous_id)
    failed_path = posixpath.join(paths["failed_root"], operation_id)
    return f"""set -eu
live_path={shlex.quote(paths['live_path'])}
previous_path={shlex.quote(previous_path)}
failed_path={shlex.quote(failed_path)}
test -d "$live_path"
test -d "$previous_path"
if [ -e "$failed_path" ]; then
    echo '失败现场目录已经存在，拒绝覆盖' >&2
    exit 78
fi
mv "$live_path" "$failed_path"
if ! mv "$previous_path" "$live_path"; then
    mv "$failed_path" "$live_path" || true
    exit 79
fi
printf 'DEPLOYX_DIRECTORY_RECOVERY\t%s\t%s\n' "$live_path" "$failed_path"
"""


def directory_runtime_health(args: argparse.Namespace, paths: dict[str, str]) -> dict[str, Any]:
    nginx = remote_step(args.alias, "nginx -t", args.timeout, "nginx-test")
    if nginx.get("status") != "ok":
        return {
            "status": nginx.get("status", "failed"),
            "connection_status": nginx.get("connection_status", "ok"),
            "failed_stage": "nginx-test",
            "error": nginx.get("error") or "nginx -t 未通过",
            "nginx": nginx,
            "application": None,
        }
    application = run_application_health(
        args.alias,
        application_health_specs(args.health_check),
        args.nginx_server_name,
        args.timeout,
    )
    if application.get("status") != "pass":
        return {
            "status": "unavailable" if application.get("status") == "unavailable" else "failed",
            "connection_status": application.get("connection_status", "ok"),
            "failed_stage": "application-health",
            "error": "应用 HTTP 健康检查失败",
            "nginx": nginx,
            "application": application,
        }
    host_specs = host_health_specs(args.health_check)
    host = run_host_health(args.alias, host_specs, args.timeout) if host_specs else {
        "status": "not_requested",
        "connection_status": "not_requested",
    }
    if host_specs and host.get("status") not in {"pass", "warn"}:
        return {
            "status": "unavailable" if host.get("connection_status") == "unavailable" else "failed",
            "connection_status": host.get("connection_status", "ok"),
            "failed_stage": "host-health",
            "error": host.get("error") or "主机健康检查失败",
            "nginx": nginx,
            "application": application,
            "host": host,
        }
    return {
        "status": "healthy",
        "connection_status": "ok",
        "nginx": nginx,
        "application": application,
        "host": host,
        "live_path": paths["live_path"],
    }


def directory_persist_command(
    paths: dict[str, str],
    deployment: dict[str, Any] | None,
    manifests: list[dict[str, Any]],
    last_result: dict[str, Any],
    operation_id: str,
) -> str:
    writes: list[str] = []
    for manifest in manifests:
        release_id = str(manifest["release_id"])
        target = posixpath.join(paths["manifests_root"], f"{release_id}.json")
        temp = target + f".{operation_id}.tmp"
        value = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
        writes.append(
            f"printf '%s\\n' {shlex.quote(value)} > {shlex.quote(temp)}\n"
            f"mv -f {shlex.quote(temp)} {shlex.quote(target)}"
        )
    if deployment is not None:
        value = json.dumps(deployment, ensure_ascii=False, separators=(",", ":"))
        temp = paths["deployment_file"] + f".{operation_id}.tmp"
        writes.append(
            f"printf '%s\\n' {shlex.quote(value)} > {shlex.quote(temp)}\n"
            f"mv -f {shlex.quote(temp)} {shlex.quote(paths['deployment_file'])}"
        )
    last_value = json.dumps(last_result, ensure_ascii=False, separators=(",", ":"))
    last_path = posixpath.join(paths["deployments_root"], "last.json")
    last_temp = last_path + f".{operation_id}.tmp"
    writes.append(
        f"printf '%s\\n' {shlex.quote(last_value)} > {shlex.quote(last_temp)}\n"
        f"mv -f {shlex.quote(last_temp)} {shlex.quote(last_path)}"
    )
    body = "\n".join(writes)
    return f"""set -eu
mkdir -p {shlex.quote(paths['state_root'])} {shlex.quote(paths['manifests_root'])} {shlex.quote(paths['deployments_root'])}
{body}
printf 'DEPLOYX_DIRECTORY_STATE_WRITTEN\ttrue\n'
"""


def persist_directory_state(
    alias: str,
    paths: dict[str, str],
    deployment: dict[str, Any] | None,
    manifests: list[dict[str, Any]],
    last_result: dict[str, Any],
    operation_id: str,
    timeout: int,
) -> dict[str, Any]:
    return remote_step(
        alias,
        directory_persist_command(paths, deployment, manifests, last_result, operation_id),
        timeout,
        "state",
    )


def prune_directory_releases(
    alias: str,
    paths: dict[str, str],
    manifests: list[dict[str, Any]],
    current_id: str,
    previous_id: str,
    keep_releases: int,
    timeout: int,
) -> dict[str, Any]:
    ordered = sorted(
        manifests,
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("release_id") or "")),
        reverse=True,
    )
    keep: list[str] = []
    for release_id in (current_id, previous_id):
        if release_id and release_id not in keep:
            keep.append(release_id)
    for item in ordered:
        release_id = str(item.get("release_id") or "")
        if release_id and release_id not in keep:
            keep.append(release_id)
        if len(keep) >= keep_releases:
            break
    removable = [
        str(item.get("release_id") or "")
        for item in manifests
        if str(item.get("release_id") or "") not in keep
    ]
    commands = []
    for release_id in removable:
        if not RELEASE_ID_RE.fullmatch(release_id):
            continue
        commands.append(
            f"if [ -d {shlex.quote(posixpath.join(paths['releases_root'], release_id))} ]; then "
            f"rm -rf -- {shlex.quote(posixpath.join(paths['releases_root'], release_id))}; "
            f"printf 'DEPLOYX_PRUNE_REMOVED\\t%s\\n' {shlex.quote(release_id)}; fi"
        )
        commands.append(f"rm -f -- {shlex.quote(posixpath.join(paths['manifests_root'], release_id + '.json'))}")
    step = remote_step(alias, "set -eu\n" + "\n".join(commands), timeout, "prune")
    return {**step, "kept": keep, "eligible": removable}


def directory_release_manifest(
    release_id: str,
    artifact: dict[str, Any] | None,
    created_at: str,
    source: str,
    paths: dict[str, str],
    entry_sha256: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "artifact_name": str((artifact or {}).get("name") or ""),
        "artifact_sha256": str((artifact or {}).get("sha256") or ""),
        "created_at": created_at,
        "source": source,
        "strategy": "directory-swap",
        "live_path": paths["live_path"],
        "entry_sha256": entry_sha256,
        "state": "available",
    }


def directory_apply(args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    plan_payload = directory_plan(args)
    paths = plan_payload["paths"]
    payload = directory_base_payload("deployment_apply", args.alias, args.app, paths)
    operation_id = "deploy-" + uuid.uuid4().hex[:12]
    payload.update(
        {
            "operation": "apply",
            "deployment_id": operation_id,
            "started_at": started_at,
            "status": plan_payload.get("status"),
            "connection_status": plan_payload.get("connection_status"),
            "artifact": plan_payload.get("artifact"),
            "candidate_release": plan_payload.get("candidate_release"),
            "current_release": plan_payload.get("current_release"),
            "warnings": list(plan_payload.get("warnings", [])),
            "stages": [],
        }
    )
    if plan_payload.get("status") != "ready":
        payload.update(
            {
                "stage": "failed",
                "failed_stage": "precondition",
                "error": "部署前置条件未满足",
                "finished_at": utc_now(),
                "plan": plan_payload,
            }
        )
        return payload
    artifact = dict(plan_payload["artifact"])
    release_id = str(plan_payload["candidate_release"]["release_id"])
    current = plan_payload.get("current_release")
    if isinstance(current, dict) and current.get("artifact_sha256") == artifact.get("sha256"):
        payload.update(
            {
                "status": "healthy",
                "stage": "unchanged",
                "connection_status": "ok",
                "release": current,
                "stages": [stage_event("unchanged", release_id=current.get("release_id"))],
                "finished_at": utc_now(),
            }
        )
        return payload
    locked = acquire_directory_lock(args.alias, paths, operation_id, started_at, args.timeout)
    payload["lock"] = locked
    if locked.get("status") != "ok":
        return stage_failure(payload, [], "lock", locked)
    try:
        refreshed = inspect_directory_target(
            args.alias,
            args.app,
            args.live_path,
            args.state_root,
            args.nginx_server_name,
            args.timeout,
        )
        inspection = refreshed.get("inspection")
        if inspection is None:
            return stage_failure(payload, [], "inspect", refreshed)
        expected_baseline = str(plan_payload.get("baseline", {}).get("live") or "")
        actual_baseline = str(inspection.get("live_baseline") or "")
        if not expected_baseline or actual_baseline != expected_baseline:
            return stage_failure(
                payload,
                [],
                "precondition",
                {
                    "status": "failed",
                    "connection_status": "ok",
                    "error": "deployment_baseline_changed：线上目录在 plan 后发生变化",
                },
            )
        previous = directory_current_release(inspection)
        previous_id = str(previous.get("release_id")) if previous else legacy_release_id(inspection)
        previous_manifest = next(
            (
                dict(item) for item in inspection.get("manifests", [])
                if str(item.get("release_id") or "") == previous_id
            ),
            directory_release_manifest(
                previous_id,
                None,
                started_at,
                "legacy",
                paths,
                str(inspection.get("entry_sha256") or ""),
            ),
        )
        candidate_manifest = next(
            (
                dict(item) for item in inspection.get("manifests", [])
                if str(item.get("release_id") or "") == release_id
                and str(item.get("artifact_sha256") or "") == str(artifact.get("sha256") or "")
                and release_id in inspection.get("release_paths", [])
            ),
            None,
        )
        stages: list[dict[str, Any]] = []
        reused = candidate_manifest is not None
        if reused:
            candidate_path = posixpath.join(paths["releases_root"], release_id)
            stages.append(stage_event("staged", release_id=release_id, reused=True))
        else:
            artifact_remote = posixpath.join(
                paths["staging_root"], f"upload-{release_id}", "artifact.tar.gz"
            )
            upload = run_ssh_put(
                args.alias,
                str(artifact["path"]),
                artifact_remote,
                args.timeout,
                args.upload_chunk_size,
            )
            payload["upload"] = upload
            if upload.get("status") != "ok":
                return stage_failure(payload, stages, "upload", upload)
            stages.append(stage_event("uploaded", result=upload.get("result")))
            staged = remote_step(
                args.alias,
                directory_stage_command(
                    paths,
                    artifact_remote,
                    str(artifact.get("sha256") or ""),
                    release_id,
                    operation_id,
                ),
                args.timeout,
                "stage",
            )
            payload["staged_release"] = staged
            if staged.get("status") != "ok":
                return stage_failure(payload, stages, "stage", staged)
            marker = "DEPLOYX_DIRECTORY_STAGE\t"
            candidate_path = next(
                (line[len(marker):] for line in str(staged.get("stdout", "")).splitlines() if line.startswith(marker)),
                "",
            )
            if not candidate_path:
                return stage_failure(
                    payload,
                    stages,
                    "stage",
                    {"status": "failed", "connection_status": "ok", "error": "暂存目录结果不可解析"},
                )
            stages.append(stage_event("staged", release_id=release_id, reused=False))
            candidate_manifest = directory_release_manifest(
                release_id,
                artifact,
                started_at,
                "artifact",
                paths,
            )
        switched = remote_step(
            args.alias,
            directory_switch_command(paths, candidate_path, previous_id, expected_baseline),
            args.timeout,
            "switch",
        )
        payload["switch"] = switched
        if switched.get("status") != "ok":
            return stage_failure(payload, stages, "switch", switched)
        stages.append(stage_event("switched", release_id=release_id, previous_release=previous_id))
        runtime = directory_runtime_health(args, paths)
        payload["health"] = runtime
        if runtime.get("status") != "healthy":
            failure = stage_failure(
                payload,
                stages,
                str(runtime.get("failed_stage") or "health"),
                runtime,
            )
            recovery = remote_step(
                args.alias,
                directory_recovery_command(paths, previous_id, operation_id),
                args.timeout,
                "rollback",
            )
            failure["rollback"] = recovery
            if recovery.get("status") == "ok":
                recovery_health = directory_runtime_health(args, paths)
                failure["rollback"]["health"] = recovery_health
                if recovery_health.get("status") == "healthy":
                    failure.update({"status": "rolled_back", "stage": "rolled_back"})
                    failure["stages"].append(stage_event("rolled_back", release_id=previous_id))
            last_result = {
                "schema_version": SCHEMA_VERSION,
                "operation": "apply",
                "operation_id": operation_id,
                "status": failure.get("status"),
                "failed_stage": failure.get("failed_stage"),
                "release_id": release_id,
                "previous_release": previous_id,
                "started_at": started_at,
                "finished_at": utc_now(),
                "rollback": failure.get("rollback"),
            }
            persisted = persist_directory_state(
                args.alias, paths, None, [], last_result, operation_id, args.timeout
            )
            failure["persistence"] = persisted
            return failure
        stages.extend([stage_event("verified"), stage_event("healthy")])
        finished_at = utc_now()
        candidate_manifest = dict(candidate_manifest or {})
        candidate_manifest.update({"state": "active", "verified_at": finished_at})
        previous_manifest.update({"state": "available"})
        deployment = {
            "schema_version": SCHEMA_VERSION,
            "app": args.app,
            "strategy": "directory-swap",
            "service_type": "nginx-static",
            "ssh_alias": args.alias,
            "live_path": paths["live_path"],
            "state_root": paths["state_root"],
            "current_release": release_id,
            "artifact_sha256": artifact.get("sha256"),
            "nginx_server_name": args.nginx_server_name,
            "health_check_specs": list(args.health_check or []),
            "started_at": started_at,
            "finished_at": finished_at,
        }
        last_result = {
            "schema_version": SCHEMA_VERSION,
            "operation": "apply",
            "operation_id": operation_id,
            "status": "healthy",
            "release_id": release_id,
            "previous_release": previous_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "health": runtime,
        }
        persisted = persist_directory_state(
            args.alias,
            paths,
            deployment,
            [previous_manifest, candidate_manifest],
            last_result,
            operation_id,
            args.timeout,
        )
        payload["persistence"] = persisted
        manifests = [
            *[
                dict(item) for item in inspection.get("manifests", [])
                if str(item.get("release_id") or "") not in {previous_id, release_id}
            ],
            previous_manifest,
            candidate_manifest,
        ]
        retention = prune_directory_releases(
            args.alias,
            paths,
            manifests,
            release_id,
            previous_id,
            args.keep_releases,
            args.timeout,
        )
        payload.update(
            {
                "status": "healthy",
                "stage": "healthy",
                "connection_status": "ok",
                "release": candidate_manifest,
                "previous_release": previous_manifest,
                "stages": stages,
                "health": runtime,
                "retention": retention,
                "finished_at": finished_at,
            }
        )
        return payload
    finally:
        payload["unlock"] = release_directory_lock(
            args.alias, paths, operation_id, args.timeout
        )


def directory_status(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live_path:
        raise ValueError("directory-swap 必须提供 --live-path")
    result = inspect_directory_target(
        args.alias,
        args.app,
        args.live_path,
        args.state_root,
        args.nginx_server_name,
        args.timeout,
    )
    paths = result["paths"]
    payload = directory_base_payload("deployment_status", args.alias, args.app, paths)
    inspection = result.get("inspection")
    if inspection is None:
        payload.update(
            {
                "status": result.get("status"),
                "connection_status": result.get("connection_status"),
                "error": result.get("error"),
                "management_status": "unknown",
                "current_release": None,
                "last_result": None,
                "warnings": [],
            }
        )
        return payload
    current = directory_current_release(inspection)
    management_status = "managed" if current else "legacy_unmanaged"
    warnings = list(inspection.get("warnings", []))
    if inspection.get("active_lock"):
        warnings.append("存在进行中的目录部署锁")
    if args.nginx_server_name and not inspection.get("nginx_root_found"):
        warnings.append("Nginx root/alias 与 live_path 不一致")
    status_value = "partial" if warnings or management_status == "legacy_unmanaged" else "ok"
    payload.update(
        {
            "status": status_value,
            "connection_status": "ok",
            "management_status": management_status,
            "current_release": current or {
                "release_id": legacy_release_id(inspection),
                "path": paths["live_path"],
                "state": "legacy_unmanaged",
                "entry_sha256": inspection.get("entry_sha256"),
            },
            "baseline": inspection.get("live_baseline"),
            "nginx": {
                "server_name": args.nginx_server_name,
                "test": inspection.get("nginx_test"),
                "root_matched": inspection.get("nginx_root_found"),
            },
            "active_lock": inspection.get("active_lock") or None,
            "last_result": inspection.get("last_result") or None,
            "warnings": warnings,
        }
    )
    return payload


def directory_history(args: argparse.Namespace) -> dict[str, Any]:
    result = inspect_directory_target(
        args.alias,
        args.app,
        args.live_path,
        args.state_root,
        args.nginx_server_name,
        args.timeout,
    )
    paths = result["paths"]
    payload = directory_base_payload("deployment_history", args.alias, args.app, paths)
    inspection = result.get("inspection")
    if inspection is None:
        payload.update(
            {
                "status": result.get("status"),
                "connection_status": result.get("connection_status"),
                "error": result.get("error"),
                "current_release": None,
                "releases": [],
                "warnings": [],
            }
        )
        return payload
    current = directory_current_release(inspection)
    releases = sorted(
        [dict(item) for item in inspection.get("manifests", [])],
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("release_id") or "")),
        reverse=True,
    )
    payload.update(
        {
            "status": "partial" if inspection.get("warnings") else "ok",
            "connection_status": "ok",
            "management_status": "managed" if current else "legacy_unmanaged",
            "current_release": current,
            "releases": releases[: args.limit],
            "last_result": inspection.get("last_result") or None,
            "warnings": list(inspection.get("warnings", [])),
        }
    )
    return payload


def directory_rollback(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live_path:
        raise ValueError("directory-swap 必须提供 --live-path")
    started_at = utc_now()
    inspected = inspect_directory_target(
        args.alias,
        args.app,
        args.live_path,
        args.state_root,
        args.nginx_server_name,
        args.timeout,
    )
    paths = inspected["paths"]
    payload = directory_base_payload("deployment_rollback", args.alias, args.app, paths)
    operation_id = "rollback-" + uuid.uuid4().hex[:12]
    payload.update({"operation": "rollback", "deployment_id": operation_id, "started_at": started_at})
    inspection = inspected.get("inspection")
    if inspection is None:
        return stage_failure(payload, [], "inspect", inspected)
    current = directory_current_release(inspection)
    if not current:
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "历史目录尚未被 deployx 管理"},
        )
    current_id = str(current["release_id"])
    available = [
        dict(item) for item in inspection.get("manifests", [])
        if str(item.get("release_id") or "") != current_id
        and str(item.get("release_id") or "") in inspection.get("release_paths", [])
    ]
    available.sort(
        key=lambda item: (str(item.get("created_at") or ""), str(item.get("release_id") or "")),
        reverse=True,
    )
    if args.release:
        if not RELEASE_ID_RE.fullmatch(args.release):
            raise ValueError("--release 只能是单级 release ID")
        target = next((item for item in available if item.get("release_id") == args.release), None)
    else:
        target = available[0] if available else None
    if not target:
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "没有可用的历史 release"},
        )
    spec = inspection.get("deployment_spec") if isinstance(inspection.get("deployment_spec"), dict) else {}
    if not args.nginx_server_name:
        args.nginx_server_name = str(spec.get("nginx_server_name") or "")
    if not args.health_check:
        args.health_check = list(spec.get("health_check_specs") or [])
    if not application_health_specs(args.health_check):
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "回滚缺少 HTTP 健康检查"},
        )
    locked = acquire_directory_lock(args.alias, paths, operation_id, started_at, args.timeout)
    payload["lock"] = locked
    if locked.get("status") != "ok":
        return stage_failure(payload, [], "lock", locked)
    try:
        baseline = str(inspection.get("live_baseline") or "")
        switched = remote_step(
            args.alias,
            directory_switch_command(
                paths,
                posixpath.join(paths["releases_root"], str(target["release_id"])),
                current_id,
                baseline,
            ),
            args.timeout,
            "switch",
        )
        if switched.get("status") != "ok":
            return stage_failure(payload, [], "switch", switched)
        runtime = directory_runtime_health(args, paths)
        if runtime.get("status") != "healthy":
            failure = stage_failure(
                payload,
                [stage_event("switched", release_id=target["release_id"])],
                str(runtime.get("failed_stage") or "health"),
                runtime,
            )
            recovery = remote_step(
                args.alias,
                directory_recovery_command(paths, current_id, operation_id),
                args.timeout,
                "rollback-recovery",
            )
            failure["rollback"] = recovery
            return failure
        finished_at = utc_now()
        target = {**target, "state": "active", "verified_at": finished_at}
        current_manifest = next(
            (
                dict(item) for item in inspection.get("manifests", [])
                if str(item.get("release_id") or "") == current_id
            ),
            dict(current),
        )
        current_manifest["state"] = "available"
        deployment = {
            **spec,
            "schema_version": SCHEMA_VERSION,
            "current_release": target["release_id"],
            "artifact_sha256": target.get("artifact_sha256"),
            "finished_at": finished_at,
        }
        last_result = {
            "schema_version": SCHEMA_VERSION,
            "operation": "rollback",
            "operation_id": operation_id,
            "status": "healthy",
            "release_id": target["release_id"],
            "previous_release": current_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "health": runtime,
        }
        persisted = persist_directory_state(
            args.alias,
            paths,
            deployment,
            [target, current_manifest],
            last_result,
            operation_id,
            args.timeout,
        )
        payload.update(
            {
                "status": "healthy",
                "stage": "healthy",
                "connection_status": "ok",
                "release": target,
                "previous_release": current_manifest,
                "stages": [
                    stage_event("staged", release_id=target["release_id"], reused=True),
                    stage_event("switched", release_id=target["release_id"]),
                    stage_event("verified"),
                    stage_event("healthy"),
                ],
                "health": runtime,
                "persistence": persisted,
                "finished_at": finished_at,
            }
        )
        return payload
    finally:
        payload["unlock"] = release_directory_lock(
            args.alias, paths, operation_id, args.timeout
        )


def plan(args: argparse.Namespace) -> dict[str, Any]:
    if args.strategy == "directory-swap":
        return directory_plan(args)
    if not args.release_root:
        raise ValueError("versioned-link 必须提供 --release-root")
    if args.service_type != "systemd":
        raise ValueError("versioned-link 首版只支持 --service-type systemd")
    if not args.service:
        raise ValueError("systemd 部署必须提供 --service")
    artifact = artifact_info(args.artifact, args.artifact_sha256)
    paths = layout(args.app, args.release_root)
    candidate_release = release_id_for(artifact) if artifact.get("sha256") else None
    inspection_result = inspect_target(args.alias, args.app, args.release_root, args.timeout)
    inspection = inspection_result.get("inspection")
    if inspection_result["connection_status"] == "unavailable":
        service_status = {
            "status": "unavailable",
            "connection_status": "unavailable",
            "service": {"name": args.service},
            "error": "无法在目标主机查询服务状态",
        }
    else:
        service_status = run_host_service(args.alias, args.service, args.timeout)

    preconditions: list[dict[str, str]] = []
    if artifact["status"] == "ok":
        preconditions.append(check_status("artifact", "pass", "本地 tar.gz 制品可读且校验通过"))
    else:
        preconditions.append(check_status("artifact", "fail", str(artifact.get("error") or "制品不可用")))

    if inspection is None:
        preconditions.append(check_status("remote_target", "unknown", "无法读取目标发布目录"))
        preconditions.append(check_status("disk_space", "unknown", "无法读取目标主机可用空间"))
    else:
        root_ready = inspection["release_root_is_dir"] or inspection["release_root_parent_exists"]
        if root_ready:
            detail = "发布根目录已存在" if inspection["release_root_exists"] else "发布根目录尚不存在，将由 apply 创建"
            preconditions.append(check_status("remote_target", "pass", detail))
        else:
            preconditions.append(check_status("remote_target", "fail", "发布根目录及其父目录均不可用"))
        available = inspection.get("available_bytes")
        artifact_bytes = artifact.get("bytes")
        if available is None:
            preconditions.append(check_status("disk_space", "unknown", "目标主机未返回可用空间"))
        elif artifact_bytes is not None and available < artifact_bytes:
            preconditions.append(
                check_status("disk_space", "fail", f"可用空间 {available} 小于制品大小 {artifact_bytes}")
            )
        else:
            preconditions.append(check_status("disk_space", "pass", f"available_bytes={available}"))

    service_connection = service_status.get("connection_status")
    service = service_status.get("service") if isinstance(service_status.get("service"), dict) else {}
    if service_connection == "unavailable" or service_status.get("status") == "unavailable":
        preconditions.append(check_status("service", "unknown", str(service_status.get("error") or "hostx 不可用")))
    elif service.get("error") == "服务单元不存在" or service.get("load_state") == "not-found":
        preconditions.append(check_status("service", "fail", "服务单元未安装"))
    elif service_status.get("status") in {"failed", "unknown"} and not service:
        preconditions.append(check_status("service", "unknown", str(service_status.get("error") or "服务状态未知")))
    elif service.get("active") not in {"", "active", None}:
        preconditions.append(check_status("service", "warn", f"当前 active={service.get('active')}，apply 将执行重启"))
    else:
        preconditions.append(check_status("service", "pass", f"service={args.service}"))

    candidate = {
        "release_id": candidate_release,
        "path": posixpath.join(paths["releases_root"], candidate_release) if candidate_release else None,
        "artifact_name": artifact.get("name", ""),
        "artifact_sha256": artifact.get("sha256"),
        "state": "candidate" if artifact["status"] == "ok" else "invalid",
    }
    current_id = current_release_id(inspection.get("current_target", "")) if inspection else None
    current = None
    if inspection:
        current = next((item for item in inspection["releases"] if item.get("release_id") == current_id), None)
    warnings = list(inspection.get("warnings", [])) if inspection else []
    if inspection and not inspection["release_root_exists"]:
        warnings.append("目标发布根目录尚不存在，apply 需要先创建目录结构")
    if service.get("active") not in {"", "active", None}:
        warnings.append("目标服务当前未 active；本轮 plan 只读，不会改变服务状态")
    statuses = {item["status"] for item in preconditions}
    if inspection_result["connection_status"] == "unavailable":
        result_status = "unavailable"
    elif "fail" in statuses or "unknown" in statuses:
        result_status = "blocked"
    else:
        result_status = "ready"
    payload = base_payload("deployment_plan", args.alias, args.app, args.release_root)
    payload.update(
        {
            "status": result_status,
            "connection_status": inspection_result["connection_status"],
            "deployment_spec": {
                "app": args.app,
                "artifact": artifact.get("path"),
                "artifact_sha256": artifact.get("sha256"),
                "release_root": args.release_root,
                "current_link": paths["current_link"],
                "service": args.service,
                "health_checks": health_specs(args.health_check),
                "keep_releases": args.keep_releases,
            },
            "artifact": artifact,
            "current_release": current,
            "candidate_release": candidate,
            "actions": [
                "上传制品到远端暂存位置",
                "解包并写入版本目录及 manifest.json",
                "原子切换 current 链接",
                f"重启服务 {args.service}",
                "调用 hostx 健康检查并记录部署结果",
            ],
            "preconditions": preconditions,
            "service_status": service_status,
            "health_checks": health_specs(args.health_check),
            "warnings": warnings,
        }
    )
    return payload


def inferred_service(inspection: dict[str, Any]) -> str:
    spec = inspection.get("deployment_spec")
    if isinstance(spec, dict) and spec.get("service"):
        return str(spec["service"])
    last = inspection.get("last_result")
    if isinstance(last, dict) and last.get("service"):
        return str(last["service"])
    for release in sort_releases(inspection.get("releases", [])):
        if release.get("service"):
            return str(release["service"])
    return ""


def inferred_health_checks(inspection: dict[str, Any]) -> list[str]:
    spec = inspection.get("deployment_spec")
    if isinstance(spec, dict):
        raw = spec.get("health_checks")
        if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
            return health_specs(raw)
    return health_specs(None)


def stage_event(name: str, status: str = "ok", **details: Any) -> dict[str, Any]:
    event: dict[str, Any] = {"name": name, "status": status}
    event.update({key: value for key, value in details.items() if value is not None})
    return event


def stage_failure(
    payload: dict[str, Any],
    stages: list[dict[str, Any]],
    failed_stage: str,
    failure: dict[str, Any],
) -> dict[str, Any]:
    stages.append(
        stage_event(
            "failed",
            "failed",
            failed_stage=failed_stage,
            error=str(failure.get("error") or "部署阶段失败"),
        )
    )
    payload.update(
        {
            "status": "unavailable" if failure.get("status") == "unavailable" else "failed",
            "stage": "failed",
            "failed_stage": failed_stage,
            "error": str(failure.get("error") or "部署阶段失败"),
            "connection_status": str(failure.get("connection_status") or "ok"),
            "stages": stages,
            "finished_at": utc_now(),
        }
    )
    return payload


def stage_command(
    paths: dict[str, str],
    deployment_id: str,
    release_id: str,
    artifact_name: str,
    artifact_sha256: str,
    artifact_remote: str,
    previous_release: str | None,
    service: str,
    health_checks: list[str],
    keep_releases: int,
    started_at: str,
    operation: str,
    app: str,
) -> str:
    release_path = posixpath.join(paths["releases_root"], release_id)
    stage_parent = posixpath.join(paths["app_root"], ".deployx-staging")
    staging_dir = posixpath.dirname(artifact_remote)
    candidate_tmp = posixpath.join(paths["releases_root"], f".{release_id}.{deployment_id}.tmp")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "release_id": release_id,
        "path": release_path,
        "created_at": started_at,
        "artifact_name": artifact_name,
        "artifact_sha256": artifact_sha256,
        "state": "available",
        "previous_release": previous_release,
        "service": service,
    }
    deployment_spec = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "deployment_id": deployment_id,
        "app": app,
        "artifact_name": artifact_name,
        "artifact_sha256": artifact_sha256,
        "release_id": release_id,
        "release_root": posixpath.dirname(paths["app_root"]),
        "current_link": paths["current_link"],
        "service": service,
        "health_checks": health_checks,
        "keep_releases": keep_releases,
        "started_at": started_at,
    }
    staged = {
        "release_id": release_id,
        "release_path": release_path,
        "staged": True,
        "reused": False,
    }
    reused = {**staged, "reused": True}
    return f"""set -eu
app_root={shlex.quote(paths['app_root'])}
releases_root={shlex.quote(paths['releases_root'])}
deployments_root={shlex.quote(paths['deployments_root'])}
release_path={shlex.quote(release_path)}
artifact={shlex.quote(artifact_remote)}
stage_parent={shlex.quote(stage_parent)}
staging_dir={shlex.quote(staging_dir)}
candidate_tmp={shlex.quote(candidate_tmp)}
cleanup_stage() {{
    rm -rf -- "$candidate_tmp"
}}
trap cleanup_stage EXIT HUP INT TERM
mkdir -p "$app_root" "$releases_root" "$deployments_root" "$stage_parent"
if [ ! -f "$artifact" ]; then
    echo '暂存制品不存在' >&2
    exit 42
fi
if ! command -v sha256sum >/dev/null 2>&1; then
    echo '目标主机缺少 sha256sum，无法校验制品' >&2
    exit 127
fi
actual_sha256=$(sha256sum "$artifact" | sed 's/[[:space:]].*$//')
if [ "$actual_sha256" != {shlex.quote(artifact_sha256)} ]; then
    echo "远端制品 SHA-256 不匹配：$actual_sha256" >&2
    exit 43
fi
if ! tar -tzf "$artifact" >/dev/null 2>&1; then
    echo '远端制品不是可读取的 tar.gz' >&2
    exit 44
fi
if [ -e "$release_path" ]; then
    if [ -d "$release_path" ] && [ -r "$release_path/manifest.json" ] && grep -Fq -- {shlex.quote(f'"artifact_sha256":"{artifact_sha256}"')} "$release_path/manifest.json"; then
        rm -rf -- "$staging_dir"
        trap - EXIT HUP INT TERM
        printf 'DEPLOYX_STAGE_JSON\t%s\n' {shlex.quote(json.dumps(reused, ensure_ascii=False, separators=(',', ':')))}
        exit 0
    fi
    echo '同一 release_id 已存在但 manifest 校验不一致' >&2
    exit 45
fi
rm -rf -- "$candidate_tmp"
mkdir -p "$candidate_tmp"
tar -xzf "$artifact" -C "$candidate_tmp"
manifest_json={shlex.quote(json.dumps(manifest, ensure_ascii=False, separators=(',', ':')))}
printf '%s\n' "$manifest_json" > "$candidate_tmp/manifest.json"
mv "$candidate_tmp" "$release_path"
rm -rf -- "$staging_dir"
trap - EXIT HUP INT TERM
printf 'DEPLOYX_STAGE_JSON\t%s\n' {shlex.quote(json.dumps(staged, ensure_ascii=False, separators=(',', ':')))}
"""


def switch_command(paths: dict[str, str], release_id: str, operation_id: str) -> str:
    target = f"releases/{release_id}"
    target_path = posixpath.join(paths["releases_root"], release_id)
    temp_link = posixpath.join(paths["app_root"], f".current.{operation_id}")
    return f"""set -eu
current_link={shlex.quote(paths['current_link'])}
target_path={shlex.quote(target_path)}
target={shlex.quote(target)}
temp_link={shlex.quote(temp_link)}
if [ ! -d "$target_path" ] || [ ! -r "$target_path/manifest.json" ]; then
    echo '目标 release 不存在或缺少 manifest.json' >&2
    exit 46
fi
if [ -e "$current_link" ] && [ ! -L "$current_link" ]; then
    echo 'current 不是符号链接，拒绝覆盖' >&2
    exit 47
fi
rm -f "$temp_link"
ln -s "$target" "$temp_link"
mv -Tf "$temp_link" "$current_link"
actual=$(readlink "$current_link" 2>/dev/null || true)
if [ "$actual" != "$target" ]; then
    echo "current 原子切换校验失败：$actual" >&2
    exit 48
fi
printf 'DEPLOYX_SWITCH_TARGET\t%s\n' "$actual"
"""


def restart_command(service: str) -> str:
    quoted = shlex.quote(service)
    return f"""set -eu
if ! command -v systemctl >/dev/null 2>&1; then
    echo '目标主机缺少 systemctl，无法重启服务' >&2
    exit 127
fi
systemctl restart {quoted}
active=$(systemctl is-active {quoted} 2>/dev/null || true)
if [ "$active" != "active" ]; then
    echo "服务重启后不是 active：$active" >&2
    exit 49
fi
printf 'DEPLOYX_RESTART_STATUS\t%s\n' "$active"
"""


def prune_command(releases_root: str, keep_ids: list[str]) -> str:
    keep_blob = "|" + "|".join(keep_ids) + "|"
    return f"""set -eu
releases_root={shlex.quote(releases_root)}
keep_blob={shlex.quote(keep_blob)}
if [ ! -d "$releases_root" ]; then exit 0; fi
for path in "$releases_root"/*; do
    [ -d "$path" ] || continue
    release_id=$(basename "$path")
    case "$keep_blob" in
        *"|$release_id|"*) ;;
        *) rm -rf -- "$path"; printf 'DEPLOYX_PRUNE_REMOVED\t%s\n' "$release_id" ;;
    esac
done
"""


def write_state_command(
    paths: dict[str, str],
    deployment_spec: dict[str, Any],
    last_result: dict[str, Any],
    operation_id: str,
    *,
    include_deployment: bool = True,
) -> str:
    deployment_json = json.dumps(deployment_spec, ensure_ascii=False, separators=(",", ":"))
    result_json = json.dumps(last_result, ensure_ascii=False, separators=(",", ":"))
    deployment_tmp = posixpath.join(paths["app_root"], f".deployment.json.{operation_id}.tmp")
    result_tmp = posixpath.join(paths["deployments_root"], f".last.json.{operation_id}.tmp")
    deployment_write = ""
    if include_deployment:
        deployment_write = (
            f"printf '%s\\n' {shlex.quote(deployment_json)} > \"$deployment_tmp\"\n"
            "mv -f \"$deployment_tmp\" \"$app_root/deployment.json\"\n"
        )
    return f"""set -eu
app_root={shlex.quote(paths['app_root'])}
deployments_root={shlex.quote(paths['deployments_root'])}
deployment_tmp={shlex.quote(deployment_tmp)}
result_tmp={shlex.quote(result_tmp)}
mkdir -p "$app_root" "$deployments_root"
{deployment_write}printf '%s\n' {shlex.quote(result_json)} > "$result_tmp"
mv -f "$result_tmp" "$deployments_root/last.json"
printf 'DEPLOYX_STATE_WRITTEN\ttrue\n'
"""


def switch_release(alias: str, paths: dict[str, str], release_id: str, operation_id: str, timeout: int) -> dict[str, Any]:
    step = remote_step(alias, switch_command(paths, release_id, operation_id), timeout, "switch")
    if step.get("status") != "ok":
        return step
    target = f"releases/{release_id}"
    actual = ""
    for line in str(step.get("stdout", "")).splitlines():
        if line.startswith("DEPLOYX_SWITCH_TARGET\t"):
            actual = line.split("\t", 1)[1]
            break
    if actual != target:
        return {
            **step,
            "status": "failed",
            "error": f"current 切换结果不可验证：{actual or '无返回值'}",
        }
    return {**step, "release_id": release_id, "current_target": actual}


def stage_release(
    alias: str,
    paths: dict[str, str],
    deployment_id: str,
    release_id: str,
    artifact_name: str,
    artifact_sha256: str,
    artifact_remote: str,
    previous_release: str | None,
    service: str,
    health_checks: list[str],
    keep_releases: int,
    started_at: str,
    operation: str,
    app: str,
    timeout: int,
) -> dict[str, Any]:
    step = remote_step(
        alias,
        stage_command(
            paths,
            deployment_id,
            release_id,
            artifact_name,
            artifact_sha256,
            artifact_remote,
            previous_release,
            service,
            health_checks,
            keep_releases,
            started_at,
            operation,
            app,
        ),
        timeout,
        "stage",
    )
    if step.get("status") != "ok":
        return step
    result = marker_json(str(step.get("stdout", "")), "DEPLOYX_STAGE_JSON")
    if result is None:
        return {**step, "status": "failed", "error": "远端暂存结果不可解析"}
    return {**step, **result}


def restart_and_health(alias: str, service: str, health_checks: list[str], timeout: int) -> dict[str, Any]:
    restart = remote_step(alias, restart_command(service), timeout, "restart")
    if restart.get("status") != "ok":
        return {
            "status": restart.get("status", "failed"),
            "connection_status": restart.get("connection_status", "ok"),
            "failed_stage": "restart",
            "error": restart.get("error") or "服务重启失败",
            "restart": restart,
            "service_status": None,
            "health": None,
        }
    service_status = run_host_service(alias, service, timeout)
    if service_status.get("connection_status") == "unavailable":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "failed_stage": "health",
            "error": service_status.get("error") or "重启后无法读取服务状态",
            "restart": restart,
            "service_status": service_status,
            "health": None,
        }
    service_info = service_status.get("service") if isinstance(service_status.get("service"), dict) else {}
    if service_info.get("active") != "active":
        return {
            "status": "failed",
            "connection_status": "ok",
            "failed_stage": "health",
            "error": f"服务重启后 active={service_info.get('active') or 'unknown'}",
            "restart": restart,
            "service_status": service_status,
            "health": None,
        }
    health = run_host_health(alias, health_checks, timeout)
    health_status = str(health.get("status") or "unknown")
    if health.get("connection_status") == "unavailable":
        return {
            "status": "unavailable",
            "connection_status": "unavailable",
            "failed_stage": "health",
            "error": health.get("error") or "健康检查不可用",
            "restart": restart,
            "service_status": service_status,
            "health": health,
        }
    if health_status not in {"pass", "warn"}:
        return {
            "status": "failed",
            "connection_status": "ok",
            "failed_stage": "health",
            "error": health.get("error") or f"健康检查状态为 {health_status}",
            "restart": restart,
            "service_status": service_status,
            "health": health,
        }
    return {
        "status": "healthy",
        "connection_status": "ok",
        "restart": restart,
        "service_status": service_status,
        "health": health,
    }


def verified_release(inspection: dict[str, Any] | None, release_id: str | None) -> dict[str, Any] | None:
    if not inspection or not release_id:
        return None
    for release in inspection.get("releases", []):
        if release.get("release_id") == release_id and release.get("state") != "invalid":
            return release
    return None


def attempt_recovery(
    alias: str,
    paths: dict[str, str],
    release_id: str,
    service: str,
    health_checks: list[str],
    operation_id: str,
    timeout: int,
    reason: str,
) -> dict[str, Any]:
    switched = switch_release(alias, paths, release_id, operation_id + "-rollback", timeout)
    if switched.get("status") != "ok":
        return {
            "attempted": True,
            "status": switched.get("status", "failed"),
            "release_id": release_id,
            "reason": reason,
            "error": switched.get("error") or "自动回退切换失败",
            "switch": switched,
        }
    runtime = restart_and_health(alias, service, health_checks, timeout)
    return {
        "attempted": True,
        "status": "ok" if runtime.get("status") == "healthy" else runtime.get("status", "failed"),
        "release_id": release_id,
        "reason": reason,
        "switch": switched,
        "restart": runtime.get("restart"),
        "service_status": runtime.get("service_status"),
        "health": runtime.get("health"),
        "error": runtime.get("error"),
    }


def keep_release_ids(
    inspection: dict[str, Any],
    preferred_ids: list[str],
    keep_releases: int,
) -> list[str]:
    ordered: list[str] = []
    for release_id in preferred_ids:
        if release_id and release_id not in ordered:
            ordered.append(release_id)
    for release in sort_releases(inspection.get("releases", [])):
        release_id = str(release.get("release_id") or "")
        if release_id and release.get("state") != "invalid" and release_id not in ordered:
            ordered.append(release_id)
    selected = ordered[:keep_releases]
    for release_id in preferred_ids:
        if release_id and release_id not in selected:
            selected.append(release_id)
    return selected


def prune_releases(
    alias: str,
    releases_root: str,
    keep_ids: list[str],
    timeout: int,
) -> dict[str, Any]:
    step = remote_step(alias, prune_command(releases_root, keep_ids), timeout, "prune")
    if step.get("status") != "ok":
        return step
    removed = []
    for line in str(step.get("stdout", "")).splitlines():
        if line.startswith("DEPLOYX_PRUNE_REMOVED\t"):
            removed.append(line.split("\t", 1)[1])
    return {**step, "removed": removed, "kept": keep_ids}


def persist_state(
    alias: str,
    paths: dict[str, str],
    deployment_spec: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    timeout: int,
    *,
    include_deployment: bool = True,
) -> dict[str, Any]:
    release = payload.get("release")
    previous = payload.get("previous_release")
    last_result = {
        "schema_version": SCHEMA_VERSION,
        "kind": payload.get("kind"),
        "operation": payload.get("operation"),
        "deployment_id": payload.get("deployment_id"),
        "status": payload.get("status"),
        "stage": payload.get("stage"),
        "failed_stage": payload.get("failed_stage"),
        "release_id": release.get("release_id") if isinstance(release, dict) else None,
        "previous_release": previous.get("release_id") if isinstance(previous, dict) else previous,
        "service": payload.get("service"),
        "health_status": payload.get("health", {}).get("status") if isinstance(payload.get("health"), dict) else None,
        "rollback": payload.get("rollback"),
        "upload": payload.get("upload"),
        "resume": payload.get("resume"),
        "cleanup": payload.get("cleanup"),
        "started_at": payload.get("started_at"),
        "finished_at": payload.get("finished_at"),
    }
    return remote_step(
        alias,
        write_state_command(
            paths,
            deployment_spec,
            last_result,
            operation_id,
            include_deployment=include_deployment,
        ),
        timeout,
        "state",
    )


def finalize_payload(
    alias: str,
    paths: dict[str, str],
    deployment_spec: dict[str, Any],
    payload: dict[str, Any],
    operation_id: str,
    timeout: int,
    *,
    include_deployment: bool = True,
) -> dict[str, Any]:
    if include_deployment and payload.get("status") != "healthy":
        include_deployment = False
    persisted = persist_state(
        alias,
        paths,
        deployment_spec,
        payload,
        operation_id,
        timeout,
        include_deployment=include_deployment,
    )
    payload["persistence"] = {
        "status": persisted.get("status"),
        "connection_status": persisted.get("connection_status"),
        "error": persisted.get("error"),
    }
    if persisted.get("status") != "ok":
        warnings = list(payload.get("warnings", []))
        warnings.append(str(persisted.get("error") or "无法写入远端部署结果"))
        payload["warnings"] = warnings
    return payload


def apply(args: argparse.Namespace) -> dict[str, Any]:
    if args.strategy == "directory-swap":
        return directory_apply(args)
    started_at = utc_now()
    plan_payload = plan(args)
    paths = layout(args.app, args.release_root)
    payload = base_payload("deployment_apply", args.alias, args.app, args.release_root)
    payload.update(
        {
            "operation": "apply",
            "deployment_id": "deploy-" + uuid.uuid4().hex[:12],
            "started_at": started_at,
            "status": plan_payload.get("status"),
            "connection_status": plan_payload.get("connection_status"),
            "artifact": plan_payload.get("artifact"),
            "candidate_release": plan_payload.get("candidate_release"),
            "current_release": plan_payload.get("current_release"),
            "service": args.service,
            "health_checks": plan_payload.get("health_checks", health_specs(args.health_check)),
            "warnings": list(plan_payload.get("warnings", [])),
            "stages": [],
        }
    )
    if plan_payload.get("status") != "ready":
        payload.update(
            {
                "stage": "failed",
                "failed_stage": "precondition",
                "error": "部署前置条件未满足",
                "finished_at": utc_now(),
                "plan": plan_payload,
            }
        )
        return payload

    inspection_result = inspect_target(args.alias, args.app, args.release_root, args.timeout)
    inspection = inspection_result.get("inspection")
    if inspection is None:
        return stage_failure(
            payload,
            [],
            "inspect",
            {
                "status": inspection_result.get("status"),
                "connection_status": inspection_result.get("connection_status"),
                "error": inspection_result.get("error") or "无法读取目标发布目录",
            },
        )
    artifact = plan_payload["artifact"]
    candidate = plan_payload["candidate_release"]
    release_id = str(candidate["release_id"])
    previous_id = current_release_id(str(inspection.get("current_target", "")))
    previous = verified_release(inspection, previous_id)
    deployment_id = str(payload["deployment_id"])
    health_checks = health_specs(args.health_check)
    artifact_remote = posixpath.join(
        paths["app_root"], ".deployx-staging", f"upload-{release_id}", "artifact.tar.gz"
    )
    deployment_spec = dict(plan_payload["deployment_spec"])
    deployment_spec.update(
        {
            "operation": "apply",
            "deployment_id": deployment_id,
            "release_id": release_id,
            "started_at": started_at,
        }
    )
    payload["previous_release"] = previous
    payload["release"] = {
        "release_id": release_id,
        "path": posixpath.join(paths["releases_root"], release_id),
        "artifact_name": artifact.get("name", ""),
        "artifact_sha256": artifact.get("sha256"),
    }
    stages: list[dict[str, Any]] = []
    upload = run_ssh_put(
        args.alias,
        str(artifact["path"]),
        artifact_remote,
        args.timeout,
        args.upload_chunk_size,
    )
    payload["upload"] = upload
    if upload.get("status") != "ok":
        payload["resume"] = {
            "available": True,
            "remote": artifact_remote,
            "release_id": release_id,
            "reason": "upload_failed",
        }
        payload["cleanup"] = {
            "status": "preserved",
            "remote": artifact_remote,
            "reason": "保留部分上传文件以支持下次断点续传",
        }
        failure_payload = stage_failure(payload, stages, "upload", upload)
        return finalize_payload(
            args.alias,
            paths,
            deployment_spec,
            failure_payload,
            deployment_id,
            args.timeout,
            include_deployment=False,
        )
    stages.append(stage_event("uploaded", result=upload.get("result")))

    staged = stage_release(
        args.alias,
        paths,
        deployment_id,
        release_id,
        str(artifact.get("name") or ""),
        str(artifact.get("sha256") or ""),
        artifact_remote,
        previous_id,
        args.service,
        health_checks,
        args.keep_releases,
        started_at,
        "apply",
        args.app,
        args.timeout,
    )
    payload["staged_release"] = staged
    if staged.get("status") != "ok":
        payload["resume"] = {
            "available": True,
            "remote": artifact_remote,
            "release_id": release_id,
            "reason": "stage_failed",
        }
        payload["cleanup"] = {
            "status": "preserved",
            "remote": artifact_remote,
            "reason": "暂存失败，保留制品以支持下次续传或重试",
        }
        failure_payload = stage_failure(payload, stages, "stage", staged)
        return finalize_payload(
            args.alias,
            paths,
            deployment_spec,
            failure_payload,
            deployment_id,
            args.timeout,
            include_deployment=False,
        )
    stages.append(stage_event("staged", release_id=release_id, reused=bool(staged.get("reused"))))

    switched = switch_release(args.alias, paths, release_id, deployment_id, args.timeout)
    payload["switch"] = switched
    if switched.get("status") != "ok":
        failure_payload = stage_failure(payload, stages, "switch", switched)
        if previous:
            recovery = attempt_recovery(
                args.alias,
                paths,
                previous_id or "",
                args.service,
                health_checks,
                deployment_id,
                args.timeout,
                "current 切换失败后的保护性回退",
            )
            failure_payload["rollback"] = recovery
            if recovery.get("status") == "ok":
                failure_payload["stages"].append(stage_event("rolled_back", release_id=previous_id))
                failure_payload.update({"status": "rolled_back", "stage": "rolled_back"})
        else:
            failure_payload["rollback"] = {"attempted": False, "status": "not_available"}
        return finalize_payload(args.alias, paths, deployment_spec, failure_payload, deployment_id, args.timeout)
    stages.append(stage_event("switched", release_id=release_id, current_target=switched.get("current_target")))

    runtime = restart_and_health(args.alias, args.service, health_checks, args.timeout)
    payload["service_status"] = runtime.get("service_status")
    payload["health"] = runtime.get("health")
    if runtime.get("status") != "healthy":
        failure_payload = stage_failure(payload, stages, str(runtime.get("failed_stage") or "health"), runtime)
        if previous:
            recovery = attempt_recovery(
                args.alias,
                paths,
                previous_id or "",
                args.service,
                health_checks,
                deployment_id,
                args.timeout,
                "服务重启或健康检查失败后的自动回退",
            )
            failure_payload["rollback"] = recovery
            if recovery.get("status") == "ok":
                failure_payload["stages"].append(stage_event("rolled_back", release_id=previous_id))
                failure_payload.update({"status": "rolled_back", "stage": "rolled_back"})
        else:
            failure_payload["rollback"] = {"attempted": False, "status": "not_available"}
        return finalize_payload(args.alias, paths, deployment_spec, failure_payload, deployment_id, args.timeout)
    stages.extend([stage_event("restarted"), stage_event("healthy")])
    keep_ids = keep_release_ids(inspection, [release_id, previous_id or ""], args.keep_releases)
    retention = prune_releases(args.alias, paths["releases_root"], keep_ids, args.timeout)
    payload.update(
        {
            "status": "healthy",
            "stage": "healthy",
            "connection_status": "ok",
            "stages": stages,
            "retention": retention,
            "finished_at": utc_now(),
        }
    )
    if retention.get("status") != "ok":
        payload["warnings"].append(str(retention.get("error") or "版本保留清理失败"))
    return finalize_payload(args.alias, paths, deployment_spec, payload, deployment_id, args.timeout)


def rollback(args: argparse.Namespace) -> dict[str, Any]:
    if args.strategy == "directory-swap":
        return directory_rollback(args)
    if not args.release_root:
        raise ValueError("versioned-link 必须提供 --release-root")
    started_at = utc_now()
    paths = layout(args.app, args.release_root)
    payload = base_payload("deployment_rollback", args.alias, args.app, args.release_root)
    deployment_id = "rollback-" + uuid.uuid4().hex[:12]
    payload.update(
        {
            "operation": "rollback",
            "deployment_id": deployment_id,
            "started_at": started_at,
            "stages": [],
            "warnings": [],
        }
    )
    inspection_result = inspect_target(args.alias, args.app, args.release_root, args.timeout)
    inspection = inspection_result.get("inspection")
    if inspection is None:
        return stage_failure(
            payload,
            [],
            "inspect",
            {
                "status": inspection_result.get("status"),
                "connection_status": inspection_result.get("connection_status"),
                "error": inspection_result.get("error") or "无法读取目标发布目录",
            },
        )
    service = args.service or inferred_service(inspection)
    health_checks = health_specs(args.health_check) if args.health_check else inferred_health_checks(inspection)
    current_id = current_release_id(str(inspection.get("current_target", "")))
    current = verified_release(inspection, current_id)
    if not service:
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "无法确定要重启的服务，请提供 --service"},
        )
    target_id = args.release
    if target_id:
        if not RELEASE_ID_RE.fullmatch(target_id):
            return stage_failure(
                payload,
                [],
                "precondition",
                {"status": "failed", "connection_status": "ok", "error": "--release 只能是单级 release ID"},
            )
        target = verified_release(inspection, target_id)
    else:
        target = next(
            (
                release
                for release in sort_releases(inspection.get("releases", []))
                if release.get("release_id") != current_id and release.get("state") != "invalid"
            ),
            None,
        )
        target_id = str(target.get("release_id")) if target else None
    if not target or not target_id:
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "没有可验证的回退 release"},
        )
    if target_id == current_id:
        return stage_failure(
            payload,
            [],
            "precondition",
            {"status": "failed", "connection_status": "ok", "error": "目标 release 已经是 current"},
        )
    payload.update(
        {
            "status": "ready",
            "connection_status": "ok",
            "service": service,
            "health_checks": health_checks,
            "release": target,
            "previous_release": current,
        }
    )
    deployment_spec = {
        "schema_version": SCHEMA_VERSION,
        "operation": "rollback",
        "deployment_id": deployment_id,
        "app": args.app,
        "release_id": target_id,
        "release_root": args.release_root,
        "current_link": paths["current_link"],
        "service": service,
        "health_checks": health_checks,
        "keep_releases": args.keep_releases,
        "started_at": started_at,
    }
    stages: list[dict[str, Any]] = [stage_event("staged", release_id=target_id, reused=True)]
    switched = switch_release(args.alias, paths, target_id, deployment_id, args.timeout)
    payload["switch"] = switched
    if switched.get("status") != "ok":
        failure_payload = stage_failure(payload, stages, "switch", switched)
        if current:
            recovery = attempt_recovery(
                args.alias,
                paths,
                current_id or "",
                service,
                health_checks,
                deployment_id,
                args.timeout,
                "回滚切换失败后的保护性恢复",
            )
            failure_payload["rollback"] = recovery
            if recovery.get("status") == "ok":
                failure_payload["stages"].append(stage_event("rolled_back", release_id=current_id))
                failure_payload.update({"status": "rolled_back", "stage": "rolled_back"})
        else:
            failure_payload["rollback"] = {"attempted": False, "status": "not_available"}
        return finalize_payload(args.alias, paths, deployment_spec, failure_payload, deployment_id, args.timeout)
    stages.append(stage_event("switched", release_id=target_id, current_target=switched.get("current_target")))
    runtime = restart_and_health(args.alias, service, health_checks, args.timeout)
    payload["service_status"] = runtime.get("service_status")
    payload["health"] = runtime.get("health")
    if runtime.get("status") != "healthy":
        failure_payload = stage_failure(payload, stages, str(runtime.get("failed_stage") or "health"), runtime)
        if current:
            recovery = attempt_recovery(
                args.alias,
                paths,
                current_id or "",
                service,
                health_checks,
                deployment_id,
                args.timeout,
                "回滚后的服务重启或健康检查失败",
            )
            failure_payload["rollback"] = recovery
            if recovery.get("status") == "ok":
                failure_payload["stages"].append(stage_event("rolled_back", release_id=current_id))
                failure_payload.update({"status": "rolled_back", "stage": "rolled_back"})
        else:
            failure_payload["rollback"] = {"attempted": False, "status": "not_available"}
        return finalize_payload(args.alias, paths, deployment_spec, failure_payload, deployment_id, args.timeout)
    stages.extend([stage_event("restarted"), stage_event("healthy")])
    keep_ids = keep_release_ids(inspection, [target_id, current_id or ""], args.keep_releases)
    retention = prune_releases(args.alias, paths["releases_root"], keep_ids, args.timeout)
    payload.update(
        {
            "status": "healthy",
            "stage": "healthy",
            "connection_status": "ok",
            "stages": stages,
            "retention": retention,
            "finished_at": utc_now(),
        }
    )
    if retention.get("status") != "ok":
        payload["warnings"].append(str(retention.get("error") or "版本保留清理失败"))
    return finalize_payload(args.alias, paths, deployment_spec, payload, deployment_id, args.timeout)


def status(args: argparse.Namespace) -> dict[str, Any]:
    if args.strategy == "directory-swap":
        return directory_status(args)
    if not args.release_root:
        raise ValueError("versioned-link 必须提供 --release-root")
    inspection_result = inspect_target(args.alias, args.app, args.release_root, args.timeout)
    payload = base_payload("deployment_status", args.alias, args.app, args.release_root)
    if inspection_result.get("inspection") is None:
        payload.update(
            {
                "status": inspection_result["status"],
                "connection_status": inspection_result["connection_status"],
                "error": inspection_result.get("error"),
                "current_release": None,
                "releases": [],
                "service_status": {"status": "unavailable", "connection_status": "unavailable"},
                "last_result": None,
                "warnings": [],
            }
        )
        return payload
    inspection = inspection_result["inspection"]
    current_id = current_release_id(inspection.get("current_target", ""))
    current = next((item for item in inspection["releases"] if item.get("release_id") == current_id), None)
    service_name = args.service or inferred_service(inspection)
    service_status: dict[str, Any] = {"status": "not_requested", "connection_status": "not_requested"}
    if service_name:
        service_status = run_host_service(args.alias, service_name, args.timeout)
    warnings = list(inspection.get("warnings", []))
    if current_id and current is None:
        warnings.append(f"current 链接指向未找到 manifest 的版本：{current_id}")
    if service_status.get("status") in {"unavailable", "failed", "unknown"}:
        warnings.append("服务状态不可用或服务单元未安装")
    payload.update(
        {
            "status": "partial" if warnings else inspection_result["status"],
            "connection_status": "ok",
            "current_target": inspection.get("current_target", ""),
            "current_release": current,
            "releases": sort_releases(inspection.get("releases", [])),
            "service": service_name,
            "service_status": service_status,
            "last_result": inspection.get("last_result") or None,
            "warnings": warnings,
        }
    )
    return payload


def history(args: argparse.Namespace) -> dict[str, Any]:
    if args.strategy == "directory-swap":
        return directory_history(args)
    if not args.release_root:
        raise ValueError("versioned-link 必须提供 --release-root")
    inspection_result = inspect_target(args.alias, args.app, args.release_root, args.timeout)
    payload = base_payload("deployment_history", args.alias, args.app, args.release_root)
    if inspection_result.get("inspection") is None:
        payload.update(
            {
                "status": inspection_result["status"],
                "connection_status": inspection_result["connection_status"],
                "error": inspection_result.get("error"),
                "current_release": None,
                "releases": [],
                "warnings": [],
            }
        )
        return payload
    inspection = inspection_result["inspection"]
    releases = sort_releases(inspection.get("releases", []))
    current_id = current_release_id(inspection.get("current_target", ""))
    current = next((item for item in releases if item.get("release_id") == current_id), None)
    payload.update(
        {
            "status": inspection_result["status"],
            "connection_status": "ok",
            "current_release": current,
            "releases": releases[: args.limit],
            "limit": args.limit,
            "warnings": inspection.get("warnings", []),
        }
    )
    return payload


def render_human(payload: dict[str, Any]) -> None:
    kind = str(payload.get("kind", ""))
    print(
        f"# kind={kind} target={payload.get('target', {}).get('alias', '')} "
        f"app={payload.get('app', '')} status={payload.get('status', 'unknown')}"
    )
    if kind.startswith("registry_"):
        print(f"registry_revision={payload.get('registry_revision', '')}")
        if isinstance(payload.get("object"), dict):
            print(f"object={payload['object'].get('id', '')}")
        for item in payload.get("objects", []):
            if isinstance(item, dict):
                print(f"object={item.get('id', '')} name={item.get('name', '')}")
        return
    if kind in {"service_inspect", "service_adopt", "service_migration_plan"}:
        print(f"ssh_alias={payload.get('ssh_alias', '')}")
        print(f"deployment_id={payload.get('deployment_id', '')}")
        print(f"registration_status={payload.get('registration_status', '')}")
        print(f"legacy_mode={payload.get('legacy_mode', '')}")
        drift = payload.get("drift")
        if isinstance(drift, dict):
            print(f"drift_status={drift.get('status', '')}")
            for name, check in drift.get("checks", {}).items():
                print(f"drift_check={name} status={check.get('status', '')}")
        for conflict in payload.get("conflicts", []):
            print(f"conflict={conflict.get('type', '')} deployment={conflict.get('deployment_id', '')}")
        for item in payload.get("preconditions", []):
            print(f"precondition={item.get('name', '')} status={item.get('status', '')}")
        for action in payload.get("actions", []):
            print(f"action={action}")
        if payload.get("error"):
            print(f"error: {payload['error']}")
        return
    if kind == "deployment_doctor":
        print(f"deployment_id={payload.get('deployment_id', '')}")
        print(f"backup_coverage={payload.get('backup_coverage', {}).get('status', '')}")
        for item in payload.get("backup_coverage", {}).get("checks", []):
            print(f"backup_check={item.get('name', '')} status={item.get('status', '')}")
        for item in payload.get("backup_coverage", {}).get("repositories", []):
            print(f"backup_repository={item.get('repository_id', '')} status={item.get('status', '')}")
        return
    if payload.get("error"):
        print(f"error: {payload['error']}")
    if payload.get("deployment_id"):
        print(f"deployment_id={payload['deployment_id']}")
    if payload.get("plan_status"):
        print(f"plan_status={payload['plan_status']}")
    if isinstance(payload.get("project"), dict):
        print(f"project={payload['project'].get('id', '')}")
    if isinstance(payload.get("environment"), dict):
        print(f"environment={payload['environment'].get('id', '')}")
    if payload.get("stage"):
        print(f"stage={payload['stage']}")
    if payload.get("failed_stage"):
        print(f"failed_stage={payload['failed_stage']}")
    if isinstance(payload.get("release"), dict):
        release = payload["release"]
        print(f"release={release.get('release_id', '')} path={release.get('path', '')}")
    for stage in payload.get("stages", []):
        print(
            f"stage_event={stage.get('name', '')} status={stage.get('status', '')}"
            + (f" failed_stage={stage.get('failed_stage')}" if stage.get("failed_stage") else "")
        )
    for stage in payload.get("creation_stages", []):
        print(f"creation_stage={stage.get('name', '')} status={stage.get('status', '')}")
    if isinstance(payload.get("rollback"), dict):
        rollback = payload["rollback"]
        print(f"rollback={rollback.get('status', '')} release={rollback.get('release_id', '')}")
    if payload.get("current_release"):
        current = payload["current_release"]
        print(f"current_release={current.get('release_id', '')} path={current.get('path', '')}")
    elif "current_release" in payload:
        print("current_release=")
    if "candidate_release" in payload:
        candidate = payload["candidate_release"]
        print(f"candidate_release={candidate.get('release_id') or ''} path={candidate.get('path') or ''}")
    if payload.get("service"):
        print(f"service={payload['service']}")
    service_status = payload.get("service_status")
    if isinstance(service_status, dict) and service_status.get("status"):
        print(f"service_status={service_status.get('status')}")
    for item in payload.get("preconditions", []):
        print(f"precondition={item.get('name', '')} status={item.get('status', '')}")
    for release in payload.get("releases", []):
        print(
            "release="
            + str(release.get("release_id", ""))
            + " state="
            + str(release.get("state", ""))
            + " created_at="
            + str(release.get("created_at", ""))
        )
    for action in payload.get("actions", []):
        print(f"action={action}")
    for warning in payload.get("warnings", []):
        print(f"warning: {warning}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def add_registry_output_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("alias")
    parser.add_argument("--app", required=True)
    parser.add_argument("--release-root")
    add_common_args(parser)


def add_deployment_strategy_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="versioned-link")
    parser.add_argument("--service-type", choices=sorted(SERVICE_TYPES), default="systemd")
    parser.add_argument("--live-path")
    parser.add_argument("--state-root")
    parser.add_argument("--nginx-server-name")


def add_service_lifecycle_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_id")
    parser.add_argument("service_id")
    parser.add_argument("environment")
    parser.add_argument("--ssh-alias", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--artifact-sha256")
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--service-manager", default="systemd")
    parser.add_argument("--recipe", default=BUILTIN_RECIPE_ID)
    parser.add_argument("--local-project-path")
    parser.add_argument("--path", action="append", help="路径事实，格式为 kind=/absolute/path")
    parser.add_argument(
        "--port",
        action="append",
        help="端口事实，格式为 protocol:bind_address:port[:purpose[:public]]",
    )
    parser.add_argument("--health-check", action="append")
    parser.add_argument("--keep-releases", type=int, default=5)
    parser.add_argument("--start-command")
    parser.add_argument("--run-user")
    parser.add_argument("--working-directory")
    parser.add_argument("--environment-file")
    parser.add_argument("--db-source", action="append")
    parser.add_argument("--backup-asset", action="append")
    add_common_args(parser)


def add_service_observation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("project_id")
    parser.add_argument("service_id")
    parser.add_argument("environment")
    parser.add_argument("--ssh-alias", required=True)
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--unit", required=True)
    parser.add_argument("--service-manager", default="systemd")
    parser.add_argument("--path", action="append", help="期望路径，格式为 kind=/absolute/path")
    parser.add_argument(
        "--port",
        action="append",
        help="期望端口，格式为 protocol:bind_address:port[:purpose[:public]]",
    )
    add_common_args(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="编排版本化或历史目录兼容部署的计划、执行、状态和回滚。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="生成只读部署计划")
    add_target_args(plan_parser)
    plan_parser.add_argument("--artifact", required=True)
    plan_parser.add_argument("--artifact-sha256")
    plan_parser.add_argument("--service")
    plan_parser.add_argument("--health-check", action="append")
    plan_parser.add_argument("--keep-releases", type=int, default=5)
    add_deployment_strategy_args(plan_parser)
    plan_parser.set_defaults(func=plan)

    apply_parser = subparsers.add_parser("apply", help="上传制品并执行部署")
    add_target_args(apply_parser)
    apply_parser.add_argument("--artifact", required=True)
    apply_parser.add_argument("--artifact-sha256")
    apply_parser.add_argument("--service")
    apply_parser.add_argument("--health-check", action="append")
    apply_parser.add_argument("--keep-releases", type=int, default=5)
    add_deployment_strategy_args(apply_parser)
    apply_parser.add_argument(
        "--upload-chunk-size",
        type=int,
        default=DEFAULT_UPLOAD_CHUNK_SIZE,
        help="上传时单次 SFTP 写入字节数",
    )
    apply_parser.set_defaults(func=apply)

    status_parser = subparsers.add_parser("status", help="查看当前版本和最近部署结果")
    add_target_args(status_parser)
    status_parser.add_argument("--service")
    add_deployment_strategy_args(status_parser)
    status_parser.set_defaults(func=status)

    history_parser = subparsers.add_parser("history", help="查看受管版本和部署 manifest")
    add_target_args(history_parser)
    history_parser.add_argument("--limit", type=int, default=20)
    add_deployment_strategy_args(history_parser)
    history_parser.set_defaults(func=history)

    rollback_parser = subparsers.add_parser("rollback", help="切换到指定或上一个可用 release")
    add_target_args(rollback_parser)
    rollback_parser.add_argument("--release")
    rollback_parser.add_argument("--service")
    rollback_parser.add_argument("--health-check", action="append")
    rollback_parser.add_argument("--keep-releases", type=int, default=5)
    add_deployment_strategy_args(rollback_parser)
    rollback_parser.set_defaults(func=rollback)

    project_parser = subparsers.add_parser("project", help="登记和查看项目")
    project_subparsers = project_parser.add_subparsers(dest="project_command", required=True)
    project_register = project_subparsers.add_parser("register", help="登记项目")
    project_register.add_argument("project_id")
    project_register.add_argument("--name", required=True)
    project_register.add_argument("--alias", action="append")
    project_register.add_argument("--local-path")
    project_register.add_argument("--repository-source")
    project_register.add_argument("--description")
    project_register.add_argument("--upsert", action="store_true")
    add_registry_output_args(project_register)
    project_register.set_defaults(func=register_project)
    project_list = project_subparsers.add_parser("list", help="列出项目")
    project_list.set_defaults(object_kind="project", func=list_registry_objects)
    add_registry_output_args(project_list)

    environment_parser = subparsers.add_parser("environment", help="登记和查看逻辑环境")
    environment_subparsers = environment_parser.add_subparsers(
        dest="environment_command", required=True
    )
    environment_register = environment_subparsers.add_parser("register", help="登记环境")
    environment_register.add_argument("environment_id")
    environment_register.add_argument("--name")
    environment_register.add_argument("--description")
    environment_register.add_argument("--upsert", action="store_true")
    add_registry_output_args(environment_register)
    environment_register.set_defaults(func=register_environment)
    environment_list = environment_subparsers.add_parser("list", help="列出环境")
    environment_list.set_defaults(object_kind="environment", func=list_registry_objects)
    add_registry_output_args(environment_list)

    service_parser = subparsers.add_parser("service", help="登记服务并生成新服务创建模型")
    service_subparsers = service_parser.add_subparsers(dest="service_command", required=True)
    service_register_parser = service_subparsers.add_parser("register", help="登记服务")
    service_register_parser.add_argument("project_id")
    service_register_parser.add_argument("service_id")
    service_register_parser.add_argument("--name", required=True)
    service_register_parser.add_argument("--alias", action="append")
    service_register_parser.add_argument("--service-type", default="systemd")
    service_register_parser.add_argument("--description")
    service_register_parser.add_argument("--upsert", action="store_true")
    add_registry_output_args(service_register_parser)
    service_register_parser.set_defaults(func=register_service)
    service_list_parser = service_subparsers.add_parser("list", help="列出服务")
    service_list_parser.add_argument("--project-id")
    service_list_parser.set_defaults(object_kind="service", func=list_registry_objects)
    add_registry_output_args(service_list_parser)
    service_plan_parser = service_subparsers.add_parser("plan", help="生成新服务只读创建计划")
    add_service_lifecycle_args(service_plan_parser)
    service_plan_parser.set_defaults(func=service_plan)
    service_create_parser = service_subparsers.add_parser(
        "create", help="登记 draft deployment 并保存创建阶段模型"
    )
    add_service_lifecycle_args(service_create_parser)
    service_create_parser.set_defaults(func=service_create)
    service_inspect_parser = service_subparsers.add_parser(
        "inspect", help="读取既有服务事实并检查注册表 drift"
    )
    add_service_observation_args(service_inspect_parser)
    service_inspect_parser.set_defaults(func=service_inspect)
    service_adopt_parser = service_subparsers.add_parser(
        "adopt", help="在明确确认后登记既有服务的 adopted 基线"
    )
    add_service_observation_args(service_adopt_parser)
    service_adopt_parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认写入本地注册表；不会执行远端写操作",
    )
    service_adopt_parser.set_defaults(func=service_adopt)
    service_migration_parser = service_subparsers.add_parser(
        "migrate-plan", help="为 legacy 单目录服务生成只读迁移计划"
    )
    add_service_observation_args(service_migration_parser)
    service_migration_parser.set_defaults(func=service_migration_plan)

    doctor_parser = subparsers.add_parser(
        "doctor", help="检查 deployment 的备份资产覆盖和最近校验状态"
    )
    doctor_parser.add_argument("project_id")
    doctor_parser.add_argument("service_id")
    doctor_parser.add_argument("environment")
    add_registry_output_args(doctor_parser)
    doctor_parser.set_defaults(func=doctor)

    recipe_parser = subparsers.add_parser("recipe", help="登记和查看受管 recipe")
    recipe_subparsers = recipe_parser.add_subparsers(dest="recipe_command", required=True)
    recipe_register_parser = recipe_subparsers.add_parser("register", help="登记托管脚本 recipe")
    recipe_register_parser.add_argument("recipe_id")
    recipe_register_parser.add_argument("--name", required=True)
    recipe_register_parser.add_argument("--stage", action="append")
    recipe_register_parser.add_argument("--upsert", action="store_true")
    add_registry_output_args(recipe_register_parser)
    recipe_register_parser.set_defaults(func=register_recipe)
    recipe_list_parser = recipe_subparsers.add_parser("list", help="列出内置和托管 recipe")
    add_registry_output_args(recipe_list_parser)
    recipe_list_parser.set_defaults(func=list_recipes)
    return parser


def command_exit_code(payload: dict[str, Any]) -> int:
    return 0 if payload.get("status") in {
        "ready", "ok", "partial", "healthy", "created", "updated", "drafted",
        "observed", "adopted", "not_applicable",
    } else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "timeout", DEFAULT_TIMEOUT) < 1:
            raise ValueError("--timeout 必须大于 0")
        if getattr(args, "keep_releases", 1) < 1:
            raise ValueError("--keep-releases 必须大于 0")
        if getattr(args, "upload_chunk_size", DEFAULT_UPLOAD_CHUNK_SIZE) < 1:
            raise ValueError("--upload-chunk-size 必须大于 0")
        if getattr(args, "limit", 1) < 1:
            raise ValueError("--limit 必须大于 0")
        payload = args.func(args)
        if args.json:
            print_json(payload)
        else:
            render_human(payload)
        return command_exit_code(payload)
    except Exception as exc:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": getattr(args, "command", "unknown"),
            "status": "failed",
            "connection_status": "unknown",
            "error": str(exc),
        }
        if getattr(args, "json", False):
            print_json(payload)
        else:
            print(f"error: {payload['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
