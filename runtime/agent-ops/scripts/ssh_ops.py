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
import re
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

import registry_store
import result_store
from config_store import FileTransaction, load_toml, lock_files, write_toml


CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
CONFIG_FILE = CONFIG_HOME / "agent-ops" / "ssh.toml"
BASE_DIR = STATE_HOME / "agent-ops" / "ssh"
RUN_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", BASE_DIR / "run")) / "agent-ops-ssh"
SCRIPT = Path(__file__).resolve()
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 120
DEFAULT_SFTP_CHUNK_SIZE = 4 * 1024 * 1024
COMMANDS = {
    "open", "status", "close", "forget", "reap", "exec", "run", "job",
    "jobs", "cancel", "wait", "put", "get", "ls", "cat", "last", "daemon",
    "forward",
}
CACHE_ACTIONS = {
    "exec", "run", "job", "jobs", "cancel", "wait", "put", "get", "ls", "cat",
    "forward",
}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
FORWARD_CHUNK_BYTES = 64 * 1024


class RegistryReferenceError(ValueError):
    """删除 SSH alias/profile 前发现注册表依赖。"""

    def __init__(self, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.details = details


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
        impact = result.get("impact")
        if isinstance(impact, dict):
            for kind in ("deployments", "services", "backup_assets", "relations", "project_bindings"):
                items = impact.get(kind, [])
                if isinstance(items, list) and items:
                    ids = ", ".join(
                        str(
                            item.get("id")
                            or item.get("path")
                            or item.get("project_root")
                            or ""
                        )
                        for item in items
                        if isinstance(item, dict)
                    )
                    if ids:
                        write_stream(sys.stderr, f"impact {kind}: {ids}\n")
        if "confirmation_required" in result:
            write_stream(
                sys.stderr,
                f"confirmation_required={plain_value(result.get('confirmation_required'))}\n",
            )
        if "registry_revision" in result:
            write_stream(sys.stderr, f"registry_revision={result.get('registry_revision')}\n")


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


def ssh_registry() -> registry_store.RegistryStore:
    registry_file = os.environ.get("SSHX_REGISTRY_FILE") or str(CONFIG_FILE.with_name("ops.toml"))
    revision_dir = os.environ.get("SSHX_REGISTRY_REVISION_DIR") or None
    return registry_store.RegistryStore(registry_file=registry_file, revision_dir=revision_dir)


def reconcile_ssh_registry(
    alias: str,
    profile_id: str,
    remaining_aliases: list[str],
    confirm: bool,
    document: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    removing_profile = not remaining_aliases
    impact = registry_store.reference_impact(
        document,
        ssh_aliases={alias},
        ssh_profiles={profile_id} if removing_profile else set(),
    )
    result: dict[str, Any] = {
        "target": {
            "kind": "ssh_profile" if removing_profile else "ssh_alias",
            "alias": alias,
            "profile": profile_id,
            "remaining_aliases": remaining_aliases,
        },
        "impact": impact,
        "synced": [],
        "orphaned": [],
        "confirmation_required": False,
        "registry_revision": document.get("revision", 0),
    }
    if not impact["has_references"]:
        result["status"] = "ready"
        return result, False

    if not removing_profile:
        replacement = sorted(remaining_aliases)[0]
        changes = registry_store.synchronize_references(
            document,
            {alias: replacement},
            {
                "deployments": ("ssh_alias",),
                "backup_assets": ("ssh_alias",),
                "relations": ("source_id", "target_id"),
            },
        )
        result["status"] = "synced"
        result["synced"] = changes
        return result, bool(changes)

    if not confirm:
        result["status"] = "blocked"
        result["confirmation_required"] = True
        result["confirmation_hint"] = "sshx forget --confirm <alias>"
        raise RegistryReferenceError(
            f"cannot forget SSH profile {profile_id}; registry references exist",
            result,
        )

    marked = registry_store.mark_references_orphaned(
        document,
        impact,
        f"SSH profile removed: {profile_id}",
    )
    result["status"] = "orphaned"
    result["orphaned"] = marked
    return result, bool(marked)


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


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("invalid job id")
    return job_id


class PortForward:
    """A local TCP listener backed by the daemon's existing SSH transport."""

    def __init__(
        self,
        daemon: "Daemon",
        forward_id: str,
        bind_host: str,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ):
        self.daemon = daemon
        self.forward_id = forward_id
        self.bind_host = bind_host
        self.requested_local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.listener: socket.socket | None = None
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.state = "created"
        self.error = ""
        self.created_at = utc_now()
        self.closed_at = ""
        self.active_connections = 0
        self.last_error = ""

    def start(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((self.bind_host, self.requested_local_port))
            listener.listen(32)
            listener.settimeout(0.5)
        except Exception:
            listener.close()
            raise

        self.listener = listener
        self.state = "open"
        self.thread = threading.Thread(
            target=self._accept_loop,
            name=f"sshx-forward-{self.forward_id}",
            daemon=True,
        )
        self.thread.start()

    def _accept_loop(self) -> None:
        assert self.listener is not None
        while not self.stop_event.is_set():
            try:
                client, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if not self.stop_event.is_set():
                    self._set_error(str(exc))
                break

            with self.lock:
                self.active_connections += 1
            threading.Thread(
                target=self._bridge,
                args=(client, address),
                name=f"sshx-forward-connection-{self.forward_id}",
                daemon=True,
            ).start()

    def _set_error(self, message: str) -> None:
        with self.lock:
            self.last_error = message
            if self.state == "open":
                self.state = "error"
                self.error = message

    def _bridge(self, client: socket.socket, address: tuple[str, int]) -> None:
        channel: paramiko.Channel | None = None
        try:
            transport = self.daemon.client.get_transport() if self.daemon.client else None
            if not transport or not transport.is_active():
                raise RuntimeError("SSH transport is unavailable")
            channel = transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                address,
                timeout=self.daemon.connection_timeout,
            )
            channel.settimeout(0.5)
            client.settimeout(0.5)

            def pump(source: Any, target: Any) -> None:
                try:
                    while not self.stop_event.is_set():
                        try:
                            data = source.recv(FORWARD_CHUNK_BYTES)
                        except socket.timeout:
                            continue
                        except (OSError, EOFError, paramiko.SSHException):
                            break
                        if not data:
                            break
                        target.sendall(data)
                except (OSError, EOFError, paramiko.SSHException):
                    pass

            upstream = threading.Thread(target=pump, args=(client, channel), daemon=True)
            downstream = threading.Thread(target=pump, args=(channel, client), daemon=True)
            upstream.start()
            downstream.start()
            upstream.join()
            downstream.join(timeout=1)
        except Exception as exc:
            self._set_error(str(exc))
        finally:
            for resource in (channel, client):
                if resource is None:
                    continue
                try:
                    resource.close()
                except (OSError, EOFError, paramiko.SSHException):
                    pass
            with self.lock:
                self.active_connections = max(0, self.active_connections - 1)

    def close(self) -> dict[str, Any]:
        with self.lock:
            was_open = self.state in {"open", "error"}
            if self.state != "closed":
                self.state = "closed"
                self.closed_at = utc_now()
            self.stop_event.set()
            listener = self.listener
            thread = self.thread
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)
        return {**self.snapshot(), "closed": was_open}

    def snapshot(self) -> dict[str, Any]:
        local_host = self.bind_host
        local_port = self.requested_local_port
        if self.listener is not None:
            try:
                local_host, local_port = self.listener.getsockname()[:2]
            except OSError:
                pass
        result: dict[str, Any] = {
            "forward_id": self.forward_id,
            "state": self.state,
            "bind_host": local_host,
            "local_host": local_host,
            "local_port": local_port,
            "remote_host": self.remote_host,
            "remote_port": self.remote_port,
            "created_at": self.created_at,
            "active_connections": self.active_connections,
        }
        if self.closed_at:
            result["closed_at"] = self.closed_at
        if self.error:
            result["error"] = self.error
        if self.last_error and self.last_error != self.error:
            result["last_error"] = self.last_error
        return result


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


def sha256_remote(sftp: paramiko.SFTPClient, remote: str, chunk_size: int) -> str:
    digest = hashlib.sha256()
    with sftp.open(remote, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
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
    *,
    resume: bool = False,
    chunk_size: int = DEFAULT_SFTP_CHUNK_SIZE,
) -> dict[str, Any]:
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    local = Path(local_text).expanduser().resolve()
    if not local.exists():
        raise FileNotFoundError(f"local path not found: {local}")
    if resume and local.is_dir():
        raise ValueError("resume upload only supports a single file")
    files = 0
    bytes_count = 0
    transferred_bytes = 0
    chunks = 0
    resumed = False
    resume_offset = 0
    skipped = False
    local_digest = sha256_local(local) if local.is_file() else None
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
            local_size = local.stat().st_size
            offset = 0
            mode = "wb"
            if resume:
                try:
                    remote_size = sftp.stat(remote_file).st_size
                except OSError:
                    remote_size = 0
                if remote_size < local_size:
                    offset = remote_size
                    mode = "r+b"
                    resumed = offset > 0
                elif remote_size == local_size and local_digest is not None:
                    if sha256_remote(sftp, remote_file, chunk_size) == local_digest:
                        offset = local_size
                        resume_offset = offset
                        resumed = offset > 0
                        skipped = True
                    else:
                        mode = "r+b"
                elif remote_size > local_size:
                    mode = "r+b"
            if not skipped:
                with local.open("rb") as source:
                    source.seek(offset)
                    with sftp.open(remote_file, mode) as destination:
                        if mode == "r+b":
                            if offset == 0:
                                destination.truncate(0)
                            destination.seek(offset)
                        while True:
                            chunk = source.read(chunk_size)
                            if not chunk:
                                break
                            destination.write(chunk)
                            transferred_bytes += len(chunk)
                            chunks += 1
                        destination.flush()
                resume_offset = offset
            files = 1
            bytes_count = local_size
    result: dict[str, Any] = {
        "files": files,
        "bytes": bytes_count,
        "transferred_bytes": transferred_bytes if local.is_file() else bytes_count,
        "chunks": chunks,
        "remote": remote,
        "resume": bool(resume),
        "resumed": resumed,
        "resume_offset": resume_offset,
        "skipped": skipped,
        "chunk_size": chunk_size,
    }
    if local.is_file() and local_digest is not None:
        result["sha256"] = local_digest
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
    created_at = utc_now()
    job_id = dt.datetime.now(dt.UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = posixpath.join(remote_home(client, timeout), ".agent-ops", "jobs", job_id)
    with open_sftp_session(client, timeout) as sftp:
        sftp_mkdirs(sftp, job_dir)
        command_path = posixpath.join(job_dir, "command.sh")
        launcher_path = posixpath.join(job_dir, "launcher.sh")
        metadata_path = posixpath.join(job_dir, "job.json")
        with sftp.open(command_path, "w") as handle:
            handle.write(command + "\n")
        with sftp.open(metadata_path, "w") as handle:
            handle.write(json.dumps({
                "job_id": job_id,
                "command": command,
                "remote_dir": job_dir,
                "stdout_log": posixpath.join(job_dir, "stdout.log"),
                "stderr_log": posixpath.join(job_dir, "stderr.log"),
                "created_at": created_at,
            }, ensure_ascii=False) + "\n")
        with sftp.open(launcher_path, "w") as handle:
            handle.write(
                """#!/bin/sh
cd "$(dirname "$0")" || exit 127
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '{"state":"running","pid":%s,"started_at":"%s","updated_at":"%s"}\n' "$$" "$started" "$started" > status.json
cancelled=0
trap 'cancelled=1' TERM INT
/bin/sh command.sh > stdout.log 2> stderr.log
code=$?
ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ "$cancelled" -eq 1 ] || [ -f cancel.requested ]; then
    state=cancelled
    code=143
else
    state=finished
fi
printf '{"state":"%s","exit_code":%s,"pid":%s,"started_at":"%s","ended_at":"%s","updated_at":"%s"}\n' "$state" "$code" "$$" "$started" "$ended" "$ended" > status.json
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
    validate_job_id(job_id)
    job_dir = posixpath.join(remote_home(client, timeout), ".agent-ops", "jobs", job_id)
    quoted_dir = shlex.quote(job_dir)
    command = (
        f"cd {quoted_dir} 2>/dev/null || exit 44; "
        f"printf 'META_JSON\\n'; cat job.json 2>/dev/null || true; "
        f"printf '\\nSTATUS_JSON\\n'; cat status.json 2>/dev/null || true; "
        f"printf '\\nSTDOUT_TAIL\\n'; tail -n {int(tail)} stdout.log 2>/dev/null || true; "
        f"printf '\\nSTDERR_TAIL\\n'; tail -n {int(tail)} stderr.log 2>/dev/null || true"
    )
    reply = exec_command(client, command, timeout, 64_000)
    if int(reply.get("exit_code", 0)) == 44:
        return {
            "job_id": job_id,
            "status": {"state": "not_found"},
            "error": "job not found",
        }
    output = reply.get("stdout", "")
    metadata_text = ""
    status_text = ""
    stdout_tail = ""
    stderr_tail = ""
    if "META_JSON\n" in output and "\nSTATUS_JSON\n" in output:
        metadata_text, status_text = output.split("\nSTATUS_JSON\n", 1)
        metadata_text = metadata_text.replace("META_JSON\n", "", 1).strip()
        if "\nSTDOUT_TAIL\n" in status_text:
            status_text, stdout_tail = status_text.split("\nSTDOUT_TAIL\n", 1)
    elif "STDOUT_TAIL\n" in output:
        status_text, stdout_tail = output.split("STDOUT_TAIL\n", 1)
    if "\nSTDERR_TAIL\n" in stdout_tail:
        stdout_tail, stderr_tail = stdout_tail.split("\nSTDERR_TAIL\n", 1)
    try:
        status_payload = json.loads(status_text) if status_text else {"state": "unknown"}
    except json.JSONDecodeError:
        status_payload = {"state": "unknown", "raw": status_text}
    result: dict[str, Any] = {"job_id": job_id, "status": status_payload, "remote_dir": job_dir}
    try:
        metadata = json.loads(metadata_text) if metadata_text else {}
    except json.JSONDecodeError:
        metadata = {}
    if isinstance(metadata, dict):
        for key in ("command", "created_at", "stdout_log", "stderr_log"):
            if metadata.get(key):
                result[key] = metadata[key]
    if stdout_tail:
        result["stdout"] = stdout_tail
    if stderr_tail:
        result["stderr"] = stderr_tail
    return result


def remote_log_delta(
    client: paramiko.SSHClient,
    job_dir: str,
    log_name: str,
    offset: int,
    max_bytes: int,
    timeout: int,
) -> tuple[str, int, int, bool]:
    if offset < 0:
        raise ValueError("log offset must be non-negative")
    if max_bytes < 1:
        raise ValueError("log max bytes must be positive")
    quoted_dir = shlex.quote(job_dir)
    quoted_log = shlex.quote(log_name)
    command = (
        f"cd {quoted_dir} 2>/dev/null || exit 44; "
        f"size=$(wc -c < {quoted_log} 2>/dev/null || printf 0); "
        f"printf 'SIZE:%s\\n' \"$size\"; "
        f"start={int(offset)}; "
        f"[ \"$start\" -le \"$size\" ] || start=0; "
        f"if [ \"$size\" -gt \"$start\" ]; then "
        f"dd if={quoted_log} bs=1 skip=\"$start\" count={int(max_bytes)} 2>/dev/null; fi"
    )
    reply = exec_command(client, command, timeout, max_bytes + 128)
    if int(reply.get("exit_code", 0)) == 44:
        return "", offset, 0, False
    output = str(reply.get("stdout", ""))
    if not output.startswith("SIZE:"):
        return "", offset, 0, False
    header, data = output.split("\n", 1) if "\n" in output else (output, "")
    try:
        size = max(0, int(header.removeprefix("SIZE:")))
    except ValueError:
        return "", offset, 0, False
    start = offset if offset <= size else 0
    next_offset = min(size, start + max_bytes)
    return data, next_offset, size, start != offset


def remote_job_follow(
    client: paramiko.SSHClient,
    job_id: str,
    stdout_offset: int,
    stderr_offset: int,
    timeout: int = DEFAULT_TIMEOUT,
    max_bytes: int = FORWARD_CHUNK_BYTES,
) -> dict[str, Any]:
    status = remote_job_status(client, job_id, 0, timeout)
    if status.get("error"):
        return status
    job_dir = str(status["remote_dir"])
    stdout, next_stdout, stdout_size, stdout_reset = remote_log_delta(
        client, job_dir, "stdout.log", stdout_offset, max_bytes, timeout
    )
    stderr, next_stderr, stderr_size, stderr_reset = remote_log_delta(
        client, job_dir, "stderr.log", stderr_offset, max_bytes, timeout
    )
    state = status.get("status", {}).get("state", "unknown")
    result = {
        **status,
        "stdout_offset": next_stdout,
        "stderr_offset": next_stderr,
        "stdout_size": stdout_size,
        "stderr_size": stderr_size,
        "stdout_complete": next_stdout >= stdout_size,
        "stderr_complete": next_stderr >= stderr_size,
        "follow_complete": state in {"finished", "cancelled"}
        and next_stdout >= stdout_size
        and next_stderr >= stderr_size,
    }
    if stdout:
        result["stdout_delta"] = stdout
    if stderr:
        result["stderr_delta"] = stderr
    if stdout_reset or stderr_reset:
        result["offset_reset"] = True
    return result


def remote_jobs(
    client: paramiko.SSHClient,
    limit: int,
    tail: int,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("job limit must be positive")
    home = remote_home(client, timeout)
    jobs_dir = posixpath.join(home, ".agent-ops", "jobs")
    quoted_jobs_dir = shlex.quote(jobs_dir)
    command = (
        f"for path in {quoted_jobs_dir}/*; do "
        "[ -d \"$path\" ] || continue; basename \"$path\"; done"
    )
    reply = exec_command(client, command, timeout, 32_000)
    job_ids = [
        value.strip()
        for value in str(reply.get("stdout", "")).splitlines()
        if value.strip() and JOB_ID_RE.fullmatch(value.strip())
    ]
    jobs: list[dict[str, Any]] = []
    for job_id in sorted(job_ids, reverse=True)[:limit]:
        item = remote_job_status(client, job_id, tail, timeout)
        if not item.get("error"):
            jobs.append(item)
    return {"jobs": jobs, "remote_dir": jobs_dir}


def remote_job_cancel(
    client: paramiko.SSHClient,
    job_id: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    validate_job_id(job_id)
    current = remote_job_status(client, job_id, 0, timeout)
    if current.get("error"):
        return {**current, "cancelled": False}

    state = str(current.get("status", {}).get("state", "unknown"))
    if state in {"finished", "cancelled"}:
        return {**current, "cancelled": False, "reason": f"already_{state}"}
    if state != "running":
        return {**current, "cancelled": False, "reason": "not_running"}

    job_dir = shlex.quote(str(current["remote_dir"]))
    command = (
        f"cd {job_dir} 2>/dev/null || exit 44; "
        ": > cancel.requested; "
        "pid=$(sed -n 's/.*\"pid\":\\([0-9][0-9]*\\).*/\\1/p' status.json | head -n 1); "
        "if [ -n \"$pid\" ]; then "
        "kill -TERM -- \"-$pid\" 2>/dev/null || kill -TERM \"$pid\" 2>/dev/null || true; fi"
    )
    reply = exec_command(client, command, timeout, 2_000)
    if int(reply.get("exit_code", 0)) == 44:
        return {"job_id": job_id, "status": {"state": "not_found"}, "cancelled": False, "error": "job not found"}

    deadline = time.monotonic() + min(3, max(1, timeout))
    latest = current
    while time.monotonic() < deadline:
        latest = remote_job_status(client, job_id, 0, timeout)
        if latest.get("error"):
            break
        latest_state = str(latest.get("status", {}).get("state", "unknown"))
        if latest_state in {"cancelled", "finished"}:
            break
        time.sleep(0.1)
    latest_state = str(latest.get("status", {}).get("state", "cancelling"))
    return {
        **latest,
        "cancelled": latest_state == "cancelled",
        "cancel_requested": True,
        "status": {**latest.get("status", {}), "state": latest_state},
    }


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
        self.forward_lock = threading.Lock()
        self.forwards: dict[str, PortForward] = {}
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
        with self.forward_lock:
            forwards = list(self.forwards.values())
        for forward in forwards:
            forward.close()
        close_connection_chain(self.clients, self.proxy_channels)
        self.client = None
        self.clients = []
        self.proxy_channels = []

    def open_forward(self, payload: dict[str, Any]) -> dict[str, Any]:
        bind_host = str(payload.get("bind_host", "127.0.0.1"))
        local_port = int(payload.get("local_port", 0))
        remote_host = str(payload.get("remote_host", ""))
        remote_port = int(payload.get("remote_port", 0))
        if not remote_host:
            raise ValueError("forward remote host is required")
        if not 0 <= local_port <= 65535:
            raise ValueError("forward local port must be between 0 and 65535")
        if not 1 <= remote_port <= 65535:
            raise ValueError("forward remote port must be between 1 and 65535")
        self.ensure_connected()
        forward_id = "fwd-" + uuid.uuid4().hex[:8]
        forward = PortForward(
            self,
            forward_id,
            bind_host,
            local_port,
            remote_host,
            remote_port,
        )
        forward.start()
        with self.forward_lock:
            self.forwards[forward_id] = forward
        return forward.snapshot()

    def forward_list(self, forward_id: str = "") -> dict[str, Any]:
        with self.forward_lock:
            forwards = list(self.forwards.values())
        if forward_id:
            forwards = [forward for forward in forwards if forward.forward_id == forward_id]
        return {
            "profile": self.profile_id,
            "forwards": [forward.snapshot() for forward in forwards],
        }

    def close_forward(self, forward_id: str) -> dict[str, Any]:
        with self.forward_lock:
            forward = self.forwards.get(forward_id)
        if not forward:
            return {
                "profile": self.profile_id,
                "forward_id": forward_id,
                "state": "not_found",
                "closed": False,
            }
        return {"profile": self.profile_id, **forward.close()}

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
        if action == "forward_open":
            return {"profile": self.profile_id, **self.open_forward(payload)}
        if action in {"forward_list", "forward_status"}:
            return self.forward_list(str(payload.get("forward_id", "")))
        if action == "forward_close":
            return self.close_forward(str(payload.get("forward_id", "")))
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
        if action == "jobs":
            return remote_jobs(
                client,
                int(payload.get("limit", 100)),
                int(payload.get("tail", 0)),
                operation_timeout,
            )
        if action == "cancel":
            return remote_job_cancel(client, payload["job_id"], operation_timeout)
        if action == "job_follow":
            return remote_job_follow(
                client,
                payload["job_id"],
                int(payload.get("stdout_offset", 0)),
                int(payload.get("stderr_offset", 0)),
                operation_timeout,
                int(payload.get("max_bytes", FORWARD_CHUNK_BYTES)),
            )
        if action == "put":
            return sftp_put(
                client,
                payload["local"],
                payload["remote"],
                operation_timeout,
                resume=bool(payload.get("resume", False)),
                chunk_size=int(payload.get("chunk_size", DEFAULT_SFTP_CHUNK_SIZE)),
            )
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
    store = ssh_registry()
    with FileTransaction(
        (CONFIG_FILE, store.registry_file),
        glob_paths=((store.revision_dir, "revision-*.toml"),),
    ) as transaction:
        profile_id, _profile, config = resolve_profile(args.alias)
        all_aliases = aliases_for(config, profile_id)
        remaining = [item for item in all_aliases if item != args.alias]
        if not remaining:
            dependents = [
                dependent_id
                for dependent_id, profile in config["profiles"].items()
                if dependent_id != profile_id and profile_id in profile_jump_ids(profile)
            ]
            if dependents:
                labels = ", ".join(profile_aliases_or_id(config, item) for item in dependents)
                raise ValueError(f"cannot forget SSH profile {profile_id}; used by jump profiles: {labels}")
        document = store.load_locked()
        registry_result, registry_changed = reconcile_ssh_registry(
            args.alias,
            profile_id,
            remaining,
            bool(getattr(args, "confirm", False)),
            document,
        )
        config["aliases"].pop(args.alias, None)
        remaining = aliases_for(config, profile_id)
        if not remaining:
            close_profile(profile_id)
            config["profiles"].pop(profile_id, None)
        save_config(config)
        if registry_changed:
            committed = store.save_locked(document)
            registry_result["registry_revision"] = committed.get("revision", 0)
        transaction.commit()
    print_json({
        "forgotten": args.alias,
        "profile": profile_id,
        "remaining_aliases": remaining,
        "registry": registry_result,
    })
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
        return {
            "job_id": args.job_id,
            "tail": args.tail,
            "follow": bool(getattr(args, "follow", False)),
            "interval": getattr(args, "interval", 0),
        }
    if args.action == "jobs":
        return {"tail": args.tail, "limit": args.limit}
    if args.action == "cancel":
        return {"job_id": args.job_id}
    if args.action == "forward":
        values = {"forward_action": args.forward_action}
        for name in (
            "forward_id", "bind_host", "local_port_arg", "remote_host_arg",
            "remote_port_arg", "local_port_option", "remote_host_option",
            "remote_port_option",
        ):
            if hasattr(args, name) and getattr(args, name) not in (None, ""):
                values[name] = getattr(args, name)
        return values
    return {
        key: getattr(args, key)
        for key in ("local", "remote", "max_bytes", "resume", "chunk_size")
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


def render_jobs(result: dict[str, Any]) -> None:
    rows = []
    for job in result.get("jobs", []):
        status = job.get("status", {})
        command = str(job.get("command", "")).replace("\n", " ")
        rows.append([
            job.get("job_id", ""),
            command,
            status.get("state", "unknown"),
            status.get("pid", ""),
            status.get("started_at", job.get("created_at", "")),
            status.get("ended_at", ""),
            job.get("stdout_log", ""),
            job.get("stderr_log", ""),
        ])
    write_stream(
        sys.stdout,
        csv_text(
            f"jobs={len(rows)}",
            ["job_id", "command", "state", "pid", "created_at", "ended_at", "stdout_log", "stderr_log"],
            rows,
        ),
    )


def render_forward_list(result: dict[str, Any]) -> None:
    rows = []
    for forward in result.get("forwards", []):
        rows.append([
            forward.get("forward_id", ""),
            forward.get("state", "unknown"),
            forward.get("bind_host", forward.get("local_host", "")),
            forward.get("local_port", ""),
            forward.get("remote_host", ""),
            forward.get("remote_port", ""),
            forward.get("active_connections", 0),
            forward.get("error", ""),
        ])
    write_stream(
        sys.stdout,
        csv_text(
            f"forwards={len(rows)}",
            ["forward_id", "state", "bind_host", "local_port", "remote_host", "remote_port", "active_connections", "error"],
            rows,
        ),
    )


def render_follow_event(job_id: str, event: str, payload: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print_json({"event": event, "job_id": job_id, **payload})
        return
    if event == "state":
        values: list[tuple[str, Any]] = [("job", job_id), ("state", payload.get("state", "unknown"))]
        if "exit_code" in payload:
            values.append(("exit", payload["exit_code"]))
        write_stream(sys.stdout, "# " + key_value_text(values))
        return
    write_stream(sys.stdout, f"# {event}\n")
    write_stream(sys.stdout, str(payload.get("data", "")))


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
    elif action == "jobs":
        render_jobs(result)
    elif action == "cancel":
        write_stream(sys.stdout, key_value_text([
            ("job_id", result.get("job_id", "")),
            ("state", result.get("status", {}).get("state", "unknown")),
            ("cancelled", result.get("cancelled", False)),
            ("cancel_requested", result.get("cancel_requested", False)),
        ]))
    elif action == "forward":
        if "forwards" in result:
            render_forward_list(result)
        else:
            write_stream(sys.stdout, key_value_text([
                ("forward_id", result.get("forward_id", "")),
                ("state", result.get("state", "unknown")),
                ("local", f"{result.get('bind_host', result.get('local_host', ''))}:{result.get('local_port', '')}"),
                ("remote", f"{result.get('remote_host', '')}:{result.get('remote_port', '')}"),
                ("closed", result.get("closed", "")),
            ]))
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
            ("transferred_bytes", result.get("transferred_bytes", 0)),
            ("chunks", result.get("chunks", 0)),
            ("remote", result.get("remote", "")),
            ("sha256", result.get("sha256", "")),
            ("resumed", result.get("resumed", False)),
            ("resume_offset", result.get("resume_offset", 0)),
            ("skipped", result.get("skipped", False)),
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
    if args.follow:
        return run_follow_cli(args)
    result = send_alias_request(
        args.alias,
        {"action": "job", "job_id": args.job_id, "tail": args.tail, "timeout": args.timeout},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


def run_wait_cli(args: argparse.Namespace) -> int:
    if args.follow:
        return run_follow_cli(args)
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
        if status_payload.get("state") in {"finished", "cancelled"}:
            exit_code = int(status_payload.get("exit_code") or 0)
            return finish_operation(args, result, exit_code)
        time.sleep(args.interval)


def run_follow_cli(args: argparse.Namespace) -> int:
    stdout_offset = 0
    stderr_offset = 0
    last_state = ""
    result: dict[str, Any] = {}
    while True:
        result = send_alias_request(
            args.alias,
            {
                "action": "job_follow",
                "job_id": args.job_id,
                "stdout_offset": stdout_offset,
                "stderr_offset": stderr_offset,
                "timeout": args.timeout,
                "max_bytes": FORWARD_CHUNK_BYTES,
            },
            timeout=args.timeout + 5,
            connect_timeout=args.timeout,
        )
        if result.get("error"):
            save_operation_result(args, result, False)
            render_result(args.action, result, getattr(args, "json", False))
            return 1

        status = result.get("status", {})
        state = str(status.get("state", "unknown"))
        if state != last_state:
            render_follow_event(args.job_id, "state", status, getattr(args, "json", False))
            last_state = state
        if result.get("stdout_delta"):
            render_follow_event(
                args.job_id,
                "stdout",
                {"data": result["stdout_delta"]},
                getattr(args, "json", False),
            )
        if result.get("stderr_delta"):
            render_follow_event(
                args.job_id,
                "stderr",
                {"data": result["stderr_delta"]},
                getattr(args, "json", False),
            )
        stdout_offset = int(result.get("stdout_offset", stdout_offset))
        stderr_offset = int(result.get("stderr_offset", stderr_offset))
        if result.get("follow_complete"):
            exit_code = int(status.get("exit_code") or 0)
            save_operation_result(args, result, exit_code == 0)
            return exit_code
        time.sleep(args.interval)


def run_jobs_cli(args: argparse.Namespace) -> int:
    result = send_alias_request(
        args.alias,
        {"action": "jobs", "limit": args.limit, "tail": args.tail, "timeout": args.timeout},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


def run_cancel_cli(args: argparse.Namespace) -> int:
    result = send_alias_request(
        args.alias,
        {"action": "cancel", "job_id": args.job_id, "timeout": args.timeout},
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    if result.get("error") and result.get("status", {}).get("state") != "not_found":
        exit_code = 1
    elif result.get("status", {}).get("state") == "not_found":
        exit_code = 1
    else:
        exit_code = 0
    return finish_operation(args, result, exit_code)


def forward_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.forward_action == "open":
        local_port = args.local_port_option if args.local_port_option is not None else args.local_port_arg
        remote_host = args.remote_host_option or args.remote_host_arg
        remote_port = args.remote_port_option if args.remote_port_option is not None else args.remote_port_arg
        if local_port is None or not remote_host or remote_port is None:
            raise ValueError("forward open needs local port, remote host, and remote port")
        return {
            "action": "forward_open",
            "bind_host": args.bind_host,
            "local_port": local_port,
            "remote_host": remote_host,
            "remote_port": remote_port,
            "timeout": args.timeout,
        }
    if args.forward_action == "list":
        return {"action": "forward_list", "timeout": args.timeout}
    if args.forward_action == "status":
        return {
            "action": "forward_status",
            "forward_id": args.forward_id,
            "timeout": args.timeout,
        }
    if args.forward_action == "close":
        return {
            "action": "forward_close",
            "forward_id": args.forward_id,
            "timeout": args.timeout,
        }
    raise ValueError(f"unknown forward action: {args.forward_action}")


def run_forward_cli(args: argparse.Namespace) -> int:
    result = send_alias_request(
        args.alias,
        forward_request(args),
        timeout=args.timeout + 5,
        connect_timeout=args.timeout,
    )
    return finish_operation(args, result, 1 if result.get("error") else 0)


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
    forget_parser.add_argument(
        "--confirm",
        action="store_true",
        help="确认删除最后一个 alias，并将注册表依赖标记为 orphaned",
    )
    add_json_arg(forget_parser)
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
    job_parser.add_argument("--follow", action="store_true")
    job_parser.add_argument("--interval", type=int, default=5)
    add_json_arg(job_parser)
    job_parser.set_defaults(func=run_job_cli)

    jobs_parser = subparsers.add_parser("jobs")
    jobs_parser.add_argument("alias")
    jobs_parser.add_argument("--limit", type=int, default=100)
    jobs_parser.add_argument("--tail", type=int, default=0)
    jobs_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(jobs_parser)
    jobs_parser.set_defaults(func=run_jobs_cli)

    cancel_parser = subparsers.add_parser("cancel")
    cancel_parser.add_argument("alias")
    cancel_parser.add_argument("job_id")
    cancel_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(cancel_parser)
    cancel_parser.set_defaults(func=run_cancel_cli)

    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("alias")
    wait_parser.add_argument("job_id")
    wait_parser.add_argument("--tail", type=int, default=120)
    wait_parser.add_argument("--interval", type=int, default=5)
    wait_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    wait_parser.add_argument("--follow", action="store_true")
    add_json_arg(wait_parser)
    wait_parser.set_defaults(func=run_wait_cli)

    forward_parser = subparsers.add_parser("forward")
    forward_subparsers = forward_parser.add_subparsers(dest="forward_action", required=True)

    forward_open = forward_subparsers.add_parser("open")
    forward_open.add_argument("alias")
    forward_open.add_argument("local_port_arg", nargs="?", type=int)
    forward_open.add_argument("remote_host_arg", nargs="?")
    forward_open.add_argument("remote_port_arg", nargs="?", type=int)
    forward_open.add_argument("--bind-host", default="127.0.0.1")
    forward_open.add_argument("--local-port", dest="local_port_option", type=int)
    forward_open.add_argument("--remote-host", dest="remote_host_option")
    forward_open.add_argument("--remote-port", dest="remote_port_option", type=int)
    forward_open.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(forward_open)
    forward_open.set_defaults(func=run_forward_cli)

    for name in ("list", "status"):
        parser_for_action = forward_subparsers.add_parser(name)
        parser_for_action.add_argument("alias")
        if name == "status":
            parser_for_action.add_argument("forward_id")
        parser_for_action.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
        add_json_arg(parser_for_action)
        parser_for_action.set_defaults(func=run_forward_cli)

    forward_close = forward_subparsers.add_parser("close")
    forward_close.add_argument("alias")
    forward_close.add_argument("forward_id")
    forward_close.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    add_json_arg(forward_close)
    forward_close.set_defaults(func=run_forward_cli)

    put_parser = subparsers.add_parser("put")
    put_parser.add_argument("alias")
    put_parser.add_argument("local")
    put_parser.add_argument("remote")
    put_parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    put_parser.add_argument("--resume", action="store_true", help="从远端已有文件偏移继续上传")
    put_parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_SFTP_CHUNK_SIZE,
        help="单次 SFTP 写入字节数",
    )
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
        error = {"error": str(exc), **getattr(exc, "details", {})}
        if args.action in CACHE_ACTIONS:
            save_operation_result(args, error, False)
        render_error(error, getattr(args, "json", False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
