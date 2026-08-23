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

for archive in lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops lystar-devops-toolkit-skills; do
    test -f "$root/dist/$archive.zip"
    unzip -tq "$root/dist/$archive.zip" >/dev/null
done
test -f "$root/dist/SHA256SUMS"
(cd "$root/dist" && sha256sum -c SHA256SUMS >/dev/null)

for archive in lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops; do
    package="$temp/$archive"
    mkdir -p "$package"
    unzip -q "$root/dist/$archive.zip" -d "$package"
    test -x "$package/install.sh"
    test -f "$package/runtime/agent-ops/scripts/config_store.py"
    test -f "$package/runtime/agent-ops/scripts/registry_store.py"
    HOME="$temp/home-$archive" \
    XDG_DATA_HOME="$temp/data-$archive" \
    XDG_STATE_HOME="$temp/state-$archive" \
    XDG_BIN_HOME="$temp/bin-$archive" \
    PYTHON_BIN="$fake_python" \
        sh "$package/install.sh" --harness codex >/dev/null
done

test -f "$temp/home-lystar-db-ops/.codex/skills/lystar-db-ops/SKILL.md"
test -x "$temp/bin-lystar-db-ops/dbx"
test ! -e "$temp/bin-lystar-db-ops/sshx"
test -f "$temp/home-lystar-ssh-ops/.codex/skills/lystar-ssh-ops/SKILL.md"
test -L "$temp/home-lystar-ssh-ops/.codex/skills/lystar-ssh-ops/servers"
test -x "$temp/bin-lystar-ssh-ops/sshx"
test ! -e "$temp/bin-lystar-ssh-ops/dbx"
test -f "$temp/home-lystar-host-ops/.codex/skills/lystar-host-ops/SKILL.md"
test -x "$temp/bin-lystar-host-ops/hostx"
test ! -e "$temp/bin-lystar-host-ops/sshx"
test -f "$temp/home-lystar-deploy-ops/.codex/skills/lystar-deploy-ops/SKILL.md"
test -x "$temp/bin-lystar-deploy-ops/deployx"
test ! -e "$temp/bin-lystar-deploy-ops/sshx"
test ! -e "$temp/bin-lystar-deploy-ops/hostx"
test -f "$temp/home-lystar-backup-ops/.codex/skills/lystar-backup-ops/SKILL.md"
test -x "$temp/bin-lystar-backup-ops/backupx"
test ! -e "$temp/bin-lystar-backup-ops/dbx"
test ! -e "$temp/bin-lystar-backup-ops/sshx"
test -f "$temp/home-lystar-incident-ops/.codex/skills/lystar-incident-ops/SKILL.md"
test -x "$temp/bin-lystar-incident-ops/incidentx"
test ! -e "$temp/bin-lystar-incident-ops/hostx"
test ! -e "$temp/bin-lystar-incident-ops/dbx"

complete="$temp/complete"
mkdir -p "$complete"
unzip -q "$root/dist/lystar-devops-toolkit-skills.zip" -d "$complete"
HOME="$temp/home-complete" \
XDG_DATA_HOME="$temp/data-complete" \
XDG_STATE_HOME="$temp/state-complete" \
XDG_BIN_HOME="$temp/bin-complete" \
PYTHON_BIN="$fake_python" \
    sh "$complete/install.sh" --harness codex,pi >/dev/null
for command in dbx sshx hostx deployx backupx incidentx lystar-skill-update; do
    test -x "$temp/bin-complete/$command"
done
for skills_home in "$temp/home-complete/.codex/skills" "$temp/home-complete/.pi/agent/skills"; do
    test -f "$skills_home/lystar-db-ops/SKILL.md"
    test -f "$skills_home/lystar-ssh-ops/SKILL.md"
    test -f "$skills_home/lystar-host-ops/SKILL.md"
    test -f "$skills_home/lystar-deploy-ops/SKILL.md"
    test -f "$skills_home/lystar-backup-ops/SKILL.md"
    test -f "$skills_home/lystar-incident-ops/SKILL.md"
    test -L "$skills_home/lystar-ssh-ops/servers"
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
