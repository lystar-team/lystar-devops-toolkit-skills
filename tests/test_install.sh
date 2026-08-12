#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temp=$(mktemp -d)
trap 'rm -rf "$temp"' EXIT HUP INT TERM

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
exec python3 "$@"
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
    skill_home=$1
    server_home=$2
    test -L "$skill_home/lystar-ssh-ops/servers"
    test "$(readlink "$skill_home/lystar-ssh-ops/servers")" = "$server_home"
    test -f "$server_home/README.md"
    test -f "$server_home/_templates/server.md"
    test -f "$server_home/_templates/topic.md"
}

# 自动探测：只模拟 Codex 和 Pi。
auto="$temp/auto"
make_harness_commands "$auto/path" codex pi
HOME="$auto/home" \
PATH="$auto/path:/usr/bin:/bin" \
XDG_DATA_HOME="$auto/data" \
XDG_STATE_HOME="$auto/state" \
XDG_BIN_HOME="$auto/bin" \
PYTHON_BIN="$fake_python" \
    sh "$root/install.sh" >/dev/null
assert_skill "$auto/home/.codex/skills" lystar-db-ops
assert_skill "$auto/home/.codex/skills" lystar-ssh-ops
assert_skill "$auto/home/.pi/agent/skills" lystar-db-ops
assert_skill "$auto/home/.pi/agent/skills" lystar-ssh-ops
test ! -e "$auto/home/.claude/skills/lystar-db-ops"
test ! -e "$auto/home/.config/opencode/skills/lystar-db-ops"
assert_server_data "$auto/home/.codex/skills" "$auto/home/lystar-server-list"
assert_server_data "$auto/home/.pi/agent/skills" "$auto/home/lystar-server-list"

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
test ! -e "$single/home/.codex/skills/lystar-db-ops"
test ! -e "$single/home/.pi/agent/skills/lystar-db-ops"
test ! -e "$single/home/.config/opencode/skills/lystar-db-ops"

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
for skills_home in "$multi/home/.config/opencode/skills" "$multi/home/.codex/skills" "$multi/home/.pi/agent/skills"; do
    assert_skill "$skills_home" lystar-ssh-ops
    assert_server_data "$skills_home" "$server_home"
done
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
    assert_server_data "$skills_home" "$custom/server-home"
done
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

# 更新器记录了两个 Skill 和原安装目标。
python3 - "$auto/state/lystar-devops-toolkit-skills/installed.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert sorted(state["skills"]) == ["lystar-db-ops", "lystar-ssh-ops"]
assert len(state["skills"]["lystar-db-ops"]["skill_homes"]) == 2
assert state["skills"]["lystar-ssh-ops"]["server_home"].endswith("lystar-server-list")
PY

echo "install tests passed"
