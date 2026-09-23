#!/bin/sh

python_bin=${PYTHON_BIN:-python3}
lystar_home=${LYSTAR_HOME:-"$HOME/.lystar"}
legacy_data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
legacy_config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
legacy_state_home=${XDG_STATE_HOME:-"$HOME/.local/state"}
legacy_bin_home=${XDG_BIN_HOME:-"$HOME/.local/bin"}
runtime_home="$lystar_home/runtime"
config_home="$lystar_home/config"
state_home="$lystar_home/state"
bin_home="$lystar_home/bin"
server_ops_home=${LYSTAR_SERVER_HOME:-"$lystar_home/servers"}
old_server_home=${LYSTAR_SERVER_OPS_HOME:-"$HOME/lystar-server-list"}
server_home_explicit=0
legacy_layout_detected=0
if [ -d "$legacy_data_home/agent-ops" ] || [ -d "$legacy_config_home/agent-ops" ] || [ -d "$legacy_state_home/agent-ops" ]; then
    legacy_layout_detected=1
fi
compat_bin_home=
skill_homes=

usage() {
    cat <<'EOF'
用法：install.sh [选项]

  --harness <auto|all|opencode|codex|claude|pi>[,...]
  --skills-home <目录>    安装到自定义 Agent Skills 目录，可重复
  --server-home <目录>    兼容入口：使用指定服务器资料目录
  -h, --help              显示帮助
EOF
}

add_skill_home() {
    candidate=$1
    [ -n "$candidate" ] || return
    case "
$skill_homes
" in
        *"
$candidate
"*) return ;;
    esac
    if [ -n "$skill_homes" ]; then
        skill_homes="$skill_homes
$candidate"
    else
        skill_homes=$candidate
    fi
}

harness_installed() {
    case "$1" in
        opencode) command -v opencode >/dev/null 2>&1 || [ -d "${OPENCODE_CONFIG_DIR:-"$HOME/.config/opencode"}" ] ;;
        codex) command -v codex >/dev/null 2>&1 || [ -d "${CODEX_HOME:-"$HOME/.codex"}" ] ;;
        claude) command -v claude >/dev/null 2>&1 || [ -d "${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}" ] ;;
        pi) command -v pi >/dev/null 2>&1 || [ -d "${PI_CODING_AGENT_DIR:-"$HOME/.pi/agent"}" ] ;;
        *) return 1 ;;
    esac
}

harness_skill_home() {
    case "$1" in
        opencode) printf '%s\n' "${OPENCODE_CONFIG_DIR:-"$HOME/.config/opencode"}/skills" ;;
        codex) printf '%s\n' "${AGENTS_SKILLS_HOME:-"$HOME/.agents/skills"}" ;;
        claude) printf '%s\n' "${CLAUDE_CONFIG_DIR:-"$HOME/.claude"}/skills" ;;
        pi) printf '%s\n' "${AGENTS_SKILLS_HOME:-"$HOME/.agents/skills"}" ;;
    esac
}

normalize_harness() {
    case "$1" in
        claude-code|claudecode) printf '%s\n' claude ;;
        *) printf '%s\n' "$1" ;;
    esac
}

add_harnesses() {
    value=$1
    old_ifs=$IFS
    IFS=,
    for raw in $value; do
        harness=$(normalize_harness "$raw")
        case "$harness" in
            opencode|codex|claude|pi) add_skill_home "$(harness_skill_home "$harness")" ;;
            all)
                for item in opencode codex claude pi; do
                    add_skill_home "$(harness_skill_home "$item")"
                done
                ;;
            auto)
                for item in opencode codex claude pi; do
                    if harness_installed "$item"; then
                        add_skill_home "$(harness_skill_home "$item")"
                    fi
                done
                ;;
            *)
                echo "安装失败：未知 Harness：$raw" >&2
                exit 2
                ;;
        esac
    done
    IFS=$old_ifs
}

parse_install_args() {
    explicit_harness=
    custom_homes=
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --harness)
                [ "$#" -ge 2 ] || { echo "安装失败：--harness 缺少参数。" >&2; exit 2; }
                explicit_harness="${explicit_harness:+$explicit_harness,}$2"
                shift 2
                ;;
            --skills-home)
                [ "$#" -ge 2 ] || { echo "安装失败：--skills-home 缺少参数。" >&2; exit 2; }
                custom_homes="$custom_homes
$2"
                shift 2
                ;;
            --server-home)
                [ "$#" -ge 2 ] || { echo "安装失败：--server-home 缺少参数。" >&2; exit 2; }
                server_ops_home=$2
                server_home_explicit=1
                shift 2
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                echo "安装失败：未知参数：$1" >&2
                usage >&2
                exit 2
                ;;
        esac
    done

    if [ -n "${SKILLS_HOME:-}" ]; then
        custom_homes="$custom_homes
$SKILLS_HOME"
    fi
    if [ -n "$explicit_harness" ]; then
        add_harnesses "$explicit_harness"
    elif [ -z "$custom_homes" ]; then
        add_harnesses auto
    fi

    old_ifs=$IFS
    IFS='
'
    for home in $custom_homes; do
        [ -n "$home" ] && add_skill_home "$home"
    done
    IFS=$old_ifs

    if [ -z "$skill_homes" ]; then
        echo "安装失败：没有探测到支持的 Harness。请用 --harness 指定，或用 --skills-home 提供目录。" >&2
        exit 1
    fi

    if [ "$legacy_layout_detected" = 1 ] && [ -d "$legacy_bin_home" ]; then
        for command in dbx sshx hostx deployx backupx incidentx codeupx redisx magicx lystar-skill-update lystar-migrate; do
            if [ -f "$legacy_bin_home/$command" ] && grep -Eq 'agent-ops|lystar' "$legacy_bin_home/$command"; then
                compat_bin_home=$legacy_bin_home
                break
            fi
        done
    fi

    if [ "$legacy_layout_detected" = 1 ] && [ "$lystar_home" != "$HOME/.lystar" ]; then
        echo "  迁移旧目录到：$lystar_home" >&2
    fi
}

require_python() {
    if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
        echo "安装失败：需要 Python 3.11 或更高版本。" >&2
        exit 1
    fi
    if ! "$python_bin" -m pip --version >/dev/null 2>&1; then
        echo "安装失败：$python_bin 缺少 pip。" >&2
        exit 1
    fi
    migrate_legacy_layout
}

migrate_legacy_layout() {
    [ "$legacy_layout_detected" = 1 ] || return 0
    set -- "$python_bin" "$root/runtime/agent-ops/scripts/migrate.py" \
        --target-home "$lystar_home" \
        --old-data "$legacy_data_home" \
        --old-config "$legacy_config_home" \
        --old-state "$legacy_state_home"
    if [ "$server_home_explicit" = 1 ]; then
        set -- "$@" --server-home "$server_ops_home"
        if [ -d "$old_server_home" ] && [ "$old_server_home" != "$server_ops_home" ]; then
            set -- "$@" --old-server "$old_server_home"
        fi
    elif [ -d "$old_server_home" ]; then
        set -- "$@" --old-server "$old_server_home"
    fi
    "$@" >/dev/null
}

install_python_dependencies() {
    mkdir -p "$runtime_home/vendor"
    "$python_bin" -m pip install --ignore-installed --no-warn-conflicts --upgrade \
        --target "$runtime_home/vendor" -r "$1"
}

install_runtime_scripts() {
    mkdir -p "$runtime_home/scripts"
    install -m 0644 "$root/runtime/agent-ops/scripts/paths.py" "$runtime_home/scripts/paths.py"
    for script in "$@"; do
        install -m 0644 "$root/runtime/agent-ops/scripts/$script" "$runtime_home/scripts/$script"
    done
}

install_runtime_tests() {
    mkdir -p "$runtime_home/tests"
    install -m 0644 "$root"/runtime/agent-ops/tests/*.py "$runtime_home/tests/"
}

install_command() {
    mkdir -p "$bin_home"
    install -m 0755 "$root/bin/$1" "$bin_home/$1"
    install -m 0644 "$root/bin/_lystar-paths.sh" "$bin_home/_lystar-paths.sh"
    if [ -n "$compat_bin_home" ] && [ "$compat_bin_home" != "$bin_home" ] && [ -f "$compat_bin_home/$1" ] &&
        grep -Eq 'agent-ops|lystar' "$compat_bin_home/$1"; then
        install -m 0755 "$root/bin/$1" "$compat_bin_home/$1"
        install -m 0644 "$root/bin/_lystar-paths.sh" "$compat_bin_home/_lystar-paths.sh"
    fi
}

install_codeup_cli_plugin() {
    aliyun_bin="$bin_home/aliyun"
    if [ ! -x "$aliyun_bin" ]; then
        aliyun_bin=$(command -v aliyun 2>/dev/null || true)
    fi
    [ -x "$aliyun_bin" ] || return 0
    export ALIBABA_CLOUD_CLI_PLUGINS_DIR="${ALIBABA_CLOUD_CLI_PLUGINS_DIR:-$runtime_home/aliyun/plugins}"
    mkdir -p "$ALIBABA_CLOUD_CLI_PLUGINS_DIR"
    "$aliyun_bin" plugin install --names aliyun-cli-devops >/dev/null 2>&1 ||
        echo "  提示：云效 CLI 插件安装失败，请稍后执行 aliyun plugin install --names aliyun-cli-devops。" >&2
}

install_update_tool() {
    mkdir -p "$runtime_home/scripts" "$bin_home"
    install -m 0644 "$root/scripts/skill-update.py" "$runtime_home/scripts/skill_update.py"
    install -m 0644 "$root/runtime/agent-ops/scripts/migrate.py" "$runtime_home/scripts/migrate.py"
    install -m 0755 "$root/bin/lystar-skill-update" "$bin_home/lystar-skill-update"
    install -m 0755 "$root/bin/lystar-migrate" "$bin_home/lystar-migrate"
    install -m 0644 "$root/bin/_lystar-paths.sh" "$bin_home/_lystar-paths.sh"
    if [ -n "$compat_bin_home" ] && [ "$compat_bin_home" != "$bin_home" ]; then
        for command in lystar-skill-update lystar-migrate; do
            if [ -f "$compat_bin_home/$command" ] && grep -Eq 'agent-ops|lystar' "$compat_bin_home/$command"; then
                install -m 0755 "$root/bin/$command" "$compat_bin_home/$command"
                install -m 0644 "$root/bin/_lystar-paths.sh" "$compat_bin_home/_lystar-paths.sh"
            fi
        done
    fi
}

for_each_skill_home() {
    callback=$1
    shift
    old_ifs=$IFS
    IFS='
'
    for skills_home in $skill_homes; do
        "$callback" "$skills_home" "$@"
    done
    IFS=$old_ifs
}

install_regular_skill_at() {
    skills_home=$1
    skill_name=$2
    mkdir -p "$skills_home/$skill_name"
    install -m 0644 "$root/skills/$skill_name/SKILL.md" "$skills_home/$skill_name/SKILL.md"
    for extra_dir in agents references examples scripts schemas evals; do
        if [ -d "$root/skills/$skill_name/$extra_dir" ]; then
            mkdir -p "$skills_home/$skill_name/$extra_dir"
            cp -R "$root/skills/$skill_name/$extra_dir/." "$skills_home/$skill_name/$extra_dir/"
        fi
    done
    if [ -f "$root/skills/$skill_name/requirements.txt" ]; then
        install -m 0644 "$root/skills/$skill_name/requirements.txt" "$skills_home/$skill_name/requirements.txt"
    fi
    install -m 0644 "$root/VERSION" "$skills_home/$skill_name/VERSION"
}

install_regular_skill() {
    for_each_skill_home install_regular_skill_at "$1"
}

remove_legacy_skill_at() {
    skills_home=$1
    old_name=$2
    legacy="$skills_home/$old_name"
    if [ -L "$legacy" ]; then
        rm -f "$legacy"
    elif [ -d "$legacy" ] && [ -f "$legacy/SKILL.md" ] && \
        [ "$(find "$legacy" -mindepth 1 -maxdepth 1 | wc -l)" -eq 1 ]; then
        rm -f "$legacy/SKILL.md"
        rmdir "$legacy"
    elif [ -e "$legacy" ]; then
        echo "  提示：保留已有目录 $legacy，请确认无自定义内容后手工删除。" >&2
    fi
}

remove_legacy_skill() {
    for_each_skill_home remove_legacy_skill_at "$1"
}

install_if_missing() {
    source=$1
    target=$2
    if [ ! -e "$target" ]; then
        install -m 0600 "$source" "$target"
    fi
}

install_server_data() {
    old_umask=$(umask)
    umask 077
    mkdir -p "$server_ops_home/_templates"
    install_if_missing "$root/templates/server-list/README.md" "$server_ops_home/README.md"
    install_if_missing "$root/templates/server-list/_templates/server.md" "$server_ops_home/_templates/server.md"
    install_if_missing "$root/templates/server-list/_templates/service.md" "$server_ops_home/_templates/service.md"
    install_if_missing "$root/templates/server-list/_templates/topic.md" "$server_ops_home/_templates/topic.md"
    umask "$old_umask"
}

remove_legacy_server_link_at() {
    skills_home=$1
    destination="$skills_home/lystar-ssh-ops/servers"
    [ -L "$destination" ] || return 0
    destination_real=$(CDPATH= cd -- "$destination" 2>/dev/null && pwd -P) || destination_real=
    server_real=$(CDPATH= cd -- "$server_ops_home" 2>/dev/null && pwd -P) || server_real=
    if [ -n "$destination_real" ] && [ -n "$server_real" ] && [ "$destination_real" = "$server_real" ]; then
        unlink "$destination"
    else
        echo "  提示：保留旧服务器资料链接 $destination；请确认资料已迁移到 $server_ops_home 后手工删除。" >&2
    fi
}

remove_legacy_server_links() {
    for_each_skill_home remove_legacy_server_link_at
}

record_skill_installation() {
    skill_name=$1
    include_server_home=${2:-}
    set -- record "$skill_name" \
        --asset "$skill_name.zip" \
        --version-file "$root/VERSION" \
        --skill-homes "$skill_homes" \
        --lystar-home "$lystar_home" \
        --runtime-home "$runtime_home" \
        --config-home "$config_home" \
        --state-home "$state_home" \
        --bin-home "$bin_home" \
        --python-bin "$python_bin"
    if [ -n "$include_server_home" ]; then
        set -- "$@" --server-home "$server_ops_home"
    fi
    "$bin_home/lystar-skill-update" "$@"
}

verify_command() {
    LYSTAR_SKILL_UPDATE=1 "$bin_home/$1" --help >/dev/null
}

print_skill_locations() {
    old_ifs=$IFS
    IFS='
'
    for skills_home in $skill_homes; do
        for skill_name in "$@"; do
            echo "  Skill：$skills_home/$skill_name"
        done
    done
    IFS=$old_ifs
}

print_path_notice() {
    case ":$PATH:" in
        *":$bin_home:"*) ;;
        *) echo "  PATH：请将 $bin_home 加入 PATH" ;;
    esac
}
