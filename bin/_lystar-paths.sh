#!/bin/sh

# 新安装的唯一根目录。只有在新根目录尚未创建、且检测到旧 agent-ops
# 数据时，才进入兼容模式；这样旧版本可以先完成迁移，再切换到新布局。
lystar_default_home=${LYSTAR_HOME:-"$HOME/.lystar"}
legacy_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
legacy_config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
legacy_state_home=${XDG_STATE_HOME:-"$HOME/.local/state"}

if [ -n "${LYSTAR_HOME:-}" ] || [ -d "$lystar_default_home" ] || {
    [ ! -d "$legacy_data_home/agent-ops" ] &&
    [ ! -d "$legacy_config_home/agent-ops" ] &&
    [ ! -d "$legacy_state_home/agent-ops" ];
}; then
    export LYSTAR_HOME="$lystar_default_home"
    export LYSTAR_RUNTIME_HOME="$LYSTAR_HOME/runtime"
    export LYSTAR_BIN_HOME="$LYSTAR_HOME/bin"
else
    export LYSTAR_LEGACY_MODE=1
    export LYSTAR_RUNTIME_HOME="$legacy_data_home/agent-ops"
    export LYSTAR_BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"
fi

if [ -n "${LYSTAR_LEGACY_MODE:-}" ]; then
    export LYSTAR_CONFIG_HOME="${LYSTAR_CONFIG_HOME:-$legacy_config_home/agent-ops}"
    export LYSTAR_STATE_HOME="${LYSTAR_STATE_HOME:-$legacy_state_home/agent-ops}"
    export LYSTAR_SERVER_HOME="${LYSTAR_SERVER_HOME:-${LYSTAR_SERVER_OPS_HOME:-$HOME/lystar-server-list}}"
    export LYSTAR_RECIPE_HOME="${LYSTAR_RECIPE_HOME:-$legacy_data_home/agent-ops/recipes}"
else
    export LYSTAR_CONFIG_HOME="${LYSTAR_CONFIG_HOME:-$LYSTAR_HOME/config}"
    export LYSTAR_STATE_HOME="${LYSTAR_STATE_HOME:-$LYSTAR_HOME/state}"
    export LYSTAR_SERVER_HOME="${LYSTAR_SERVER_HOME:-$LYSTAR_HOME/servers}"
    export LYSTAR_RECIPE_HOME="${LYSTAR_RECIPE_HOME:-$LYSTAR_HOME/data/recipes}"
fi

if [ -n "${LYSTAR_LEGACY_MODE:-}" ]; then
    export LYSTAR_UPDATE_STATE_FILE="${LYSTAR_UPDATE_STATE_FILE:-$legacy_state_home/lystar-devops-toolkit-skills/installed.json}"
else
    export LYSTAR_UPDATE_STATE_FILE="${LYSTAR_UPDATE_STATE_FILE:-$LYSTAR_STATE_HOME/update/installed.json}"
fi
