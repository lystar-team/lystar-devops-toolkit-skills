#!/bin/sh
set -eu
unset CODEX_HOME CLAUDE_CONFIG_DIR OPENCODE_CONFIG_DIR PI_CODING_AGENT_DIR SKILLS_HOME

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT HUP INT TERM

real_python=$(command -v python3)
export REAL_PYTHON=$real_python
fake_python="$temp/python3"
cat >"$fake_python" <<'EOF'
#!/bin/sh
if [ "$1" = "-c" ]; then
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ] && [ "$3" = "--version" ]; then
    exit 0
fi
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    exit 0
fi
exec "$REAL_PYTHON" "$@"
EOF
chmod +x "$fake_python"

make_harness_commands() {
    directory=$1
    shift
    mkdir -p "$directory"
    for command in "$@"; do
        cat >"$directory/$command" <<'EOF'
#!/bin/sh
exit 0
EOF
        chmod +x "$directory/$command"
    done
}

assert_skill() {
    test -f "$1/$2/SKILL.md"
    grep -q "^name: $2$" "$1/$2/SKILL.md"
}

assert_server_data() {
    server_home=$1
    test -d "$server_home"
    test ! -L "$server_home"
    test -f "$server_home/README.md"
    test -f "$server_home/_templates/server.md"
    test -f "$server_home/_templates/service.md"
    test -f "$server_home/_templates/topic.md"
}

# 自动探测：只模拟 Codex 和 Pi。XDG 目录不再作为新安装位置。
auto="$temp/auto"
make_harness_commands "$auto/path" codex pi
HOME="$auto/home" \
PATH="$auto/path:/usr/bin:/bin" \
XDG_DATA_HOME="$auto/data" \
XDG_STATE_HOME="$auto/state" \
XDG_BIN_HOME="$auto/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install.sh" >/dev/null
for command in dbx sshx hostx deployx backupx incidentx codeupx redisx magicx lystar-skill-update lystar-migrate; do
    test -x "$auto/home/.lystar/bin/$command"
done
test ! -e "$auto/bin/dbx"
assert_skill "$auto/home/.agents/skills" lystar-db-ops
assert_skill "$auto/home/.agents/skills" lystar-ssh-ops
assert_skill "$auto/home/.agents/skills" lystar-host-ops
assert_skill "$auto/home/.agents/skills" lystar-deploy-ops
assert_skill "$auto/home/.agents/skills" lystar-backup-ops
assert_skill "$auto/home/.agents/skills" lystar-incident-ops
assert_skill "$auto/home/.agents/skills" lystar-codeup-devops
assert_skill "$auto/home/.agents/skills" lystar-redis-ops
assert_skill "$auto/home/.agents/skills" lystar-magicapi-ops
test ! -e "$auto/home/.claude/skills/lystar-db-ops"
test ! -e "$auto/home/.config/opencode/skills/lystar-db-ops"
assert_server_data "$auto/home/.lystar/servers"
test ! -e "$auto/home/.agents/skills/lystar-ssh-ops/servers"
test ! -e "$auto/home/.codex/skills/lystar-ssh-ops"
test ! -e "$auto/home/.pi/agent/skills/lystar-ssh-ops"

# 显式单选不会受其它命令影响。
single="$temp/single"
make_harness_commands "$single/path" opencode codex claude pi
HOME="$single/home" \
PATH="$single/path:/usr/bin:/bin" \
XDG_DATA_HOME="$single/data" \
XDG_STATE_HOME="$single/state" \
XDG_BIN_HOME="$single/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-db-ops.sh" --harness claude >/dev/null
assert_skill "$single/home/.claude/skills" lystar-db-ops
test -x "$single/home/.lystar/bin/dbx"
test ! -e "$single/home/.codex/skills/lystar-db-ops"
test ! -e "$single/home/.pi/agent/skills/lystar-db-ops"
test ! -e "$single/home/.config/opencode/skills/lystar-db-ops"

# 新增的单独 Skill 使用相同 Lystar 根目录。
codeup="$temp/codeup"
HOME="$codeup/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$codeup/data" \
XDG_STATE_HOME="$codeup/state" \
XDG_BIN_HOME="$codeup/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-codeup-devops.sh" --harness pi >/dev/null
assert_skill "$codeup/home/.agents/skills" lystar-codeup-devops
test -x "$codeup/home/.lystar/bin/codeupx"
test -f "$codeup/home/.lystar/runtime/scripts/codeup_devops.py"

# Redis Skill shares the same Lystar root and installs its own command.
redis="$temp/redis"
HOME="$redis/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$redis/data" \
XDG_STATE_HOME="$redis/state" \
XDG_BIN_HOME="$redis/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-redis-ops.sh" --harness pi >/dev/null
assert_skill "$redis/home/.agents/skills" lystar-redis-ops
test -x "$redis/home/.lystar/bin/redisx"
test -f "$redis/home/.lystar/runtime/scripts/redis_ops.py"

# Magic-API Skill shares the same Lystar root and installs its own command.
magicapi="$temp/magicapi"
HOME="$magicapi/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$magicapi/data" \
XDG_STATE_HOME="$magicapi/state" \
XDG_BIN_HOME="$magicapi/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-magicapi-ops.sh" --harness pi >/dev/null
assert_skill "$magicapi/home/.agents/skills" lystar-magicapi-ops
test -x "$magicapi/home/.lystar/bin/magicx"
test -f "$magicapi/home/.lystar/runtime/scripts/magicapi_ops.py"
test -f "$magicapi/home/.agents/skills/lystar-magicapi-ops/references/magicapi-contract.md"

# 显式多选与自定义服务器目录。
multi="$temp/multi"
server_home="$multi/private-servers"
HOME="$multi/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$multi/data" \
XDG_STATE_HOME="$multi/state" \
XDG_BIN_HOME="$multi/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-ssh-ops.sh" --harness opencode,codex,pi --server-home "$server_home" >/dev/null
for skills_home in "$multi/home/.config/opencode/skills" "$multi/home/.agents/skills"; do
    assert_skill "$skills_home" lystar-ssh-ops
    test ! -e "$skills_home/lystar-ssh-ops/servers"
done
assert_server_data "$server_home"
test ! -e "$multi/home/.claude/skills/lystar-ssh-ops"

# 自定义目录可重复传入，并保留已有服务器资料。
custom="$temp/custom"
mkdir -p "$custom/server-home"
printf '%s\n' '用户服务器资料' >"$custom/server-home/README.md"
HOME="$custom/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$custom/data" \
XDG_STATE_HOME="$custom/state" \
XDG_BIN_HOME="$custom/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-ssh-ops.sh" \
        --skills-home "$custom/skills-a" \
        --skills-home "$custom/skills-b" \
        --server-home "$custom/server-home" >/dev/null
for skills_home in "$custom/skills-a" "$custom/skills-b"; do
    assert_skill "$skills_home" lystar-ssh-ops
    test ! -e "$skills_home/lystar-ssh-ops/servers"
done
assert_server_data "$custom/server-home"
grep -q '^用户服务器资料$' "$custom/server-home/README.md"

# 旧名称只有 SKILL.md 或软链接时自动清理。
legacy="$temp/legacy"
mkdir -p "$legacy/skills/ssh-ops" "$legacy/skills/sql-multi-db-ops"
printf '%s\n' legacy >"$legacy/skills/ssh-ops/SKILL.md"
printf '%s\n' legacy >"$legacy/skills/sql-multi-db-ops/SKILL.md"
ln -s "$legacy/old-server" "$legacy/skills/lystar-server-ops"
HOME="$legacy/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$legacy/data" \
XDG_STATE_HOME="$legacy/state" \
XDG_BIN_HOME="$legacy/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install.sh" --skills-home "$legacy/skills" >/dev/null
test ! -e "$legacy/skills/ssh-ops"
test ! -e "$legacy/skills/sql-multi-db-ops"
test ! -e "$legacy/skills/lystar-server-ops"

# 安装器自动迁移旧配置、状态和服务器目录，但保留旧目录。
migrated="$temp/migrated"
mkdir -p "$migrated/old-data/agent-ops" "$migrated/old-config/agent-ops" \
    "$migrated/old-state/agent-ops" "$migrated/old-state/lystar-devops-toolkit-skills" \
    "$migrated/old-server"
printf '%s\n' old-runtime >"$migrated/old-data/agent-ops/old.txt"
printf '%s\n' old-config >"$migrated/old-config/agent-ops/ssh.toml"
printf '%s\n' old-state >"$migrated/old-state/agent-ops/state.txt"
printf '%s\n' '{"skills":{}}' >"$migrated/old-state/lystar-devops-toolkit-skills/installed.json"
printf '%s\n' old-server >"$migrated/old-server/README.md"
HOME="$migrated/home" \
PATH="/usr/bin:/bin" \
XDG_DATA_HOME="$migrated/old-data" \
XDG_CONFIG_HOME="$migrated/old-config" \
XDG_STATE_HOME="$migrated/old-state" \
XDG_BIN_HOME="$migrated/old-bin" \
LYSTAR_SERVER_OPS_HOME="$migrated/old-server" \
PYTHON_BIN="$fake_python" \
    sh "$root/install-lystar-ssh-ops.sh" --harness codex >/dev/null
test -f "$migrated/home/.lystar/runtime/old.txt"
test -f "$migrated/home/.lystar/config/ssh.toml"
test -f "$migrated/home/.lystar/state/state.txt"
test -f "$migrated/home/.lystar/state/update/installed.json"
test -f "$migrated/home/.lystar/servers/README.md"
test -f "$migrated/old-config/agent-ops/ssh.toml"
test -f "$migrated/old-server/README.md"

# 更新器记录了九个 Skill 和新布局。
python3 - "$auto/home/.lystar/state/update/installed.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert sorted(state["skills"]) == ["lystar-backup-ops", "lystar-codeup-devops", "lystar-db-ops", "lystar-deploy-ops", "lystar-host-ops", "lystar-incident-ops", "lystar-magicapi-ops", "lystar-redis-ops", "lystar-ssh-ops"]
assert len(state["skills"]["lystar-db-ops"]["skill_homes"]) == 1
assert state["skills"]["lystar-ssh-ops"]["lystar_home"].endswith("/.lystar")
assert state["skills"]["lystar-ssh-ops"]["server_home"].endswith("/.lystar/servers")
PY

echo "install tests passed"
