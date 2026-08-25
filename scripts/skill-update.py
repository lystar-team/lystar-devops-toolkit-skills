#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


REPOSITORY = "lystar-team/lystar-devops-toolkit-skills"
RELEASE_API = os.environ.get(
    "LYSTAR_SKILL_RELEASE_API",
    f"https://api.github.com/repos/{REPOSITORY}/releases/latest",
)
NETWORK_TIMEOUT = 20
AUTO_INTERVAL_HOURS = 24
VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


def absolute_path(value: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def default_state_file() -> Path:
    configured = os.environ.get("LYSTAR_UPDATE_STATE_FILE", "").strip()
    if configured:
        return absolute_path(configured)
    configured_home = os.environ.get("LYSTAR_HOME", "").strip()
    if configured_home:
        return absolute_path(configured_home) / "state" / "update" / "installed.json"
    default_home = absolute_path(Path.home() / ".lystar")
    legacy_data = absolute_path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    legacy_config = absolute_path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    legacy_state = absolute_path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    if not default_home.exists() and any(
        (root / "agent-ops").exists() for root in (legacy_data, legacy_config, legacy_state)
    ):
        return legacy_state / "lystar-devops-toolkit-skills" / "installed.json"
    return default_home / "state" / "update" / "installed.json"


STATE_FILE = default_state_file()


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"skills": {}}
    try:
        payload = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"skills": {}}
    payload.setdefault("skills", {})
    return payload


def save_state(payload: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".installed.", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, STATE_FILE)
    except Exception:
        Path(temp_name).unlink(missing_ok=True)
        raise


def parse_version(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"不支持的版本号：{value}")
    return tuple(int(part) for part in match.groups())


def get_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "lystar-skill-update"})
    with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
        return response.read()


def latest_release() -> dict[str, Any]:
    return json.loads(get_bytes(RELEASE_API).decode("utf-8"))


def asset_urls(release: dict[str, Any]) -> dict[str, str]:
    return {
        str(asset["name"]): str(asset["browser_download_url"])
        for asset in release.get("assets", [])
        if asset.get("name") and asset.get("browser_download_url")
    }


def checksum_for(checksums: str, filename: str) -> str:
    for line in checksums.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    raise ValueError(f"SHA256SUMS 中找不到 {filename}")


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            target = (destination / item.filename).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"压缩包包含越界路径：{item.filename}")
            if stat.S_ISLNK(item.external_attr >> 16):
                raise ValueError(f"压缩包包含符号链接：{item.filename}")
        bundle.extractall(destination)


def selected_skills(state: dict[str, Any], name: str) -> list[str]:
    installed = sorted(state["skills"])
    if name == "all":
        if not installed:
            raise ValueError("没有已记录的 LYStar Skill")
        return installed
    if name not in state["skills"]:
        raise ValueError(f"未记录的 Skill：{name}；请先运行对应安装器")
    return [name]


def release_version(release: dict[str, Any]) -> str:
    value = str(release.get("tag_name", "")).lstrip("v")
    parse_version(value)
    return value


def due(last_checked: str | None) -> bool:
    if not last_checked:
        return True
    try:
        checked_at = dt.datetime.fromisoformat(last_checked.replace("Z", "+00:00"))
    except ValueError:
        return True
    return dt.datetime.now(dt.UTC) - checked_at >= dt.timedelta(hours=AUTO_INTERVAL_HOURS)


def record(args: argparse.Namespace) -> int:
    version = Path(args.version_file).read_text(encoding="utf-8").strip()
    parse_version(version)
    state = load_state()
    previous = state["skills"].get(args.skill, {})
    runtime_home = args.runtime_home or args.data_home or ""
    if not runtime_home:
        runtime_home = str(absolute_path(args.lystar_home or Path.home() / ".lystar") / "runtime")
    lystar_home = args.lystar_home or str(Path(runtime_home).parent)
    config_home = args.config_home or str(absolute_path(lystar_home) / "config")
    state_home = args.state_home or str(absolute_path(lystar_home) / "state")
    state["skills"][args.skill] = {
        **previous,
        "version": version,
        "asset": args.asset,
        "skill_homes": [str(absolute_path(path)) for path in args.skill_homes.splitlines() if path],
        "lystar_home": str(absolute_path(lystar_home)),
        "runtime_home": str(absolute_path(runtime_home)),
        "config_home": str(absolute_path(config_home)),
        "state_home": str(absolute_path(state_home)),
        "data_home": str(absolute_path(runtime_home)),
        "bin_home": str(absolute_path(args.bin_home)),
        "python_bin": args.python_bin,
        "server_home": str(absolute_path(args.server_home)) if args.server_home else "",
        "installed_at": utc_now(),
    }
    save_state(state)
    return 0


def report(name: str, current: str, latest: str, quiet: bool) -> None:
    if quiet:
        return
    status_text = "可更新" if parse_version(latest) > parse_version(current) else "已是最新"
    print(f"{name}: {current} -> {latest} ({status_text})")


def installer_command(installer: Path, entry: dict[str, Any]) -> list[str]:
    command = ["sh", str(installer)]
    for skill_home in entry.get("skill_homes", []):
        command.extend(["--skills-home", str(skill_home)])
    if entry.get("server_home"):
        command.extend(["--server-home", str(entry["server_home"])])
    return command


def install_update(name: str, entry: dict[str, Any], release: dict[str, Any], quiet: bool) -> None:
    urls = asset_urls(release)
    asset_name = str(entry["asset"])
    if asset_name not in urls or "SHA256SUMS" not in urls:
        raise ValueError(f"Release 缺少 {asset_name} 或 SHA256SUMS")

    with tempfile.TemporaryDirectory(prefix="lystar-skill-update-") as temp:
        temp_path = Path(temp)
        archive = temp_path / asset_name
        archive.write_bytes(get_bytes(urls[asset_name]))
        expected = checksum_for(get_bytes(urls["SHA256SUMS"]).decode("utf-8"), asset_name)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"{asset_name} SHA-256 校验失败")
        package = temp_path / "package"
        package.mkdir()
        safe_extract(archive, package)
        installer = package / "install.sh"
        if not installer.is_file():
            raise ValueError(f"{asset_name} 缺少 install.sh")
        environment = os.environ.copy()
        lystar_home = entry.get("lystar_home") or str(Path(entry.get("runtime_home") or entry["data_home"]).parent)
        environment.update({
            "LYSTAR_HOME": str(lystar_home),
            "LYSTAR_RUNTIME_HOME": str(entry.get("runtime_home") or entry["data_home"]),
            "LYSTAR_CONFIG_HOME": str(entry.get("config_home") or Path(lystar_home) / "config"),
            "LYSTAR_STATE_HOME": str(entry.get("state_home") or Path(lystar_home) / "state"),
            "LYSTAR_BIN_HOME": str(entry["bin_home"]),
            "LYSTAR_UPDATE_STATE_FILE": str(STATE_FILE),
            "PYTHON_BIN": str(entry["python_bin"]),
            "LYSTAR_SKILL_UPDATE": "1",
        })
        if entry.get("server_home"):
            environment["LYSTAR_SERVER_HOME"] = str(entry["server_home"])
        subprocess.run(installer_command(installer, entry), cwd=package, env=environment, check=True)
    if not quiet:
        print(f"{name}: 更新完成")


def run_check_or_update(args: argparse.Namespace) -> int:
    state = load_state()
    names = selected_skills(state, args.skill)
    if args.action == "auto":
        names = [name for name in names if due(state["skills"][name].get("last_checked"))]
        if not names:
            return 0
        checked_at = utc_now()
        for name in names:
            state["skills"][name]["last_checked"] = checked_at
        save_state(state)

    release = latest_release()
    latest = release_version(release)
    checked_at = utc_now()
    for name in names:
        state = load_state()
        entry = state["skills"][name]
        current = str(entry["version"])
        if args.action in {"update", "auto"} and parse_version(latest) > parse_version(current):
            install_update(name, entry, release, args.quiet)
            state = load_state()
        else:
            report(name, current, latest, args.quiet)
        state["skills"][name]["last_checked"] = checked_at
        save_state(state)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="检查并更新 LYStar DevOps Toolkit Skills")
    subparsers = parser.add_subparsers(dest="action", required=True)

    record_parser = subparsers.add_parser("record", help=argparse.SUPPRESS)
    record_parser.add_argument("skill")
    record_parser.add_argument("--asset", required=True)
    record_parser.add_argument("--version-file", required=True)
    record_parser.add_argument("--skill-homes", required=True)
    record_parser.add_argument("--lystar-home")
    record_parser.add_argument("--runtime-home")
    record_parser.add_argument("--config-home")
    record_parser.add_argument("--state-home")
    record_parser.add_argument("--data-home")
    record_parser.add_argument("--bin-home", required=True)
    record_parser.add_argument("--python-bin", required=True)
    record_parser.add_argument("--server-home")
    record_parser.set_defaults(func=record)

    for action in ("check", "update", "auto"):
        action_parser = subparsers.add_parser(action)
        action_parser.add_argument("skill", nargs="?", default="all")
        action_parser.add_argument("--quiet", action="store_true")
        action_parser.set_defaults(func=run_check_or_update)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(f"更新失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
