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
if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
    exit 0
fi
exec "$REAL_PYTHON" "$@"
EOF
chmod +x "$fake_python"

sh "$root/scripts/build-packages.sh" >/dev/null

for archive in lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops lystar-codeup-devops lystar-redis-ops lystar-magicapi-ops lystar-devops-toolkit-skills; do
    test -f "$root/dist/$archive.zip"
    unzip -tq "$root/dist/$archive.zip" >/dev/null
done
test -f "$root/dist/SHA256SUMS"
(cd "$root/dist" && sha256sum -c SHA256SUMS >/dev/null)

for archive in lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops lystar-codeup-devops lystar-redis-ops lystar-magicapi-ops; do
    package="$temp/$archive"
    mkdir -p "$package"
    unzip -q "$root/dist/$archive.zip" -d "$package"
    test -x "$package/install.sh"
    test -f "$package/runtime/agent-ops/scripts/config_store.py"
    if [ "$archive" != "lystar-codeup-devops" ] && [ "$archive" != "lystar-redis-ops" ] && [ "$archive" != "lystar-magicapi-ops" ]; then
        test -f "$package/runtime/agent-ops/scripts/registry_store.py"
    fi
    test -f "$package/runtime/agent-ops/scripts/paths.py"
    test -f "$package/runtime/agent-ops/scripts/migrate.py"
    test -x "$package/bin/lystar-migrate"
    if [ "$archive" = "lystar-codeup-devops" ]; then
        test -f "$package/runtime/agent-ops/scripts/codeup_devops.py"
        test -x "$package/bin/codeupx"
    fi
    if [ "$archive" = "lystar-redis-ops" ]; then
        test -f "$package/runtime/agent-ops/scripts/redis_ops.py"
        test -x "$package/bin/redisx"
    fi
    if [ "$archive" = "lystar-magicapi-ops" ]; then
        test -f "$package/runtime/agent-ops/scripts/magicapi_ops.py"
        test -x "$package/bin/magicx"
        test -f "$package/skills/lystar-magicapi-ops/references/magicapi-contract.md"
    fi
    HOME="$temp/home-$archive" \
    XDG_DATA_HOME="$temp/data-$archive" \
    XDG_STATE_HOME="$temp/state-$archive" \
    XDG_BIN_HOME="$temp/bin-$archive" \
    PYTHON_BIN="$fake_python" \
        sh "$package/install.sh" --harness codex >/dev/null
done

test -f "$temp/home-lystar-db-ops/.agents/skills/lystar-db-ops/SKILL.md"
test -x "$temp/home-lystar-db-ops/.lystar/bin/dbx"
test ! -e "$temp/home-lystar-db-ops/.lystar/bin/sshx"
test -f "$temp/home-lystar-ssh-ops/.agents/skills/lystar-ssh-ops/SKILL.md"
test -d "$temp/home-lystar-ssh-ops/.lystar/servers"
test ! -e "$temp/home-lystar-ssh-ops/.agents/skills/lystar-ssh-ops/servers"
test -x "$temp/home-lystar-ssh-ops/.lystar/bin/sshx"
test ! -e "$temp/home-lystar-ssh-ops/.lystar/bin/dbx"
test -f "$temp/home-lystar-host-ops/.agents/skills/lystar-host-ops/SKILL.md"
test -x "$temp/home-lystar-host-ops/.lystar/bin/hostx"
test ! -e "$temp/home-lystar-host-ops/.lystar/bin/sshx"
test -f "$temp/home-lystar-deploy-ops/.agents/skills/lystar-deploy-ops/SKILL.md"
test -x "$temp/home-lystar-deploy-ops/.lystar/bin/deployx"
test ! -e "$temp/home-lystar-deploy-ops/.lystar/bin/sshx"
test ! -e "$temp/home-lystar-deploy-ops/.lystar/bin/hostx"
test -f "$temp/home-lystar-backup-ops/.agents/skills/lystar-backup-ops/SKILL.md"
test -x "$temp/home-lystar-backup-ops/.lystar/bin/backupx"
test ! -e "$temp/home-lystar-backup-ops/.lystar/bin/dbx"
test ! -e "$temp/home-lystar-backup-ops/.lystar/bin/sshx"
test -f "$temp/home-lystar-incident-ops/.agents/skills/lystar-incident-ops/SKILL.md"
test -x "$temp/home-lystar-incident-ops/.lystar/bin/incidentx"
test ! -e "$temp/home-lystar-incident-ops/.lystar/bin/hostx"
test ! -e "$temp/home-lystar-incident-ops/.lystar/bin/dbx"
test -f "$temp/home-lystar-codeup-devops/.agents/skills/lystar-codeup-devops/SKILL.md"
test -x "$temp/home-lystar-codeup-devops/.lystar/bin/codeupx"
test ! -e "$temp/home-lystar-codeup-devops/.lystar/bin/deployx"
test -f "$temp/home-lystar-redis-ops/.agents/skills/lystar-redis-ops/SKILL.md"
test -x "$temp/home-lystar-redis-ops/.lystar/bin/redisx"
test ! -e "$temp/home-lystar-redis-ops/.lystar/bin/deployx"
test -f "$temp/home-lystar-magicapi-ops/.agents/skills/lystar-magicapi-ops/SKILL.md"
test -x "$temp/home-lystar-magicapi-ops/.lystar/bin/magicx"
test -f "$temp/home-lystar-magicapi-ops/.lystar/runtime/scripts/magicapi_ops.py"
complete="$temp/complete"
mkdir -p "$complete"
unzip -q "$root/dist/lystar-devops-toolkit-skills.zip" -d "$complete"
HOME="$temp/home-complete" \
XDG_DATA_HOME="$temp/data-complete" \
XDG_STATE_HOME="$temp/state-complete" \
XDG_BIN_HOME="$temp/bin-complete" \
PYTHON_BIN="$fake_python" \
    sh "$complete/install.sh" --harness codex,pi >/dev/null
for command in dbx sshx hostx deployx backupx incidentx codeupx redisx magicx lystar-skill-update; do
    test -x "$temp/home-complete/.lystar/bin/$command"
done
test -x "$temp/home-complete/.lystar/bin/lystar-migrate"
for skills_home in "$temp/home-complete/.agents/skills"; do
    test -f "$skills_home/lystar-db-ops/SKILL.md"
    test -f "$skills_home/lystar-ssh-ops/SKILL.md"
    test -f "$skills_home/lystar-host-ops/SKILL.md"
    test -f "$skills_home/lystar-deploy-ops/SKILL.md"
    test -f "$skills_home/lystar-backup-ops/SKILL.md"
    test -f "$skills_home/lystar-incident-ops/SKILL.md"
    test -f "$skills_home/lystar-codeup-devops/SKILL.md"
    test -f "$skills_home/lystar-redis-ops/SKILL.md"
    test -f "$skills_home/lystar-magicapi-ops/SKILL.md"
    test ! -e "$skills_home/lystar-ssh-ops/servers"
done

for archive in "$root"/dist/*.zip; do
    if unzip -Z1 "$archive" | rg -q '__pycache__|\.pyc$|\.DS_Store$'; then
        echo "$(basename "$archive") 包含缓存或系统文件" >&2
        exit 1
    fi
    if unzip -Z1 "$archive" | rg -q '^runtime/rtk(/|$)'; then
        echo "$(basename "$archive") 包含 RTK 本地缓存" >&2
        exit 1
    fi
    private_markers='/home/'"yean"'|changsha'"-"'|xingma'"cheng"'|dmit'"-us"
    if unzip -p "$archive" | rg -q "$private_markers"; then
        echo "$(basename "$archive") 包含私人服务器资料" >&2
        exit 1
    fi
done

if unzip -Z1 "$root/dist/lystar-ssh-ops.zip" | rg -q 'lystar-server-ops|skills/ssh-ops'; then
    echo "SSH 包仍包含旧 Skill" >&2
    exit 1
fi

echo "package tests passed"
