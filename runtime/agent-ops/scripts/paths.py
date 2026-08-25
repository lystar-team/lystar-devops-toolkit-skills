#!/usr/bin/env python3
"""LYStar 本地目录布局的唯一事实源。

新安装统一使用 ``$HOME/.lystar``。当旧安装尚未迁移且新目录不存在时，
短期兼容旧的 XDG/agent-ops 目录，避免旧命令在迁移前突然失效。
"""

from __future__ import annotations

import os
from pathlib import Path


def _path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return _path(value) if value else None


def home() -> Path:
    configured = os.environ.get("LYSTAR_HOME", "").strip()
    if configured:
        return _path(configured)
    return _path(Path.home() / ".lystar")


def _legacy_data_home() -> Path:
    return _path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def _legacy_config_home() -> Path:
    return _path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _legacy_state_home() -> Path:
    return _path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def legacy_mode() -> bool:
    if os.environ.get("LYSTAR_LEGACY_MODE", "").strip() in {"1", "true", "yes"}:
        return True
    if os.environ.get("LYSTAR_HOME", "").strip():
        return False
    if home().exists():
        return False
    return (
        (_legacy_data_home() / "agent-ops").exists()
        or (_legacy_config_home() / "agent-ops").exists()
        or (_legacy_state_home() / "agent-ops").exists()
    )


def runtime_home() -> Path:
    configured = _configured_path("LYSTAR_RUNTIME_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        return _legacy_data_home() / "agent-ops"
    return home() / "runtime"


def scripts_home() -> Path:
    return runtime_home() / "scripts"


def vendor_home() -> Path:
    return runtime_home() / "vendor"


def config_home() -> Path:
    configured = _configured_path("LYSTAR_CONFIG_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        return _legacy_config_home() / "agent-ops"
    return home() / "config"


def state_home() -> Path:
    configured = _configured_path("LYSTAR_STATE_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        return _legacy_state_home() / "agent-ops"
    return home() / "state"


def bin_home() -> Path:
    configured = _configured_path("LYSTAR_BIN_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        return _path(os.environ.get("XDG_BIN_HOME", Path.home() / ".local" / "bin"))
    return home() / "bin"


def servers_home() -> Path:
    configured = _configured_path("LYSTAR_SERVER_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        configured = _configured_path("LYSTAR_SERVER_OPS_HOME")
        if configured is not None:
            return configured
        return _path(Path.home() / "lystar-server-list")
    return home() / "servers"


def recipes_home() -> Path:
    configured = os.environ.get("DEPLOYX_RECIPE_HOME", "").strip()
    if configured:
        return _path(configured)
    configured = _configured_path("LYSTAR_RECIPE_HOME")
    if configured is not None:
        return configured
    if legacy_mode():
        return _legacy_data_home() / "agent-ops" / "recipes"
    return home() / "data" / "recipes"


def registry_file() -> Path:
    return config_home() / "ops.toml"


def registry_revision_dir() -> Path:
    return state_home() / "registry" / "revisions"


def update_state_file() -> Path:
    configured = _configured_path("LYSTAR_UPDATE_STATE_FILE")
    if configured is not None:
        return configured
    if legacy_mode():
        return _legacy_state_home() / "lystar-devops-toolkit-skills" / "installed.json"
    return home() / "state" / "update" / "installed.json"


def version_file() -> Path:
    configured = _configured_path("LYSTAR_VERSION_FILE")
    if configured is not None:
        return configured
    return home() / "VERSION"
