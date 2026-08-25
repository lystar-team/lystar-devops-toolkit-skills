#!/usr/bin/env python3
"""把旧版 agent-ops 布局安全迁移到 ``$HOME/.lystar``。

迁移只复制缺失目标，不删除旧目录、不覆盖已有目标，也不替换已有的
服务器资料目录。Yean 的 Git 服务器资料可以通过 ``--server-link`` 显式接入。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


OLD_UPDATE_RELATIVE = Path("lystar-devops-toolkit-skills") / "installed.json"


def absolute(path: str | Path) -> Path:
    return Path(path).expanduser().absolute()


def default_old_root(name: str, fallback: Path) -> Path:
    return absolute(os.environ.get(name, str(fallback)))


def copy_missing_tree(
    source: Path,
    target: Path,
    label: str,
    report: dict[str, list[str]],
    exclude: set[str] | None = None,
) -> None:
    if not source.exists():
        return
    if not source.is_dir():
        raise RuntimeError(f"旧目录不是目录：{source}")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        if exclude and relative.parts and relative.parts[0] in exclude:
            continue
        destination = target / relative
        display = f"{label}/{relative.as_posix()}"
        if item.is_dir() and not item.is_symlink():
            if destination.exists() and not destination.is_dir():
                report["conflicts"].append(display)
                continue
            if not destination.exists():
                destination.mkdir(parents=True, mode=0o700)
                try:
                    shutil.copystat(item, destination, follow_symlinks=False)
                except OSError:
                    pass
            continue
        if destination.exists() or destination.is_symlink():
            report["skipped"].append(display)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if item.is_symlink():
            destination.symlink_to(os.readlink(item))
        else:
            shutil.copy2(item, destination)
        report["copied"].append(display)


def copy_missing_file(source: Path, target: Path, label: str, report: dict[str, list[str]]) -> None:
    if not source.exists():
        return
    if target.exists() or target.is_symlink():
        report["skipped"].append(label)
        return
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copy2(source, target)
    report["copied"].append(label)


def ensure_server_directory(target: Path, report: dict[str, list[str]]) -> None:
    if target.is_symlink():
        if not target.is_dir():
            raise RuntimeError(f"服务器资料软链接无效：{target}")
        report["skipped"].append(f"server:{target}")
        return
    if target.exists() and not target.is_dir():
        raise RuntimeError(f"服务器资料路径不是目录：{target}")
    if not target.exists():
        target.mkdir(parents=True, mode=0o700)
        report["created"].append(f"server:{target}")


def ensure_server_link(target: Path, link_target: Path, report: dict[str, list[str]]) -> None:
    link_target = link_target.resolve()
    if target.is_symlink():
        if target.resolve() != link_target:
            raise RuntimeError(f"服务器资料软链接已指向其他目录：{target}")
        report["skipped"].append(f"server-link:{target}")
        return
    if target.exists():
        raise RuntimeError(f"服务器资料路径已存在，拒绝覆盖：{target}")
    if not link_target.is_dir():
        raise RuntimeError(f"软链接目标不存在或不是目录：{link_target}")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.symlink_to(link_target, target_is_directory=True)
    report["created"].append(f"server-link:{target}->{link_target}")


def migrate(args: argparse.Namespace) -> dict[str, Any]:
    target_home = absolute(args.target_home)
    old_data = absolute(args.old_data)
    old_config = absolute(args.old_config)
    old_state = absolute(args.old_state)
    old_server = absolute(args.old_server) if args.old_server else None
    report: dict[str, list[str]] = {"created": [], "copied": [], "skipped": [], "conflicts": []}

    target_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    target_runtime = target_home / "runtime"
    target_config = target_home / "config"
    target_state = target_home / "state"
    target_data = target_home / "data"
    for directory in (target_runtime, target_config, target_state, target_data):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    old_runtime = old_data / "agent-ops"
    old_config_dir = old_config / "agent-ops"
    old_state_dir = old_state / "agent-ops"
    copy_missing_tree(old_runtime, target_runtime, "runtime", report, exclude={"recipes"})
    if old_runtime.exists():
        copy_missing_tree(old_runtime / "recipes", target_data / "recipes", "recipes", report)
    copy_missing_tree(old_config_dir, target_config, "config", report)
    copy_missing_tree(old_state_dir, target_state, "state", report, exclude={"ops"})
    copy_missing_tree(old_state_dir / "ops", target_state / "registry", "state/registry", report)
    copy_missing_file(
        old_state / OLD_UPDATE_RELATIVE,
        target_state / "update" / "installed.json",
        "state/update/installed.json",
        report,
    )

    server_target = absolute(args.server_home) if args.server_home else target_home / "servers"
    if args.server_link:
        ensure_server_link(server_target, absolute(args.server_link), report)
    else:
        ensure_server_directory(server_target, report)
        if old_server is not None and old_server.exists() and old_server != server_target:
            copy_missing_tree(old_server, server_target, "servers", report)

    return {
        "target_home": str(target_home),
        "server_home": str(server_target),
        "legacy_sources": {
            "data": str(old_runtime),
            "config": str(old_config_dir),
            "state": str(old_state_dir),
            "server": str(old_server) if old_server else "",
        },
        "report": report,
    }


def build_parser() -> argparse.ArgumentParser:
    home = Path.home()
    parser = argparse.ArgumentParser(description="迁移 LYStar 旧版本地目录到 $HOME/.lystar")
    parser.add_argument("--target-home", default=os.environ.get("LYSTAR_HOME", str(home / ".lystar")))
    parser.add_argument("--old-data", default=default_old_root("XDG_DATA_HOME", home / ".local" / "share"))
    parser.add_argument("--old-config", default=default_old_root("XDG_CONFIG_HOME", home / ".config"))
    parser.add_argument("--old-state", default=default_old_root("XDG_STATE_HOME", home / ".local" / "state"))
    parser.add_argument("--old-server", default=os.environ.get("LYSTAR_SERVER_OPS_HOME", str(home / "lystar-server-list")))
    parser.add_argument("--server-home")
    parser.add_argument("--server-link", help="把目标 servers 显式软链接到已有服务器资料目录")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.server_home and args.server_link:
        print("迁移失败：--server-home 与 --server-link 不能同时使用。", file=sys.stderr)
        return 2
    try:
        result = migrate(args)
    except Exception as exc:
        print(f"迁移失败：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        report = result["report"]
        print(f"迁移完成：{result['target_home']}")
        print(f"服务器资料：{result['server_home']}")
        print(f"复制 {len(report['copied'])} 项，跳过 {len(report['skipped'])} 项，创建 {len(report['created'])} 项。")
        if report["conflicts"]:
            print(f"存在 {len(report['conflicts'])} 个路径冲突，未覆盖。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
