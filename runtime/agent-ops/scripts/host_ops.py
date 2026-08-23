#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
SSH_OUTPUT_BYTES = 64_000
TRUNCATED_RE = re.compile(r"\.\.\. omitted \d+ bytes \.\.\.")
PROCESS_RE = re.compile(r"\(\(\"([^\"]+)\",pid=(\d+)")
INSPECT_META = "__HOSTX_INSPECT_META__"
INSPECT_UNIT_BEGIN = "__HOSTX_INSPECT_UNIT_BEGIN__"
INSPECT_UNIT_END = "__HOSTX_INSPECT_UNIT_END__"
INSPECT_DROPIN_BEGIN = "__HOSTX_INSPECT_DROPIN_BEGIN__"
INSPECT_DROPIN_END = "__HOSTX_INSPECT_DROPIN_END__"
INSPECT_PROCESS_BEGIN = "__HOSTX_INSPECT_PROCESS_BEGIN__"
INSPECT_PROCESS_END = "__HOSTX_INSPECT_PROCESS_END__"
INSPECT_PORTS_BEGIN = "__HOSTX_INSPECT_PORTS_BEGIN__"
INSPECT_PORTS_END = "__HOSTX_INSPECT_PORTS_END__"


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def trim_error(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > 1000:
        return text[-1000:]
    return text


def target_ref(alias: str) -> dict[str, str]:
    return {"alias": alias}


def command_with_marker(marker: str, body: str) -> str:
    return f"# hostx:{marker}\n{body.strip()}\n"


def facts_command() -> str:
    return command_with_marker(
        "facts",
        """
printf 'hostname\t%s\n' "$(hostname 2>/dev/null || true)"
printf 'os\t%s\n' "$(uname -s 2>/dev/null || true)"
printf 'kernel\t%s\n' "$(uname -r 2>/dev/null || true)"
printf 'arch\t%s\n' "$(uname -m 2>/dev/null || true)"
if [ -r /proc/uptime ]; then
    printf 'uptime_seconds\t%s\n' "$(awk '{print $1}' /proc/uptime)"
else
    printf 'warning\t/proc/uptime unavailable\n'
fi
if [ -r /proc/loadavg ]; then
    awk '{printf "load_1\t%s\nload_5\t%s\nload_15\t%s\n", $1, $2, $3}' /proc/loadavg
else
    printf 'warning\t/proc/loadavg unavailable\n'
fi
""",
    )


def service_command(unit: str) -> str:
    quoted = shlex.quote(unit)
    return command_with_marker(
        "service",
        f"""
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'error\tservice manager command not found\n'
    exit 127
fi
printf 'name\t%s\n' {quoted}
printf 'manager\tsystemd\n'
printf 'load_state\t%s\n' "$(systemctl show {quoted} --property=LoadState --value 2>/dev/null || true)"
printf 'active\t%s\n' "$(systemctl is-active {quoted} 2>/dev/null || true)"
printf 'substate\t%s\n' "$(systemctl show {quoted} --property=SubState --value 2>/dev/null || true)"
printf 'pid\t%s\n' "$(systemctl show {quoted} --property=MainPID --value 2>/dev/null || true)"
printf 'enabled\t%s\n' "$(systemctl is-enabled {quoted} 2>/dev/null || true)"
""",
    )


def service_inspect_command(unit: str) -> str:
    quoted = shlex.quote(unit)
    return command_with_marker(
        "service_inspect",
        f"""
if ! command -v systemctl >/dev/null 2>&1; then
    printf 'error\\tservice manager command not found\\n'
    exit 127
fi
unit={quoted}
emit_meta() {{
    printf '{INSPECT_META}\\t%s\\t%s\\n' "$1" "$2"
}}
show_property() {{
    property=$1
    value=$(systemctl show "$unit" --property="$property" --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
    emit_meta "$property" "$value"
}}
for property in \\
    Id Names LoadState ActiveState SubState MainPID UnitFileState FragmentPath DropInPaths \\
    ExecStart ExecStartPre ExecStartPost User WorkingDirectory EnvironmentFiles \\
    After Before Requires Requisite Wants WantedBy Conflicts BindsTo PartOf \\
    ControlGroup ConfigurationDirectory StateDirectory CacheDirectory LogsDirectory \\
    RuntimeDirectory RootDirectory RootDirectoryStartOnly
do
    show_property "$property"
done

fragment=$(systemctl show "$unit" --property=FragmentPath --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
printf '{INSPECT_UNIT_BEGIN}\\n'
if [ -n "$fragment" ] && [ -r "$fragment" ]; then
    cat -- "$fragment"
fi
printf '{INSPECT_UNIT_END}\\n'

drop_ins=$(systemctl show "$unit" --property=DropInPaths --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
for path in $drop_ins; do
    printf '{INSPECT_DROPIN_BEGIN}\\t%s\\n' "$path"
    if [ -r "$path" ]; then
        cat -- "$path"
    else
        emit_meta "DropInError" "$path"
    fi
    printf '{INSPECT_DROPIN_END}\\n'
done

main_pid=$(systemctl show "$unit" --property=MainPID --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
printf '{INSPECT_PROCESS_BEGIN}\\n'
if [ "${{main_pid:-0}}" -gt 0 ] 2>/dev/null && command -v ps >/dev/null 2>&1; then
    ps -p "$main_pid" -eo pid=,ppid=,user=,stat=,pcpu=,pmem=,args= || true
fi
printf '{INSPECT_PROCESS_END}\\n'
if [ "${{main_pid:-0}}" -gt 0 ] 2>/dev/null; then
    if command -v readlink >/dev/null 2>&1; then
        emit_meta "ProcessCwd" "$(readlink "/proc/$main_pid/cwd" 2>/dev/null || true)"
        emit_meta "ProcessExe" "$(readlink "/proc/$main_pid/exe" 2>/dev/null || true)"
    fi
    if [ -r "/proc/$main_pid/cmdline" ] && command -v tr >/dev/null 2>&1; then
        emit_meta "ProcessCommand" "$(tr '\\0' ' ' < "/proc/$main_pid/cmdline" 2>/dev/null || true)"
    fi
fi
cgroup=$(systemctl show "$unit" --property=ControlGroup --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
if [ -n "$cgroup" ] && [ -r "/sys/fs/cgroup$cgroup/cgroup.procs" ] && command -v tr >/dev/null 2>&1; then
    emit_meta "CGroupPids" "$(tr '\\n' ' ' < "/sys/fs/cgroup$cgroup/cgroup.procs" 2>/dev/null || true)"
fi

printf '{INSPECT_PORTS_BEGIN}\\n'
if command -v ss >/dev/null 2>&1; then
    ss -H -ltnup || true
else
    emit_meta "PortsError" "ss command not found"
fi
printf '{INSPECT_PORTS_END}\\n'

load_state=$(systemctl show "$unit" --property=LoadState --value 2>/dev/null | tr '\\n' ' ' | sed 's/[[:space:]]*$//' || true)
if [ "$load_state" = "not-found" ]; then
    exit 3
fi
""",
    )


def process_command(pid: str | None = None) -> str:
    selector = f"-p {shlex.quote(pid)}" if pid else ""
    return command_with_marker(
        "process_show" if pid else "process_list",
        f"""
if ! command -v ps >/dev/null 2>&1; then
    printf 'process command not found\n'
    exit 127
fi
ps {selector} -eo pid=,ppid=,user=,stat=,pcpu=,pmem=,args=
""",
    )


def logs_command(source: str, lines: int, since: str | None) -> str:
    quoted = shlex.quote(source)
    if source.startswith("/"):
        if since:
            raise ValueError("--since 仅适用于 systemd unit 日志")
        body = f"""
if ! command -v tail >/dev/null 2>&1; then
    printf 'tail command not found\n'
    exit 127
fi
tail -n {lines} -- {quoted}
"""
    else:
        since_arg = f" --since {shlex.quote(since)}" if since else ""
        body = f"""
if ! command -v journalctl >/dev/null 2>&1; then
    printf 'journalctl command not found\n'
    exit 127
fi
journalctl -u {quoted} -n {lines} --no-pager --output=short-iso{since_arg}
"""
    return command_with_marker("logs", body)


def ports_command() -> str:
    return command_with_marker(
        "ports",
        """
if ! command -v ss >/dev/null 2>&1; then
    printf 'ss command not found\n'
    exit 127
fi
ss -H -ltnup
""",
    )


def disk_command(path: str) -> str:
    return command_with_marker(
        "disk",
        f"""
if ! command -v df >/dev/null 2>&1; then
    printf 'df command not found\n'
    exit 127
fi
df -Pk {shlex.quote(path)}
""",
    )


def run_remote(alias: str, command: str, timeout: int) -> dict[str, Any]:
    executable = os.environ.get("HOSTX_SSHX", "sshx")
    if not Path(executable).is_absolute() and shutil.which(executable) is None:
        return {
            "transport": "unavailable",
            "error": f"找不到 sshx 命令：{executable}",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "truncated": False,
        }
    argv = [
        executable,
        "exec",
        "--json",
        "--timeout",
        str(timeout),
        "--max-bytes",
        str(SSH_OUTPUT_BYTES),
        alias,
        command,
    ]
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
            "error": "sshx 执行超时",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "truncated": False,
        }
    except OSError as exc:
        return {
            "transport": "unavailable",
            "error": str(exc),
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "truncated": False,
        }

    raw = completed.stdout.strip()
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {
            "transport": "unavailable",
            "error": trim_error(completed.stderr) or "sshx 未返回 JSON 结果",
            "stdout": "",
            "stderr": trim_error(completed.stderr),
            "exit_code": None,
            "truncated": False,
        }
    if not isinstance(payload, dict):
        return {
            "transport": "unavailable",
            "error": "sshx 返回了非对象 JSON",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "truncated": False,
        }
    if payload.get("error"):
        return {
            "transport": "unavailable",
            "error": trim_error(payload.get("error")),
            "stdout": str(payload.get("stdout", "")),
            "stderr": trim_error(payload.get("stderr")),
            "exit_code": None,
            "truncated": False,
        }
    stdout = str(payload.get("stdout", ""))
    stderr = str(payload.get("stderr", ""))
    try:
        exit_code = int(payload.get("exit_code", completed.returncode))
    except (TypeError, ValueError):
        exit_code = completed.returncode
    return {
        "transport": "ok",
        "error": "",
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "truncated": bool(TRUNCATED_RE.search(stdout)),
    }


def classify_remote(remote: dict[str, Any]) -> tuple[str, str]:
    if remote.get("transport") != "ok":
        return "unavailable", "connection_unavailable"
    exit_code = remote.get("exit_code")
    if exit_code in (126, 127):
        return "unknown", "command_missing"
    if exit_code not in (0, None):
        return "failed", "remote_command_failed"
    if remote.get("truncated"):
        return "partial", "output_truncated"
    return "ok", ""


def new_result(kind: str, alias: str, remote: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "target": target_ref(alias),
        "collected_at": utc_now(),
        "status": "ok",
        "connection_status": "ok",
    }
    if remote is not None:
        status, reason = classify_remote(remote)
        result["status"] = status
        result["connection_status"] = "ok" if remote.get("transport") == "ok" else "unavailable"
        if reason == "command_missing":
            result["availability"] = "missing"
        error = trim_error(remote.get("error")) or trim_error(remote.get("stderr"))
        if error and status != "ok":
            result["error"] = error
        if remote.get("exit_code") not in (None, 0):
            result["remote_exit_code"] = remote.get("exit_code")
        if remote.get("truncated"):
            result.setdefault("warnings", []).append("远端输出已被 sshx 截断")
    return result


def parse_key_values(text: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    warnings: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" not in line:
            warnings.append(f"无法解析远端字段：{line[:200]}")
            continue
        key, value = line.split("\t", 1)
        values[key] = value
    return values, warnings


def parse_service_inspect_output(text: str) -> dict[str, Any]:
    metadata: dict[str, str] = {}
    unit_lines: list[str] = []
    drop_ins: list[dict[str, Any]] = []
    process_lines: list[str] = []
    ports_lines: list[str] = []
    warnings: list[str] = []
    section: str | None = None
    current_drop_in: dict[str, Any] | None = None

    def finish_drop_in() -> None:
        nonlocal current_drop_in
        if current_drop_in is not None:
            current_drop_in["content"] = "\n".join(current_drop_in.pop("content_lines", []))
            drop_ins.append(current_drop_in)
            current_drop_in = None

    for line in text.splitlines():
        if line == INSPECT_UNIT_BEGIN:
            section = "unit"
            continue
        if line == INSPECT_UNIT_END:
            if section != "unit":
                warnings.append("远端 unit 文件区段不完整")
            section = None
            continue
        if line.startswith(INSPECT_DROPIN_BEGIN + "\t"):
            finish_drop_in()
            current_drop_in = {
                "path": line.split("\t", 1)[1],
                "content_lines": [],
            }
            section = "dropin"
            continue
        if line == INSPECT_DROPIN_END:
            if current_drop_in is None:
                warnings.append("远端 drop-in 区段缺少路径")
            finish_drop_in()
            section = None
            continue
        if line == INSPECT_PROCESS_BEGIN:
            section = "process"
            continue
        if line == INSPECT_PROCESS_END:
            section = None
            continue
        if line == INSPECT_PORTS_BEGIN:
            section = "ports"
            continue
        if line == INSPECT_PORTS_END:
            section = None
            continue
        if line.startswith(INSPECT_META + "\t"):
            parts = line.split("\t", 2)
            if len(parts) == 3:
                metadata[parts[1]] = parts[2].strip()
            else:
                warnings.append(f"无法解析 service inspect 元数据：{line[:200]}")
            continue
        if section == "unit":
            unit_lines.append(line)
        elif section == "dropin" and current_drop_in is not None:
            current_drop_in.setdefault("content_lines", []).append(line)
        elif section == "process":
            process_lines.append(line)
        elif section == "ports":
            ports_lines.append(line)
        elif line.startswith("error\t"):
            metadata.setdefault("Error", line.split("\t", 1)[1])
        elif line.strip():
            warnings.append(f"无法解析远端 service inspect 记录：{line[:200]}")
    finish_drop_in()
    return {
        "metadata": metadata,
        "unit_content": "\n".join(unit_lines),
        "drop_ins": drop_ins,
        "process_text": "\n".join(process_lines),
        "ports_text": "\n".join(ports_lines),
        "warnings": warnings,
    }


def split_systemd_list(value: str | None) -> list[str]:
    return [item for item in str(value or "").split() if item]


def parse_environment_files(value: str | None) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for item in split_systemd_list(value):
        optional = item.startswith("-")
        path = item[1:] if optional else item
        if path:
            files.append({"path": path, "optional": optional})
    return files


def parse_pid_list(value: str | None) -> list[int]:
    pids: list[int] = []
    for item in split_systemd_list(value):
        try:
            pids.append(int(item))
        except ValueError:
            continue
    return pids


ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s'\"<>|;&,)]*)")


def normalize_absolute_path(value: str) -> str | None:
    path = value.strip().strip("'\"[]{}")
    if not path.startswith("/"):
        return None
    path = path.rstrip(",;:")
    if not path:
        return None
    return posixpath.normpath(path)


def canonical_release_path(path: str) -> str:
    parts = [item for item in path.split("/") if item]
    for index, item in enumerate(parts):
        if item == "current":
            return "/" + "/".join(parts[: index + 1])
        if item == "releases" and index + 1 < len(parts):
            return "/" + "/".join(parts[: index + 2])
    return path


def path_hint(text: str, start: int) -> str | None:
    before = text[max(0, start - 64) : start].lower()
    if re.search(r"(?:--?)(?:config|conf|environment|env)(?:[-_ ]?(?:file|dir|path))?[\s=:]*$", before) or re.search(
        r"(?:config|conf|environment|env)[-_ ]?(?:file|dir|path)?\s*(?:=|:)\s*$", before
    ):
        return "config"
    if re.search(r"(?:--?)(?:data|state|storage|database)(?:[-_ ]?(?:dir|path))?[\s=:]*$", before) or re.search(
        r"(?:data|state|storage|database)[-_ ]?(?:dir|path)?\s*(?:=|:)\s*$", before
    ):
        return "data"
    if re.search(r"(?:--?)(?:log|logs)(?:[-_ ]?(?:file|dir|path))?[\s=:]*$", before) or re.search(
        r"(?:log|logs)[-_ ]?(?:file|dir|path)?\s*(?:=|:)\s*$", before
    ):
        return "log"
    if re.search(r"(?:--?)(?:release|current)(?:[-_ ]?(?:dir|path))?[\s=:]*$", before):
        return "release"
    return None


def extract_absolute_paths(text: str) -> list[tuple[str, str | None]]:
    return [
        (match.group(0), path_hint(text, match.start()))
        for match in ABSOLUTE_PATH_RE.finditer(text)
    ]


def classify_inspect_path(path: str, hint: str | None = None) -> tuple[str, str]:
    normalized = normalize_absolute_path(path) or path
    lowered = normalized.lower()
    if hint in {"config", "data", "log", "release"}:
        kind = hint
    elif "/var/log/" in lowered or lowered.endswith(".log"):
        kind = "log"
    elif lowered.startswith("/etc/") or lowered.endswith((".conf", ".ini", ".yaml", ".yml", ".json", ".toml", ".env")):
        kind = "config"
    elif lowered.startswith(("/var/lib/", "/var/cache/")):
        kind = "data"
    elif any(part in {"current", "releases", "release"} for part in normalized.split("/")):
        kind = "release"
    else:
        kind = "other"
    if kind == "release":
        normalized = canonical_release_path(normalized)
    return kind, normalized


def inspect_path_facts(
    metadata: dict[str, str],
    unit_content: str,
    drop_ins: list[dict[str, Any]],
    process: dict[str, Any],
) -> dict[str, Any]:
    buckets: dict[str, list[str]] = {"release": [], "config": [], "data": [], "log": [], "other": []}
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_path(raw: str, source: str, hint: str | None = None) -> None:
        normalized = normalize_absolute_path(raw)
        if not normalized:
            return
        kind, normalized = classify_inspect_path(normalized, hint)
        key = (kind, normalized)
        if key not in seen:
            seen.add(key)
            buckets[kind].append(normalized)
        evidence_key = (kind, normalized, source)
        if not any(
            item["kind"] == evidence_key[0]
            and item["path"] == evidence_key[1]
            and item["source"] == evidence_key[2]
            for item in evidence
        ):
            evidence.append({"kind": kind, "path": normalized, "source": source})

    def add_text(text: str, source: str, explicit_hint: str | None = None) -> None:
        for raw, hint in extract_absolute_paths(text):
            add_path(raw, source, explicit_hint or hint)

    def add_directory_property(value: str, base: str, kind: str, source: str) -> None:
        for item in split_systemd_list(value):
            path = item if item.startswith("/") else posixpath.join(base, item)
            add_path(path, source, kind)

    for property_name, base, kind in (
        ("ConfigurationDirectory", "/etc", "config"),
        ("StateDirectory", "/var/lib", "data"),
        ("CacheDirectory", "/var/cache", "data"),
        ("LogsDirectory", "/var/log", "log"),
        ("RuntimeDirectory", "/run", "other"),
    ):
        add_directory_property(metadata.get(property_name, ""), base, kind, f"systemd.{property_name}")

    environment_files = parse_environment_files(metadata.get("EnvironmentFiles"))
    for item in environment_files:
        add_path(item["path"], "systemd.EnvironmentFiles", "config")
    if metadata.get("WorkingDirectory"):
        add_path(metadata["WorkingDirectory"], "systemd.WorkingDirectory")
    add_text(metadata.get("ExecStart", ""), "systemd.ExecStart")
    add_text(metadata.get("ProcessCwd", ""), "process.cwd")
    add_text(metadata.get("ProcessExe", ""), "process.exe")
    add_text(metadata.get("ProcessCommand", ""), "process.command")

    def parse_unit_paths(content: str, source_prefix: str) -> None:
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip()
            source = f"{source_prefix}.{key}"
            if key == "EnvironmentFile":
                for item in parse_environment_files(value):
                    add_path(item["path"], source, "config")
            elif key == "WorkingDirectory":
                add_path(value, source)
            elif key.startswith("ExecStart") or key in {"Environment", "ReadWritePaths", "ReadOnlyPaths", "BindPaths"}:
                add_text(value, source)
            elif key in {"ConfigurationDirectory", "StateDirectory", "CacheDirectory", "LogsDirectory", "RuntimeDirectory"}:
                bases = {
                    "ConfigurationDirectory": ("/etc", "config"),
                    "StateDirectory": ("/var/lib", "data"),
                    "CacheDirectory": ("/var/cache", "data"),
                    "LogsDirectory": ("/var/log", "log"),
                    "RuntimeDirectory": ("/run", "other"),
                }
                base, kind = bases[key]
                add_directory_property(value, base, kind, source)

    parse_unit_paths(unit_content, "unit")
    for drop_in in drop_ins:
        parse_unit_paths(str(drop_in.get("content", "")), f"drop-in:{drop_in.get('path', '')}")
    return {**buckets, "evidence": evidence}


def number_or_none(value: str | None, integer: bool = False) -> int | float | None:
    if value in (None, "", "null"):
        return None
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return None


def collect_facts(alias: str, timeout: int) -> dict[str, Any]:
    remote = run_remote(alias, facts_command(), timeout)
    result = new_result("facts", alias, remote)
    facts: dict[str, Any] = {
        "hostname": "",
        "os": "",
        "kernel": "",
        "arch": "",
        "uptime_seconds": None,
        "load": {"1m": None, "5m": None, "15m": None},
    }
    if result["status"] in {"unavailable", "unknown", "failed"}:
        result["facts"] = facts
        result.setdefault("warnings", []).append("主机基础事实未采集")
        return result
    values, warnings = parse_key_values(str(remote.get("stdout", "")))
    facts.update(
        {
            "hostname": values.get("hostname", ""),
            "os": values.get("os", ""),
            "kernel": values.get("kernel", ""),
            "arch": values.get("arch", ""),
            "uptime_seconds": number_or_none(values.get("uptime_seconds")),
            "load": {
                "1m": number_or_none(values.get("load_1")),
                "5m": number_or_none(values.get("load_5")),
                "15m": number_or_none(values.get("load_15")),
            },
        }
    )
    warnings.extend(values.get("warning", "").split("\n") if values.get("warning") else [])
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
        if result["status"] == "ok":
            result["status"] = "partial"
    result["facts"] = facts
    return result


def collect_service(alias: str, unit: str, timeout: int) -> dict[str, Any]:
    remote = run_remote(alias, service_command(unit), timeout)
    result = new_result("service", alias, remote)
    values, warnings = parse_key_values(str(remote.get("stdout", "")))
    load_state = values.get("load_state", "")
    service: dict[str, Any] = {
        "name": values.get("name", unit),
        "manager": values.get("manager", ""),
        "load_state": load_state,
        "active": values.get("active", ""),
        "substate": values.get("substate", ""),
        "pid": number_or_none(values.get("pid"), integer=True),
        "enabled": values.get("enabled", ""),
        "observed_at": result["collected_at"],
        "error": None,
    }
    if load_state == "not-found":
        service["error"] = "服务单元不存在"
        if result["status"] == "ok":
            result["status"] = "failed"
    elif result["status"] != "ok":
        service["error"] = values.get("error") or result.get("error") or "服务状态未采集"
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
    result["service"] = service
    return result


def collect_service_inspect(alias: str, unit: str, timeout: int) -> dict[str, Any]:
    remote = run_remote(alias, service_inspect_command(unit), timeout)
    result = new_result("service_inspect", alias, remote)
    parsed = parse_service_inspect_output(str(remote.get("stdout", "")))
    metadata = parsed["metadata"]
    warnings = list(parsed["warnings"])
    load_state = metadata.get("LoadState", "")
    main_pid = number_or_none(metadata.get("MainPID"), integer=True)
    service: dict[str, Any] = {
        "name": metadata.get("Id") or metadata.get("Names") or unit,
        "manager": "systemd",
        "load_state": load_state,
        "active": metadata.get("ActiveState", ""),
        "active_state": metadata.get("ActiveState", ""),
        "substate": metadata.get("SubState", ""),
        "sub_state": metadata.get("SubState", ""),
        "pid": main_pid,
        "enabled": metadata.get("UnitFileState", ""),
        "observed_at": result["collected_at"],
        "error": None,
    }
    if load_state == "not-found":
        service["error"] = "服务单元不存在"
        if result["status"] == "ok":
            result["status"] = "failed"
    elif result["status"] not in {"ok", "partial"}:
        service["error"] = metadata.get("Error") or result.get("error") or "服务事实未采集"
    elif metadata.get("Error"):
        warnings.append(metadata["Error"])

    environment_files = parse_environment_files(metadata.get("EnvironmentFiles"))
    dependencies = {
        "requires": split_systemd_list(metadata.get("Requires")),
        "requisite": split_systemd_list(metadata.get("Requisite")),
        "wants": split_systemd_list(metadata.get("Wants")),
        "wanted_by": split_systemd_list(metadata.get("WantedBy")),
        "after": split_systemd_list(metadata.get("After")),
        "before": split_systemd_list(metadata.get("Before")),
        "conflicts": split_systemd_list(metadata.get("Conflicts")),
        "binds_to": split_systemd_list(metadata.get("BindsTo")),
        "part_of": split_systemd_list(metadata.get("PartOf")),
    }

    unit_content = str(parsed.get("unit_content", ""))
    fragment_path = metadata.get("FragmentPath", "")
    unit_file = {
        "path": fragment_path or None,
        "exists": bool(fragment_path),
        "content": unit_content,
    }
    drop_ins = [
        {
            "path": item.get("path"),
            "exists": bool(item.get("path")),
            "content": str(item.get("content", "")),
        }
        for item in parsed.get("drop_ins", [])
    ]
    known_drop_in_paths = {str(item.get("path")) for item in drop_ins}
    for path in split_systemd_list(metadata.get("DropInPaths")):
        if path not in known_drop_in_paths:
            drop_ins.append({"path": path, "exists": False, "content": ""})

    process_records, process_warnings = parse_process_lines(str(parsed.get("process_text", "")))
    warnings.extend(process_warnings)
    process: dict[str, Any] = {
        "pid": main_pid,
        "ppid": None,
        "user": "",
        "state": "",
        "cpu": None,
        "memory": None,
        "command": metadata.get("ProcessCommand", "").strip(),
        "start_command": metadata.get("ProcessCommand", "").strip(),
        "cwd": metadata.get("ProcessCwd") or None,
        "executable": metadata.get("ProcessExe") or None,
        "cgroup": metadata.get("ControlGroup") or None,
        "status": "running" if main_pid else "not_running",
    }
    if process_records:
        process.update(process_records[0])
        process["start_command"] = process.get("command", "")
    elif main_pid:
        process["status"] = "unavailable"
        if metadata.get("ProcessError"):
            warnings.append(metadata["ProcessError"])
        else:
            warnings.append("无法读取服务主进程")

    all_ports, port_warnings = parse_ss_lines(str(parsed.get("ports_text", "")))
    warnings.extend(port_warnings)
    known_pids = set(parse_pid_list(metadata.get("CGroupPids")))
    if main_pid:
        known_pids.add(main_pid)
    if known_pids:
        service_ports = [item for item in all_ports if item.get("pid") in known_pids]
        if service_ports or all(item.get("pid") is not None for item in all_ports):
            ports = service_ports
            port_scope = "service"
        else:
            ports = all_ports
            port_scope = "host"
            warnings.append("无法按服务进程过滤监听端口，保留主机监听端口")
    else:
        ports = all_ports
        port_scope = "host"
        if all_ports:
            warnings.append("缺少服务进程 PID，监听端口未按服务过滤")
    if metadata.get("PortsError"):
        warnings.append(metadata["PortsError"])
        port_status = "unknown"
    elif port_warnings:
        port_status = "partial"
    else:
        port_status = "ok"

    paths = inspect_path_facts(metadata, unit_content, drop_ins, process)
    if fragment_path and not unit_content:
        warnings.append("无法读取 systemd unit 文件内容")
    if any(not item["exists"] for item in drop_ins):
        warnings.append("部分 systemd drop-in 文件无法读取")

    result["service"] = service
    result["systemd"] = {
        "fragment_path": fragment_path or None,
        "drop_in_paths": split_systemd_list(metadata.get("DropInPaths")),
        "control_group": metadata.get("ControlGroup") or None,
        "root_directory": metadata.get("RootDirectory") or None,
    }
    result["unit_file"] = unit_file
    result["drop_ins"] = drop_ins
    result["exec_start"] = {
        "main": metadata.get("ExecStart", ""),
        "pre": metadata.get("ExecStartPre", ""),
        "post": metadata.get("ExecStartPost", ""),
    }
    result["user"] = metadata.get("User", "")
    result["working_directory"] = metadata.get("WorkingDirectory") or None
    result["environment_files"] = environment_files
    result["dependencies"] = dependencies
    result["process"] = process
    result["ports"] = ports
    result["ports_status"] = port_status
    result["ports_scope"] = port_scope
    result["paths"] = paths
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
        if result["status"] == "ok":
            result["status"] = "partial"
    return result


def parse_process_lines(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    processes: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("... omitted"):
            continue
        fields = line.split(None, 6)
        if len(fields) < 7:
            warnings.append(f"无法解析进程记录：{line[:200]}")
            continue
        try:
            pid = int(fields[0])
            ppid = int(fields[1])
        except ValueError:
            warnings.append(f"无法解析进程 PID：{line[:200]}")
            continue
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "user": fields[2],
                "state": fields[3][0] if fields[3] else "",
                "cpu": number_or_none(fields[4]),
                "memory": number_or_none(fields[5]),
                "command": fields[6],
            }
        )
    return processes, warnings


def collect_process(
    alias: str,
    timeout: int,
    pattern: str | None = None,
    pid: str | None = None,
) -> dict[str, Any]:
    if pid and not pid.isdigit():
        raise ValueError("PID 必须是数字")
    remote = run_remote(alias, process_command(pid), timeout)
    result = new_result("process", alias, remote)
    processes, warnings = parse_process_lines(str(remote.get("stdout", "")))
    if pattern:
        processes = [item for item in processes if pattern in item["command"] or pattern in item["user"]]
        result["pattern"] = pattern
    if pid and not processes and result["status"] == "ok":
        result["status"] = "failed"
        result["error"] = f"未找到进程：{pid}"
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
        if result["status"] == "ok":
            result["status"] = "partial"
    result["processes"] = processes
    result["truncated"] = bool(remote.get("truncated"))
    return result


def collect_logs(alias: str, source: str, lines: int, since: str | None, timeout: int) -> dict[str, Any]:
    if lines < 1:
        raise ValueError("--lines 必须大于 0")
    remote = run_remote(alias, logs_command(source, lines, since), timeout)
    result = new_result("logs", alias, remote)
    log: dict[str, Any] = {
        "source": source,
        "lines": str(remote.get("stdout", "")).splitlines(),
        "since": since,
        "truncated": bool(remote.get("truncated")),
        "collected_at": result["collected_at"],
        "error": None,
    }
    if result["status"] not in {"ok", "partial"}:
        log["error"] = result.get("error") or "日志未采集"
    result["log"] = log
    return result


def parse_endpoint(value: str) -> tuple[str, int | str | None]:
    value = value.strip()
    if value.startswith("[") and "]" in value:
        end = value.rfind("]")
        address = value[1:end]
        port_text = value[end + 2 :] if value[end + 1 : end + 2] == ":" else ""
    elif ":" in value:
        address, port_text = value.rsplit(":", 1)
    else:
        return value, None
    if port_text in ("", "*"):
        return address, None
    try:
        return address, int(port_text)
    except ValueError:
        return address, port_text


def parse_ss_lines(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    listeners: list[dict[str, Any]] = []
    warnings: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("... omitted"):
            continue
        fields = line.split()
        if len(fields) < 5:
            warnings.append(f"无法解析监听端口记录：{line[:200]}")
            continue
        protocol = fields[0].lower()
        if protocol.startswith("tcp"):
            protocol = "tcp"
        elif protocol.startswith("udp"):
            protocol = "udp"
        local_address, port = parse_endpoint(fields[4])
        process = ""
        pid: int | None = None
        match = PROCESS_RE.search(" ".join(fields[6:])) if len(fields) > 6 else None
        if match:
            process = match.group(1)
            pid = int(match.group(2))
        listeners.append(
            {
                "protocol": protocol,
                "local_address": local_address,
                "port": port,
                "pid": pid,
                "process": process or None,
            }
        )
    return listeners, warnings


def collect_ports(alias: str, timeout: int) -> dict[str, Any]:
    remote = run_remote(alias, ports_command(), timeout)
    result = new_result("ports", alias, remote)
    listeners, warnings = parse_ss_lines(str(remote.get("stdout", "")))
    if warnings:
        result.setdefault("warnings", []).extend(warnings)
        if result["status"] == "ok":
            result["status"] = "partial"
    result["ports"] = listeners
    result["truncated"] = bool(remote.get("truncated"))
    return result


def parse_df_output(text: str, path: str) -> tuple[dict[str, Any] | None, str | None]:
    records = [line for line in text.splitlines() if line.strip() and not line.startswith("Filesystem")]
    if not records:
        return None, f"没有得到磁盘使用记录：{path}"
    fields = records[-1].split(None, 5)
    if len(fields) < 5:
        return None, f"无法解析磁盘使用记录：{records[-1][:200]}"
    try:
        total = int(fields[1]) * 1024
        used = int(fields[2]) * 1024
        available = int(fields[3]) * 1024
        used_percent = float(fields[4].rstrip("%"))
    except ValueError:
        return None, f"无法解析磁盘数值：{records[-1][:200]}"
    return (
        {
            "path": path,
            "filesystem": fields[0],
            "total_bytes": total,
            "used_bytes": used,
            "available_bytes": available,
            "used_percent": used_percent,
        },
        None,
    )


def collect_disk(alias: str, paths: list[str], timeout: int) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    statuses: list[str] = []
    connection_status = "ok"
    warnings: list[str] = []
    for path in paths:
        remote = run_remote(alias, disk_command(path), timeout)
        status, _reason = classify_remote(remote)
        statuses.append(status)
        if remote.get("transport") != "ok":
            connection_status = "unavailable"
        disk, error = parse_df_output(str(remote.get("stdout", "")), path)
        if disk is None:
            items.append(
                {
                    "path": path,
                    "filesystem": None,
                    "total_bytes": None,
                    "used_bytes": None,
                    "available_bytes": None,
                    "used_percent": None,
                    "error": trim_error(remote.get("error")) or trim_error(remote.get("stderr")) or error,
                }
            )
            if status == "ok":
                statuses[-1] = "failed"
        else:
            items.append(disk)
        if remote.get("truncated"):
            warnings.append(f"磁盘记录已截断：{path}")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "disk",
        "target": target_ref(alias),
        "collected_at": utc_now(),
        "status": aggregate_status(statuses),
        "connection_status": connection_status,
        "disks": items,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "unknown"
    if all(status == "unavailable" for status in statuses):
        return "unavailable"
    if all(status == "ok" for status in statuses):
        return "ok"
    if any(status == "failed" for status in statuses):
        return "failed" if all(status in {"failed", "unavailable"} for status in statuses) else "partial"
    if any(status == "partial" for status in statuses):
        return "partial"
    if any(status == "unknown" for status in statuses):
        return "unknown" if all(status in {"unknown", "unavailable"} for status in statuses) else "partial"
    return "partial"


def parse_check_specs(raw_specs: list[str] | None) -> list[str]:
    if not raw_specs:
        return ["facts", "disk:/"]
    specs: list[str] = []
    for raw in raw_specs:
        specs.extend(item.strip() for item in raw.split(",") if item.strip())
    return specs or ["facts", "disk:/"]


def collect_spec(alias: str, spec: str, args: argparse.Namespace) -> dict[str, Any]:
    if spec == "facts":
        return collect_facts(alias, args.timeout)
    if spec == "ports":
        return collect_ports(alias, args.timeout)
    if spec == "process":
        return collect_process(alias, args.timeout)
    if spec.startswith("process:"):
        return collect_process(alias, args.timeout, pattern=spec.split(":", 1)[1])
    if spec.startswith("service:") and spec.split(":", 1)[1]:
        return collect_service(alias, spec.split(":", 1)[1], args.timeout)
    if spec.startswith("logs:") and spec.split(":", 1)[1]:
        return collect_logs(alias, spec.split(":", 1)[1], args.lines, args.since, args.timeout)
    if spec.startswith("disk:"):
        path = spec.split(":", 1)[1] or "/"
        return collect_disk(alias, [path], args.timeout)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "unknown",
        "target": target_ref(alias),
        "collected_at": utc_now(),
        "status": "unknown",
        "connection_status": "unknown",
        "error": f"不支持的 health 检查项：{spec}",
    }


def health_check_status(result: dict[str, Any]) -> str:
    if result.get("connection_status") == "unavailable":
        return "unknown"
    status = result.get("status")
    return {
        "ok": "pass",
        "partial": "warn",
        "failed": "fail",
        "unknown": "unknown",
        "unavailable": "unknown",
    }.get(str(status), "unknown")


def collect_health(alias: str, args: argparse.Namespace) -> dict[str, Any]:
    started_at = utc_now()
    checks: list[dict[str, Any]] = []
    for spec in parse_check_specs(args.check):
        result = collect_spec(alias, spec, args)
        status = health_check_status(result)
        checks.append(
            {
                "name": spec,
                "status": status,
                "result": result,
            }
        )
    statuses = [item["status"] for item in checks]
    if statuses and all(status == "unknown" for status in statuses):
        overall = "unknown"
    elif "fail" in statuses:
        overall = "fail"
    elif "warn" in statuses or "unknown" in statuses:
        overall = "warn"
    else:
        overall = "pass"
    warnings = [
        f"{item['name']}: {item['result'].get('error')}"
        for item in checks
        if item["result"].get("error")
    ]
    connection_status = "unavailable" if any(
        item["result"].get("connection_status") == "unavailable" for item in checks
    ) else "ok"
    result = {
        "schema_version": SCHEMA_VERSION,
        "kind": "health",
        "target": target_ref(alias),
        "started_at": started_at,
        "finished_at": utc_now(),
        "status": overall,
        "connection_status": connection_status,
        "checks": checks,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def csv_text(metadata: str, columns: list[str], rows: list[list[Any]]) -> str:
    output = io.StringIO()
    output.write(f"# {metadata}\n")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue()


def render_human(result: dict[str, Any]) -> None:
    kind = str(result.get("kind", ""))
    alias = result.get("target", {}).get("alias", "")
    print(f"# kind={kind} target={alias} status={result.get('status', 'unknown')}")
    if result.get("error"):
        print(f"error: {result['error']}")
    if kind == "facts":
        facts = result.get("facts", {})
        load = facts.get("load", {})
        for key, value in (
            ("hostname", facts.get("hostname", "")),
            ("os", facts.get("os", "")),
            ("kernel", facts.get("kernel", "")),
            ("arch", facts.get("arch", "")),
            ("uptime_seconds", facts.get("uptime_seconds")),
            ("load_1m", load.get("1m")),
            ("load_5m", load.get("5m")),
            ("load_15m", load.get("15m")),
        ):
            print(f"{key}={value if value is not None else ''}")
    elif kind == "service":
        service = result.get("service", {})
        print(
            " ".join(
                f"{key}={service.get(key, '') if service.get(key) is not None else ''}"
                for key in ("name", "manager", "active", "substate", "pid", "enabled")
            )
        )
        if service.get("error"):
            print(f"error: {service['error']}")
    elif kind == "service_inspect":
        service = result.get("service", {})
        print(
            " ".join(
                f"{key}={service.get(key, '') if service.get(key) is not None else ''}"
                for key in ("name", "manager", "load_state", "active", "substate", "pid", "enabled")
            )
        )
        print(f"unit_file={result.get('unit_file', {}).get('path') or ''}")
        for drop_in in result.get("drop_ins", []):
            print(f"drop_in={drop_in.get('path', '')}")
        print(f"user={result.get('user', '')}")
        print(f"working_directory={result.get('working_directory') or ''}")
        print(f"exec_start={result.get('exec_start', {}).get('main', '')}")
        process = result.get("process", {})
        print(f"process_pid={process.get('pid') or ''} process_command={process.get('command', '')}")
        print(f"ports_scope={result.get('ports_scope', '')} ports_status={result.get('ports_status', '')}")
        rows = [
            [item.get(key, "") if item.get(key) is not None else "" for key in ("protocol", "local_address", "port", "pid", "process")]
            for item in result.get("ports", [])
        ]
        if rows:
            sys.stdout.write(csv_text(f"ports={len(rows)}", ["protocol", "local_address", "port", "pid", "process"], rows))
        for kind_name in ("release", "config", "data", "log", "other"):
            for path in result.get("paths", {}).get(kind_name, []):
                print(f"path_{kind_name}={path}")
    elif kind == "process":
        rows = [
            [item.get(key, "") for key in ("pid", "ppid", "user", "state", "cpu", "memory", "command")]
            for item in result.get("processes", [])
        ]
        sys.stdout.write(
            csv_text(
                f"processes={len(rows)}",
                ["pid", "ppid", "user", "state", "cpu", "memory", "command"],
                rows,
            )
        )
    elif kind == "logs":
        log = result.get("log", {})
        print(f"source={log.get('source', '')} truncated={'true' if log.get('truncated') else 'false'}")
        for line in log.get("lines", []):
            print(line)
    elif kind == "ports":
        rows = [
            [item.get(key, "") if item.get(key) is not None else "" for key in ("protocol", "local_address", "port", "pid", "process")]
            for item in result.get("ports", [])
        ]
        sys.stdout.write(csv_text(f"ports={len(rows)}", ["protocol", "local_address", "port", "pid", "process"], rows))
    elif kind == "disk":
        rows = [
            [item.get(key, "") if item.get(key) is not None else "" for key in ("path", "filesystem", "total_bytes", "used_bytes", "available_bytes", "used_percent", "error")]
            for item in result.get("disks", [])
        ]
        sys.stdout.write(
            csv_text(
                f"disks={len(rows)}",
                ["path", "filesystem", "total_bytes", "used_bytes", "available_bytes", "used_percent", "error"],
                rows,
            )
        )
    elif kind == "health":
        for check in result.get("checks", []):
            print(f"check={check.get('name', '')} status={check.get('status', 'unknown')}")
    for warning in result.get("warnings", []):
        print(f"warning: {warning}")


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--json", action="store_true", help="返回结构化 JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通过 sshx 采集主机只读事实和健康状态。")
    subparsers = parser.add_subparsers(dest="command", required=True)

    facts = subparsers.add_parser("facts", help="采集主机基础事实")
    facts.add_argument("alias")
    add_common_args(facts)
    facts.set_defaults(func=lambda args: collect_facts(args.alias, args.timeout))

    service = subparsers.add_parser("service", help="查看服务状态")
    service.add_argument("alias")
    service_subparsers = service.add_subparsers(dest="service_command", required=True)
    service_status = service_subparsers.add_parser("status")
    service_status.add_argument("unit")
    add_common_args(service_status)
    service_status.set_defaults(func=lambda args: collect_service(args.alias, args.unit, args.timeout))
    service_inspect = service_subparsers.add_parser("inspect", help="采集 systemd 服务接管所需事实")
    service_inspect.add_argument("unit")
    add_common_args(service_inspect)
    service_inspect.set_defaults(func=lambda args: collect_service_inspect(args.alias, args.unit, args.timeout))

    process = subparsers.add_parser("process", help="查看进程")
    process.add_argument("alias")
    process_subparsers = process.add_subparsers(dest="process_command", required=True)
    process_list = process_subparsers.add_parser("list")
    process_list.add_argument("--pattern")
    add_common_args(process_list)
    process_list.set_defaults(func=lambda args: collect_process(args.alias, args.timeout, args.pattern))
    process_show = process_subparsers.add_parser("show")
    process_show.add_argument("pid")
    add_common_args(process_show)
    process_show.set_defaults(func=lambda args: collect_process(args.alias, args.timeout, pid=args.pid))

    logs = subparsers.add_parser("logs", help="读取服务或文件日志尾部")
    logs.add_argument("alias")
    logs.add_argument("source")
    logs.add_argument("--lines", type=int, default=100)
    logs.add_argument("--since")
    add_common_args(logs)
    logs.set_defaults(func=lambda args: collect_logs(args.alias, args.source, args.lines, args.since, args.timeout))

    ports = subparsers.add_parser("ports", help="查看监听端口")
    ports.add_argument("alias")
    add_common_args(ports)
    ports.set_defaults(func=lambda args: collect_ports(args.alias, args.timeout))

    disk = subparsers.add_parser("disk", help="查看文件系统使用情况")
    disk.add_argument("alias")
    disk.add_argument("paths", nargs="*", default=["/"])
    add_common_args(disk)
    disk.set_defaults(func=lambda args: collect_disk(args.alias, args.paths or ["/"], args.timeout))

    health = subparsers.add_parser("health", help="组合只读健康检查")
    health.add_argument("alias")
    health.add_argument("--check", action="append", help="检查项，可重复或用逗号分隔")
    health.add_argument("--lines", type=int, default=100)
    health.add_argument("--since")
    add_common_args(health)
    health.set_defaults(func=lambda args: collect_health(args.alias, args))
    return parser


def command_exit_code(result: dict[str, Any]) -> int:
    if result.get("kind") == "health":
        return 0 if result.get("status") in {"pass", "warn"} else 1
    return 0 if result.get("status") in {"ok", "partial"} else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
        if args.json:
            print_json(result)
        else:
            render_human(result)
        return command_exit_code(result)
    except Exception as exc:
        error = {
            "schema_version": SCHEMA_VERSION,
            "kind": getattr(args, "command", "unknown"),
            "status": "failed",
            "connection_status": "unknown",
            "error": str(exc),
        }
        if getattr(args, "json", False):
            print_json(error)
        else:
            print(f"error: {error['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
