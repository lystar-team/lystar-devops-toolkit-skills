#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
import posixpath
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import paramiko

import result_store
from config_store import load_toml, lock_files, write_toml


CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
CONFIG_FILE = CONFIG_HOME / "agent-ops" / "ssh.toml"
BASE_DIR = STATE_HOME / "agent-ops" / "ssh"
RUN_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", BASE_DIR / "run")) / "agent-ops-ssh"
SCRIPT = Path(__file__).resolve()
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 120
COMMANDS = {
    "open", "status", "close", "forget", "reap", "exec", "run", "job",
    "wait", "put", "get", "ls", "cat", "last", "daemon",
}
CACHE_ACTIONS = {"exec", "run", "job", "wait", "put", "get", "ls", "cat"}


def ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(RUN_DIR, 0o700)


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def write_stream(stream: Any, text: str) -> None:
    if text:
        stream.write(text)
        stream.flush()


def csv_text(metadata: str, columns: list[str], rows: list[list[Any]]) -> str:
    output = io.StringIO()
    output.write(f"# {metadata}\n")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue()


def plain_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return shlex.quote(str(value))


def key_value_text(values: list[tuple[str, Any]]) -> str:
    return " ".join(f"{key}={plain_value(value)}" for key, value in values if value not in (None, "")) + "\n"


def render_error(result: dict[str, Any], as_json: bool = False) -> None:
    if as_json:
        print_json(result)
    else:
        write_stream(sys.stderr, f"error: {result.get('error', 'unknown SSH error')}\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_config() -> dict[str, Any]:
    config = load_toml(CONFIG_FILE)
    config.setdefault("profiles", {})
    config.setdefault("aliases", {})
    return config


def save_config(config: dict[str, Any]) -> None:
    write_toml(CONFIG_FILE, config)


def profile_jump_ids(profile: dict[str, Any]) -> tuple[str, ...]:
    raw_jumps = profile.get("jump_profiles", [])
    if raw_jumps is None:
        return ()
    if isinstance(raw_jumps, str):
        raw_jumps = [raw_jumps]
    if not isinstance(raw_jumps, list) or any(not isinstance(item, str) or not item for item in raw_jumps):
        raise ValueError("jump_profiles must be a list of non-empty profile IDs")
    return tuple(raw_jumps)


def profile_identity(profile: dict[str, Any]) -> tuple[str, int, str, tuple[str, ...]]:
    return (
        str(profile["host"]),
        int(profile.get("port", DEFAULT_PORT)),
        str(profile["user"]),
        profile_jump_ids(profile),
    )


def make_profile_id(host: str, port: int, user: str, jump_ids: tuple[str, ...] = ()) -> str:
    identity = f"{host}\0{port}\0{user}"
    if jump_ids:
        identity += "\0" + "\0".join(jump_ids)
    digest = hashlib.sha256(identity.encode()).hexdigest()[:12]
    return f"ssh_{digest}"


def find_profile(
    config: dict[str, Any],
    host: str,
    port: int,
    user: str,
    jump_ids: tuple[str, ...] = (),
) -> str | None:
    identity = (host, port, user, jump_ids)
    for profile_id, profile in config["profiles"].items():
        if profile_identity(profile) == identity:
            return profile_id
    return None


def resolve_jump_ids(config: dict[str, Any], aliases: list[str]) -> tuple[str, ...]:
    jump_ids: list[str] = []
    for alias in aliases:
        profile_id = config["aliases"].get(alias)
        if not profile_id or profile_id not in config["profiles"]:
            raise ValueError(f"unknown SSH jump alias: {alias}; run sshx open first")
        jump_ids.append(profile_id)
    return tuple(jump_ids)


def resolve_profile_chain(config: dict[str, Any], profile_id: str) -> list[str]:
    chain: list[str] = []
    active: list[str] = []

    def visit(current_id: str) -> None:
        if current_id in active:
            cycle = " -> ".join([*active, current_id])
            raise ValueError(f"SSH jump cycle detected: {cycle}")
        if current_id in chain:
            return
        profile = config["profiles"].get(current_id)
        if not profile:
            raise ValueError(f"SSH profile no longer exists: {current_id}")
        active.append(current_id)
        for jump_id in profile_jump_ids(profile):
            if jump_id not in config["profiles"]:
                raise ValueError(f"SSH jump profile no longer exists: {jump_id}")
            visit(jump_id)
        active.pop()
        chain.append(current_id)

    visit(profile_id)
    return chain


def profile_aliases_or_id(config: dict[str, Any], profile_id: str) -> str:
    aliases = aliases_for(config, profile_id)
    return aliases[0] if aliases else profile_id


def profile_jump_aliases(config: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    return [profile_aliases_or_id(config, jump_id) for jump_id in profile_jump_ids(profile)]


def resolve_profile(alias: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    config = load_config()
    profile_id = config["aliases"].get(alias)
    if not profile_id or profile_id not in config["profiles"]:
        raise ValueError(f"unknown SSH alias: {alias}; run sshx open first")
    return profile_id, config["profiles"][profile_id], config


def aliases_for(config: dict[str, Any], profile_id: str) -> list[str]:
    return sorted(alias for alias, target in config["aliases"].items() if target == profile_id)


def socket_path(profile_id: str) -> Path:
    return RUN_DIR / f"{profile_id}.sock"


def pid_path(profile_id: str) -> Path:
    return RUN_DIR / f"{profile_id}.pid"


def ready_path(profile_id: str) -> Path:
    return RUN_DIR / f"{profile_id}.ready.json"


def log_path(profile_id: str) -> Path:
    return RUN_DIR / f"{profile_id}.log"


def exit_path(profile_id: str) -> Path:
    return BASE_DIR / f"{profile_id}.last-exit.json"


def pid_alive(profile_id: str) -> bool:
    try:
        pid = int(pid_path(profile_id).read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True


def daemon_running(profile_id: str) -> bool:
    return pid_alive(profile_id)


def reap_stale() -> list[str]:
    ensure_dirs()
    removed: list[str] = []
    profile_ids = {path.stem for path in RUN_DIR.glob("*.sock")}
    profile_ids.update(path.stem for path in RUN_DIR.glob("*.pid"))
    for profile_id in profile_ids:
        if pid_alive(profile_id):
            continue
        for path in (socket_path(profile_id), pid_path(profile_id), ready_path(profile_id)):
            try:
                path.unlink()
                removed.append(str(path))
            except FileNotFoundError:
                pass
    return removed


def request(profile_id: str, payload: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    path = socket_path(profile_id)
    if not path.exists():
        return {"error": "SSH connection is not open"}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall(json.dumps(payload).encode())
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode())


def parse_target(target: str, user: str | None, port: int | None) -> tuple[str, str | None, int]:
    if "@" in target:
        target_user, host = target.split("@", 1)
        user = user or target_user
    else:
        host = target
    if ":" in host and host.count(":") == 1:
        host, target_port = host.rsplit(":", 1)
        if target_port.isdigit():
            port = port or int(target_port)
    return host, user, port or DEFAULT_PORT


def connect_client(
    profile: dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
    sock: Any | None = None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
    except OSError:
        pass
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    password = str(profile.get("password", ""))
    key = str(profile.get("key", ""))
    discover_keys = not password and not key
    client.connect(
        hostname=str(profile["host"]),
        port=int(profile.get("port", DEFAULT_PORT)),
        username=str(profile["user"]),
        password=password or None,
        key_filename=key or None,
        timeout=timeout,
        banner_timeout=timeout,
        auth_timeout=timeout,
        sock=sock,
        look_for_keys=discover_keys,
        allow_agent=discover_keys,
    )
    transport = client.get_transport()
    if transport:
        transport.set_keepalive(30)
    return client


def close_connection_chain(
    clients: list[paramiko.SSHClient],
    proxy_channels: list[paramiko.Channel],
) -> None:
    for client in reversed(clients):
        try:
            client.close()
        except (OSError, EOFError, paramiko.SSHException):
            pass
    for channel in reversed(proxy_channels):
        try:
            channel.close()
        except (OSError, EOFError, paramiko.SSHException):
            pass


def connect_profile_chain(
    config: dict[str, Any],
    profile_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[paramiko.SSHClient, list[paramiko.SSHClient], list[paramiko.Channel]]:
    profile_ids = resolve_profile_chain(config, profile_id)
    clients: list[paramiko.SSHClient] = []
    proxy_channels: list[paramiko.Channel] = []
    try:
        for current_id in profile_ids:
            profile = config["profiles"][current_id]
            sock: Any | None = None
            if clients:
                previous_transport = clients[-1].get_transport()
                if not previous_transport or not previous_transport.is_active():
                    raise RuntimeError(f"SSH jump transport is unavailable before {current_id}")
                sock = previous_transport.open_channel(
                    "direct-tcpip",
                    (str(profile["host"]), int(profile.get("port", DEFAULT_PORT))),
                    ("127.0.0.1", 0),
                    timeout=timeout,
                )
                proxy_channels.append(sock)
            try:
                client = connect_client(profile, timeout=timeout, sock=sock)
            except Exception as exc:
                label = profile_aliases_or_id(config, current_id)
                raise RuntimeError(
                    f"SSH connection failed at {label} "
                    f"({profile['user']}@{profile['host']}:{profile.get('port', DEFAULT_PORT)}): {exc}"
                ) from exc
            clients.append(client)
        return clients[-1], clients, proxy_channels
    except Exception:
        close_connection_chain(clients, proxy_channels)
        raise


def transport_connected(client: paramiko.SSHClient | None) -> bool:
    if client is None:
        return False
    transport = client.get_transport()
    return bool(transport and transport.is_active() and transport.is_authenticated())


class BoundedOutput:
    def __init__(self, max_bytes: int):
        if max_bytes < 1:
            raise ValueError("max bytes must be positive")
        self.max_bytes = max_bytes
        self.head_limit = max_bytes // 2
        self.tail_limit = max_bytes - self.head_limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def add(self, chunk: bytes) -> None:
        self.total += len(chunk)
        remaining = chunk
        if len(self.head) < self.head_limit:
            size = min(self.head_limit - len(self.head), len(remaining))
            self.head.extend(remaining[:size])
            remaining = remaining[size:]
        if remaining and self.tail_limit:
            self.tail.extend(remaining)
            if len(self.tail) > self.tail_limit:
                del self.tail[:-self.tail_limit]

    def text(self) -> str:
        if self.total <= self.max_bytes:
            return bytes(self.head + self.tail).decode(errors="replace")
        omitted = self.total - len(self.head) - len(self.tail)
        head = self.head.decode(errors="replace")
        tail = self.tail.decode(errors="replace")
        return f"{head}\n... omitted {omitted} bytes ...\n{tail}"


def exec_command(client: paramiko.SSHClient, command: str, timeout: int, max_bytes: int) -> dict[str, Any]:
    transport = client.get_transport()
    if not transport:
        raise RuntimeError("SSH transport is unavailable")
    channel = transport.open_session(timeout=timeout)
    channel.exec_command(command)
    stdout_data = BoundedOutput(max_bytes)
    stderr_data = BoundedOutput(max_bytes)
    deadline = time.monotonic() + timeout

    while True:
        if channel.recv_ready():
            stdout_data.add(channel.recv(64 * 1024))
        if channel.recv_stderr_ready():
            stderr_data.add(channel.recv_stderr(64 * 1024))
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError(f"remote command timed out after {timeout}s")
        time.sleep(0.01)

    result: dict[str, Any] = {"exit_code": channel.recv_exit_status()}
    stdout_text = stdout_data.text()
    stderr_text = stderr_data.text()
    if stdout_text:
        result["stdout"] = stdout_text
    if stderr_text:
        result["stderr"] = stderr_text
    return result


def sha256_local(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sftp_mkdirs(sftp: paramiko.SFTPClient, remote_dir: str) -> None:
    if remote_dir in ("", "."):
        return
    current = "/" if remote_dir.startswith("/") else ""
    for part in [item for item in remote_dir.split("/") if item]:
        current = posixpath.join(current, part) if current else part
        try:
            sftp.stat(current)
        except OSError:
            sftp.mkdir(current)


def remote_is_dir(sftp: paramiko.SFTPClient, remote: str) -> bool:
    try:
        return stat.S_ISDIR(sftp.stat(remote).st_mode)
    except OSError:
        return False


def open_sftp_session(client: paramiko.SSHClient, timeout: int) -> paramiko.SFTPClient:
    sftp = client.open_sftp()
    sftp.get_channel().settimeout(timeout)
    return sftp


def sftp_put(
    client: paramiko.SSHClient,
    local_text: str,
    remote: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    local = Path(local_text).expanduser().resolve()
    if not local.exists():
        raise FileNotFoundError(f"local path not found: {local}")
    files = 0
    bytes_count = 0
    with open_sftp_session(client, timeout) as sftp:
        if local.is_dir():
            for path in local.rglob("*"):
                if not path.is_file():
                    continue
                remote_file = posixpath.join(remote, path.relative_to(local).as_posix())
                sftp_mkdirs(sftp, posixpath.dirname(remote_file))
                sftp.put(str(path), remote_file)
                files += 1
                bytes_count += path.stat().st_size
        else:
            remote_file = posixpath.join(remote, local.name) if remote.endswith("/") or remote_is_dir(sftp, remote) else remote
            sftp_mkdirs(sftp, posixpath.dirname(remote_file))
            sftp.put(str(local), remote_file)
            files = 1
            bytes_count = local.stat().st_size
    result: dict[str, Any] = {"files": files, "bytes": bytes_count, "remote": remote}
    if local.is_file():
        result["sha256"] = sha256_local(local)
    return result


def download_file(sftp: paramiko.SFTPClient, remote: str, local: Path) -> tuple[int, int]:
    local.parent.mkdir(parents=True, exist_ok=True)
    size = sftp.stat(remote).st_size
    sftp.get(remote, str(local))
    return 1, size


def sftp_get(
    client: paramiko.SSHClient,
    remote: str,
    local_text: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    local = Path(local_text).expanduser().resolve()
    files = 0
    bytes_count = 0
    with open_sftp_session(client, timeout) as sftp:
        if remote_is_dir(sftp, remote):
            local.mkdir(parents=True, exist_ok=True)
            stack = [(remote, local)]
            while stack:
                current_remote, current_local = stack.pop()
                current_local.mkdir(parents=True, exist_ok=True)
                for item in sftp.listdir_attr(current_remote):
                    child_remote = posixpath.join(current_remote, item.filename)
                    child_local = current_local / item.filename
                    if stat.S_ISDIR(item.st_mode):
                        stack.append((child_remote, child_local))
                    else:
                        file_count, byte_count = download_file(sftp, child_remote, child_local)
                        files += file_count
                        bytes_count += byte_count
        else:
            target = local / posixpath.basename(remote) if local.exists() and local.is_dir() else local
            files, bytes_count = download_file(sftp, remote, target)
            local = target
    result: dict[str, Any] = {"files": files, "bytes": bytes_count, "local": str(local)}
    if files == 1 and local.is_file():
        result["sha256"] = sha256_local(local)
    return result


def sftp_ls(
    client: paramiko.SSHClient,
    remote: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    with open_sftp_session(client, timeout) as sftp:
        entries = [
            {
                "name": item.filename,
                "type": "dir" if stat.S_ISDIR(item.st_mode) else "file",
                "bytes": item.st_size,
                "mode": oct(item.st_mode & 0o777),
            }
            for item in sftp.listdir_attr(remote)
        ]
    return {"entries": entries}


def sftp_cat(
    client: paramiko.SSHClient,
    remote: str,
    max_bytes: int,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    with open_sftp_session(client, timeout) as sftp:
        with sftp.open(remote, "rb") as handle:
            data = handle.read(max_bytes + 1)
    content = data[:max_bytes].decode(errors="replace")
    if len(data) > max_bytes:
        content += f"\n... trimmed to {max_bytes} bytes"
    return {"content": content}


def remote_home(client: paramiko.SSHClient, timeout: int = DEFAULT_TIMEOUT) -> str:
    with open_sftp_session(client, timeout) as sftp:
        return sftp.normalize(".")


def start_remote_job(
    client: paramiko.SSHClient,
    command: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    job_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = posixpath.join(remote_home(client, timeout), ".agent-ops", "jobs", job_id)
    with open_sftp_session(client, timeout) as sftp:
        sftp_mkdirs(sftp, job_dir)
        command_path = posixpath.join(job_dir, "command.sh")
        launcher_path = posixpath.join(job_dir, "launcher.sh")
        with sftp.open(command_path, "w") as handle:
            handle.write(command + "\n")
        with sftp.open(launcher_path, "w") as handle:
            handle.write(
                """#!/bin/sh
cd "$(dirname "$0")" || exit 127
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"state":"running","pid":%s,"started_at":"%s","updated_at":"%s"}\n' "$$" "$started" "$started" > status.json
/bin/sh command.sh > stdout.log 2> stderr.log
code=$?
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"state":"finished","exit_code":%s,"pid":%s,"started_at":"%s","updated_at":"%s"}\n' "$code" "$$" "$started" "$ended" > status.json
exit "$code"
"""
            )
        sftp.chmod(command_path, 0o700)
        sftp.chmod(launcher_path, 0o700)
    launcher = shlex.quote(posixpath.join(job_dir, "launcher.sh"))
    launch = exec_command(
        client,
        f"nohup setsid /bin/sh {launcher} >/dev/null 2>&1 < /dev/null & echo $!",
        timeout,
        2000,
    )
    return {"job_id": job_id, "remote_dir": job_dir, "pid": launch.get("stdout", "").strip()}


def remote_job_status(
    client: paramiko.SSHClient,
    job_id: str,
    tail: int,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    job_dir = posixpath.join(remote_home(client, timeout), ".agent-ops", "jobs", job_id)
    quoted_dir = shlex.quote(job_dir)
    command = (
        f"cd {quoted_dir} && printf 'STATUS_JSON\\n'; cat status.json 2>/dev/null || true; "
        f"printf '\\nSTDOUT_TAIL\\n'; tail -n {int(tail)} stdout.log 2>/dev/null || true; "
        f"printf '\\nSTDERR_TAIL\\n'; tail -n {int(tail)} stderr.log 2>/dev/null || true"
    )
    reply = exec_command(client, command, timeout, 64_000)
    output = reply.get("stdout", "")
    status_text = ""
    stdout_tail = ""
    stderr_tail = ""
    if "STDOUT_TAIL\n" in output:
        before_stdout, stdout_tail = output.split("STDOUT_TAIL\n", 1)
        status_text = before_stdout.replace("STATUS_JSON\n", "", 1).strip()
    if "\nSTDERR_TAIL\n" in stdout_tail:
        stdout_tail, stderr_tail = stdout_tail.split("\nSTDERR_TAIL\n", 1)
    try:
        status_payload = json.loads(status_text) if status_text else {"state": "unknown"}
    except json.JSONDecodeError:
        status_payload = {"state": "unknown", "raw": status_text}
    result: dict[str, Any] = {"job_id": job_id, "status": status_payload, "remote_dir": job_dir}
    if stdout_tail:
        result["stdout"] = stdout_tail
    if stderr_tail:
        result["stderr"] = stderr_tail
    return result


class Daemon:
    def __init__(self, profile_id: str, timeout: int = DEFAULT_TIMEOUT):
        self.profile_id = profile_id
        self.connection_timeout = timeout
        self.client: paramiko.SSHClient | None = None
        self.clients: list[paramiko.SSHClient] = []
        self.proxy_channels: list[paramiko.Channel] = []
        self.started_at = time.time()
        self.last_used = time.time()
        self.closed = threading.Event()
        self.connect_lock = threading.Lock()
        self.sock_path = socket_path(profile_id)
        self.ensure_connected()

    def profile(self) -> dict[str, Any]:
        config = load_config()
        profile = config.get("profiles", {}).get(self.profile_id)
        if not profile:
            raise ValueError(f"SSH profile no longer exists: {self.profile_id}")
        return profile

    def chain_connected(self) -> bool:
        return bool(self.clients) and all(transport_connected(client) for client in self.clients)

    def close_connection(self) -> None:
        close_connection_chain(self.clients, self.proxy_channels)
        self.client = None
        self.clients = []
        self.proxy_channels = []

    def ensure_connected(self) -> paramiko.SSHClient:
        if self.chain_connected():
            assert self.client is not None
            return self.client
        with self.connect_lock:
            if self.chain_connected():
                assert self.client is not None
                return self.client
            self.close_connection()
            config = load_config()
            self.client, self.clients, self.proxy_channels = connect_profile_chain(
                config,
                self.profile_id,
                timeout=self.connection_timeout,
            )
            return self.client

    def status(self) -> dict[str, Any]:
        config = load_config()
        profile = config.get("profiles", {}).get(self.profile_id, {})
        return {
            "profile": self.profile_id,
            "aliases": aliases_for(config, self.profile_id),
            "host": profile.get("host", ""),
            "port": profile.get("port", DEFAULT_PORT),
            "user": profile.get("user", ""),
            "jumps": profile_jump_aliases(config, profile),
            "connected": transport_connected(self.client),
            "started_at": dt.datetime.fromtimestamp(self.started_at, dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "idle_seconds": int(time.time() - self.last_used),
        }

    def perform(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if action == "status":
            return self.status()
        if action == "close":
            self.closed.set()
            return {"closed": True, "profile": self.profile_id}

        client = self.ensure_connected()
        self.last_used = time.time()
        operation_timeout = int(payload.get("timeout", DEFAULT_TIMEOUT))
        if action == "exec":
            return exec_command(
                client,
                payload["command"],
                operation_timeout,
                int(payload.get("max_bytes", 8000)),
            )
        if action == "run":
            return start_remote_job(client, payload["command"], operation_timeout)
        if action == "job":
            return remote_job_status(
                client,
                payload["job_id"],
                int(payload.get("tail", 120)),
                operation_timeout,
            )
        if action == "put":
            return sftp_put(client, payload["local"], payload["remote"], operation_timeout)
        if action == "get":
            return sftp_get(client, payload["remote"], payload["local"], operation_timeout)
        if action == "ls":
            return sftp_ls(client, payload["remote"], operation_timeout)
        if action == "cat":
            return sftp_cat(client, payload["remote"], int(payload.get("max_bytes", 4000)), operation_timeout)
        raise ValueError(f"unknown action: {action}")

    def handle_connection(self, conn: socket.socket) -> None:
        with conn:
            try:
                chunks: list[bytes] = []
                while True:
                    chunk = conn.recv(1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                reply = self.perform(json.loads(b"".join(chunks).decode()))
            except Exception as exc:
                reply = {"error": str(exc)}
            try:
                conn.sendall(json.dumps(reply, ensure_ascii=False).encode())
            except OSError:
                pass

    def serve(self, ready_file: str) -> int:
        signal.signal(signal.SIGTERM, lambda *_: self.closed.set())
        signal.signal(signal.SIGINT, lambda *_: self.closed.set())
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        reason = "closed"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(self.sock_path))
                os.chmod(self.sock_path, 0o600)
                server.listen(16)
                server.settimeout(1)
                pid_path(self.profile_id).write_text(str(os.getpid()), encoding="utf-8")
                write_json(Path(ready_file), {"ok": True, "profile": self.profile_id})
                while not self.closed.is_set():
                    try:
                        conn, _ = server.accept()
                    except socket.timeout:
                        continue
                    threading.Thread(target=self.handle_connection, args=(conn,), daemon=True).start()
        except Exception as exc:
            reason = str(exc)
            raise
        finally:
            self.close_connection()
            write_json(exit_path(self.profile_id), {"at": utc_now(), "reason": reason})
            for path in (self.sock_path, pid_path(self.profile_id), ready_path(self.profile_id)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        return 0


def run_daemon(args: argparse.Namespace) -> int:
    ensure_dirs()
    try:
        return Daemon(args.profile_id, args.timeout).serve(args.ready_file)
    except Exception as exc:
        write_json(Path(args.ready_file), {"ok": False, "error": str(exc)})
        write_json(exit_path(args.profile_id), {"at": utc_now(), "reason": str(exc)})
        return 1


def daemon_ready_timeout(profile_id: str, timeout: int) -> int:
    try:
        hop_count = len(resolve_profile_chain(load_config(), profile_id))
    except ValueError:
        hop_count = 1
    return max(timeout, timeout * hop_count)


def start_daemon(profile_id: str, timeout: int = DEFAULT_TIMEOUT) -> dict[str, Any]:
    ensure_dirs()
    reap_stale()
    if daemon_running(profile_id):
        return {"started": False, "profile": profile_id}
    ready = ready_path(profile_id)
    try:
        ready.unlink()
    except FileNotFoundError:
        pass
    command = [
        sys.executable, str(SCRIPT), "daemon", "--profile-id", profile_id,
        "--ready-file", str(ready),
        "--timeout", str(timeout),
    ]
    log = log_path(profile_id).open("ab")
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    log.close()
    deadline = time.time() + daemon_ready_timeout(profile_id, timeout)
    ready_payload: dict[str, Any] | None = None
    while time.time() < deadline:
        if ready.exists():
            ready_payload = read_json(ready, {"ok": False, "error": "invalid ready file"})
            break
        if process.poll() is not None:
            break
        time.sleep(0.1)
    if not ready_payload:
        ready_payload = {"ok": False, "error": "SSH daemon did not become ready"}
    if not ready_payload.get("ok"):
        if process.poll() is None:
            process.terminate()
        return {"error": ready_payload.get("error", "failed to open SSH connection")}
    return {"started": True, "profile": profile_id}


def ensure_daemon(profile_id: str, timeout: int = DEFAULT_TIMEOUT) -> None:
    result = start_daemon(profile_id, timeout)
    if result.get("error"):
        raise RuntimeError(str(result["error"]))


def run_open(args: argparse.Namespace) -> int:
    host, user, port = parse_target(args.target, args.user, args.port)
    if not user:
        raise ValueError("missing user; use user@host or --user")
    with lock_files(CONFIG_FILE):
        config = load_config()
        jump_ids = resolve_jump_ids(config, args.jump or [])
        profile_id = find_profile(config, host, port, user, jump_ids) or make_profile_id(
            host, port, user, jump_ids
        )
        old_profile = config["profiles"].get(profile_id, {})
        password = args.password_option if args.password_option is not None else args.password
        profile = {
            "host": host,
            "port": port,
            "user": user,
            "password": password if password is not None else old_profile.get("password", ""),
            "key": args.key if args.key is not None else old_profile.get("key", ""),
            "created_at": old_profile.get("created_at", utc_now()),
            "updated_at": utc_now(),
        }
        if jump_ids:
            profile["jump_profiles"] = list(jump_ids)
        config["profiles"][profile_id] = profile
        resolve_profile_chain(config, profile_id)
        config["aliases"][args.alias] = profile_id
        save_config(config)
        profile_aliases = aliases_for(config, profile_id)
    result = start_daemon(profile_id, args.timeout)
    if result.get("error"):
        print_json(result)
        return 1
    print_json({
        "profile": profile_id,
        "aliases": profile_aliases,
        "host": host,
        "port": port,
        "user": user,
        "jumps": profile_jump_aliases(config, profile),
        "connected": True,
        "reused": not result.get("started", False),
    })
    return 0


def status_row(profile_id: str, profile: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    base = {
        "profile": profile_id,
        "aliases": aliases_for(config, profile_id),
        "host": profile.get("host", ""),
        "port": profile.get("port", DEFAULT_PORT),
        "user": profile.get("user", ""),
        "jumps": profile_jump_aliases(config, profile),
    }
    if not daemon_running(profile_id):
        base["connected"] = False
        last_exit = read_json(exit_path(profile_id), {})
        if last_exit:
            base["last_exit"] = last_exit
        return base
    if not socket_path(profile_id).exists():
        base["connected"] = False
        base["error"] = "SSH daemon is running without a socket"
        return base
    try:
        return request(profile_id, {"action": "status"}, timeout=2)
    except (OSError, TimeoutError, socket.timeout):
        base["connected"] = True
        base["busy"] = True
        return base


def render_status(rows: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print_json({"connections": rows})
        return
    table_rows = []
    for row in rows:
        note = row.get("error", "")
        if not note and row.get("busy"):
            note = "busy"
        if not note and isinstance(row.get("last_exit"), dict):
            note = row["last_exit"].get("reason", "")
        table_rows.append([
            "|".join(row.get("aliases", [])),
            row.get("user", ""),
            row.get("host", ""),
            row.get("port", ""),
            "true" if row.get("connected") else "false",
            note,
        ])
    write_stream(
        sys.stdout,
        csv_text(
            f"connections={len(rows)}",
            ["aliases", "user", "host", "port", "connected", "note"],
            table_rows,
        ),
    )


def run_status(args: argparse.Namespace) -> int:
    config = load_config()
    if args.alias:
        profile_id, profile, _ = resolve_profile(args.alias)
        rows = [status_row(profile_id, profile, config)]
    else:
        rows = [status_row(profile_id, profile, config) for profile_id, profile in sorted(config["profiles"].items())]
    render_status(rows, args.json)
    return 0


def close_profile(profile_id: str) -> dict[str, Any]:
    if not daemon_running(profile_id):
        return {"profile": profile_id, "closed": False}
    try:
        result = request(profile_id, {"action": "close"}, timeout=2)
        deadline = time.monotonic() + 3
        while pid_alive(profile_id) and time.monotonic() < deadline:
            time.sleep(0.05)
        return result
    except Exception as exc:
        return {"profile": profile_id, "error": str(exc)}


def run_close(args: argparse.Namespace) -> int:
    config = load_config()
    if args.all:
        profile_ids = sorted(
            config["profiles"],
            key=lambda profile_id: len(resolve_profile_chain(config, profile_id)),
            reverse=True,
        )
    elif args.alias:
        profile_id, _profile, _config = resolve_profile(args.alias)
        profile_ids = [profile_id]
    else:
        raise ValueError("close needs an alias or --all")
    print_json({"connections": [close_profile(profile_id) for profile_id in profile_ids]})
    return 0


def run_forget(args: argparse.Namespace) -> int:
    with lock_files(CONFIG_FILE):
        profile_id, _profile, config = resolve_profile(args.alias)
        config["aliases"].pop(args.alias, None)
        remaining = aliases_for(config, profile_id)
        if not remaining:
            dependents = [
                dependent_id
                for dependent_id, profile in config["profiles"].items()
                if dependent_id != profile_id and profile_id in profile_jump_ids(profile)
            ]
            if dependents:
                labels = ", ".join(profile_aliases_or_id(config, item) for item in dependents)
                raise ValueError(f"cannot forget SSH profile {profile_id}; used by jump profiles: {labels}")
            close_profile(profile_id)
            config["profiles"].pop(profile_id, None)
        save_config(config)
    print_json({"forgotten": args.alias, "profile": profile_id, "remaining_aliases": remaining})
    return 0


def command_text(parts: list[str]) -> str:
    if parts[:1] == ["--"]:
        parts = parts[1:]
    text = " ".join(parts).strip()
    if not text:
        raise ValueError("command is empty")
    return text


def send_alias_request(
    alias: str,
    payload: dict[str, Any],
    timeout: int,
    connect_timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    profile_id, _profile, _config = resolve_profile(alias)
    ensure_daemon(profile_id, connect_timeout)
    try:
        return request(profile_id, payload, timeout=timeout)
    except (FileNotFoundError, ConnectionRefusedError):
        reap_stale()
        ensure_daemon(profile_id, connect_timeout)
        return request(profile_id, payload, timeout=timeout)


def operation_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.action in {"exec", "run"}:
        parts = list(getattr(args, "command", []))
        if parts[:1] == ["--"]:
            parts = parts[1:]
        return {"command": " ".join(parts).strip()}
    if args.action in {"job", "wait"}:
        return {"job_id": args.job_id, "tail": args.tail}
    return {
        key: getattr(args, key)
        for key in ("local", "remote", "max_bytes")
        if hasattr(args, key)
    }


def save_operation_result(args: argparse.Namespace, result: dict[str, Any], success: bool) -> None:
    result_store.save_snapshot(
        "ssh",
        args.action,
        result_store.canonical_project_root(),
        operation_request(args),
        result,
        success,
        getattr(args, "alias", ""),
    )


def render_exec(result: dict[str, Any], cached: bool) -> None:
    exit_code = int(result.get("exit_code", 0))
    if cached and exit_code:
        write_stream(sys.stdout, f"# exit={exit_code}\n")
    write_stream(sys.stdout, str(result.get("stdout", "")))
    stderr = str(result.get("stderr", ""))
    if not stderr:
        return
    if cached:
        stdout = str(result.get("stdout", ""))
        if stdout and not stdout.endswith("\n"):
            write_stream(sys.stdout, "\n")
        write_stream(sys.stdout, "# stderr\n")
        write_stream(sys.stdout, stderr)
    else:
        write_stream(sys.stderr, stderr)


def render_ls(result: dict[str, Any]) -> None:
    entries = result.get("entries", [])
    rows = [[item.get("type", ""), item.get("mode", ""), item.get("bytes", 0), item.get("name", "")] for item in entries]
    write_stream(sys.stdout, csv_text(f"entries={len(rows)}", ["type", "mode", "bytes", "name"], rows))


def render_job(result: dict[str, Any]) -> None:
    status = result.get("status", {})
    values: list[tuple[str, Any]] = [
        ("job", result.get("job_id", "")),
        ("state", status.get("state", "unknown")),
    ]
    if "exit_code" in status:
        values.append(("exit", status["exit_code"]))
    write_stream(sys.stdout, "# " + key_value_text(values))
    stdout = str(result.get("stdout", ""))
    write_stream(sys.stdout, stdout)
    stderr = str(result.get("stderr", ""))
    if stderr:
        if stdout and not stdout.endswith("\n"):
            write_stream(sys.stdout, "\n")
        write_stream(sys.stdout, "# stderr\n")
        write_stream(sys.stdout, stderr)


def render_result(action: str, result: dict[str, Any], as_json: bool, cached: bool = False) -> None:
    if as_json:
        print_json(result)
        return
    if result.get("error"):
        render_error(result)
        return
    if action == "exec":
        render_exec(result, cached)
    elif action == "cat":
        write_stream(sys.stdout, str(result.get("content", "")))
    elif action == "ls":
        render_ls(result)
    elif action in {"job", "wait"}:
        render_job(result)
    elif action == "run":
        write_stream(sys.stdout, key_value_text([
            ("job_id", result.get("job_id", "")),
            ("pid", result.get("pid", "")),
            ("remote_dir", result.get("remote_dir", "")),
        ]))
    elif action == "put":
        write_stream(sys.stdout, key_value_text([
            ("files", result.get("files", 0)),
            ("bytes", result.get("bytes", 0)),
            ("remote", result.get("remote", "")),
            ("sha256", result.get("sha256", "")),
        ]))
    elif action == "get":
        write_stream(sys.stdout, key_value_text([
            ("files", result.get("files", 0)),
            ("bytes", result.get("bytes", 0)),
            ("local", result.get("local", "")),
            ("sha256", result.get("sha256", "")),
        ]))
    else:
        print_json(result)


def finish_operation(args: argparse.Namespace, result: dict[str, Any], exit_code: int) -> int:
    save_operation_result(args, result, exit_code == 0)
    render_result(args.action, result, getattr(args, "json", False))
    return exit_code


def run_exec_cli(args: argparse.Namespace) -> int:
    command = command_text(args.command)
    result = send_alias_request(
        args.alias,
        {"action": "exec", "command": command, "timeout": args.timeout, "max_bytes": args.max_bytes},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    exit_code = 1 if result.get("error") else int(result.get("exit_code", 0))
    return finish_operation(args, result, exit_code)


def run_run_cli(args: argparse.Namespace) -> int:
    result = send_alias_request(
        args.alias,
        {"action": "run", "command": command_text(args.command), "timeout": args.timeout},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


def run_job_cli(args: argparse.Namespace) -> int:
    result = send_alias_request(
        args.alias,
        {"action": "job", "job_id": args.job_id, "tail": args.tail, "timeout": args.timeout},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


def run_wait_cli(args: argparse.Namespace) -> int:
    while True:
        result = send_alias_request(
            args.alias,
            {"action": "job", "job_id": args.job_id, "tail": args.tail, "timeout": args.timeout},
            timeout=args.timeout + 5,
            connect_timeout=args.timeout,
        )
        if result.get("error"):
            return finish_operation(args, result, 1)
        status_payload = result.get("status", {})
        if status_payload.get("state") == "finished":
            return finish_operation(args, result, int(status_payload.get("exit_code") or 0))
        time.sleep(args.interval)


def run_simple_request(args: argparse.Namespace) -> int:
    payload: dict[str, Any] = {"action": args.action}
    for name in ("local", "remote", "max_bytes"):
        if hasattr(args, name):
            payload[name] = getattr(args, name)
    payload["timeout"] = args.timeout
    result = send_alias_request(
        args.alias,
        payload,
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


def run_last(args: argparse.Namespace) -> int:
    root = result_store.canonical_project_root(args.root)
    if args.clear:
        print_json({"cleared": result_store.clear_snapshot("ssh", root)})
        return 0
    snapshot = result_store.load_snapshot("ssh", root)
    if args.summary:
        print_json(result_store.snapshot_view(snapshot, True))
        return 0
    if args.json:
        print_json(result_store.snapshot_view(snapshot, False))
        return 0
    render_result(str(snapshot.get("action", "")), snapshot.get("result", {}), False, cached=True)
    return 0


def add_json_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Return the structured JSON result")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared SSH/SFTP client for AI agents.")
    subparsers = parser.add_subparsers(dest="action", required=True)

    open_parser = subparsers.add_parser("open")
    open_parser.add_argument("alias")
    open_parser.add_argument("target", help="user@host, user@host:port, or host with --user")
    open_parser.add_argument("password", nargs="?")
    open_parser.add_argument("--user")
    open_parser.add_argument("--port", type=int)
    open_parser.add_argument("--key")
    open_parser.add_argument("--password", dest="password_option")
    open_parser.add_argument("--jump", action="append", default=[], metavar="ALIAS")
    open_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    open_parser.set_defaults(func=run_open)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("alias", nargs="?")
    add_json_arg(status_parser)
    status_parser.set_defaults(func=run_status)

    close_parser = subparsers.add_parser("close")
    close_parser.add_argument("alias", nargs="?")
    close_parser.add_argument("--all", action="store_true")
    close_parser.set_defaults(func=run_close)

    forget_parser = subparsers.add_parser("forget")
    forget_parser.add_argument("alias")
    forget_parser.set_defaults(func=run_forget)

    reap_parser = subparsers.add_parser("reap")
    reap_parser.set_defaults(func=lambda _args: (print_json({"removed": reap_stale()}) or 0))

    exec_parser = subparsers.add_parser("exec")
    exec_parser.add_argument("alias")
    exec_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    exec_parser.add_argument("--max-bytes", type=int, default=8000)
    add_json_arg(exec_parser)
    exec_parser.add_argument("command", nargs=argparse.REMAINDER)
    exec_parser.set_defaults(func=run_exec_cli)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("alias")
    run_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(run_parser)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    run_parser.set_defaults(func=run_run_cli)

    job_parser = subparsers.add_parser("job")
    job_parser.add_argument("alias")
    job_parser.add_argument("job_id")
    job_parser.add_argument("--tail", type=int, default=120)
    job_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(job_parser)
    job_parser.set_defaults(func=run_job_cli)

    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("alias")
    wait_parser.add_argument("job_id")
    wait_parser.add_argument("--tail", type=int, default=120)
    wait_parser.add_argument("--interval", type=int, default=5)
    wait_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(wait_parser)
    wait_parser.set_defaults(func=run_wait_cli)

    put_parser = subparsers.add_parser("put")
    put_parser.add_argument("alias")
    put_parser.add_argument("local")
    put_parser.add_argument("remote")
    put_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(put_parser)
    put_parser.set_defaults(func=run_simple_request)

    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("alias")
    get_parser.add_argument("remote")
    get_parser.add_argument("local")
    get_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(get_parser)
    get_parser.set_defaults(func=run_simple_request)

    ls_parser = subparsers.add_parser("ls")
    ls_parser.add_argument("alias")
    ls_parser.add_argument("remote")
    ls_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(ls_parser)
    ls_parser.set_defaults(func=run_simple_request)

    cat_parser = subparsers.add_parser("cat")
    cat_parser.add_argument("alias")
    cat_parser.add_argument("remote")
    cat_parser.add_argument("--max-bytes", type=int, default=4000)
    cat_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(cat_parser)
    cat_parser.set_defaults(func=run_simple_request)

    last_parser = subparsers.add_parser("last")
    last_parser.add_argument("--root")
    last_mode = last_parser.add_mutually_exclusive_group()
    last_mode.add_argument("--summary", action="store_true")
    last_mode.add_argument("--clear", action="store_true")
    last_mode.add_argument("--json", action="store_true", help="Return the structured JSON snapshot")
    last_parser.set_defaults(func=run_last)

    daemon_parser = subparsers.add_parser("daemon")
    daemon_parser.add_argument("--profile-id", required=True)
    daemon_parser.add_argument("--ready-file", required=True)
    daemon_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    daemon_parser.set_defaults(func=run_daemon)
    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    if argv and argv[0] not in COMMANDS and not argv[0].startswith("-"):
        return ["exec", argv[0], "--", *argv[1:]]
    return argv


def main() -> int:
    ensure_dirs()
    result_store.cleanup()
    parser = build_parser()
    args = parser.parse_args(normalize_argv(sys.argv[1:]))
    try:
        return args.func(args)
    except paramiko.AuthenticationException:
        error = {"error": "SSH authentication failed"}
        if args.action in CACHE_ACTIONS:
            save_operation_result(args, error, False)
        render_error(error, getattr(args, "json", False))
        return 2
    except Exception as exc:
        error = {"error": str(exc)}
        if args.action in CACHE_ACTIONS:
            save_operation_result(args, error, False)
        render_error(error, getattr(args, "json", False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
