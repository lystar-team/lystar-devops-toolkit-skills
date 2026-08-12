#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output="$root/dist"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM

copy_common() {
    package=$1
    mkdir -p "$package/scripts" "$package/bin"
    install -m 0644 "$root/VERSION" "$package/VERSION"
    install -m 0644 "$root/LICENSE" "$package/LICENSE"
    install -m 0644 "$root/scripts/install-lib.sh" "$package/scripts/install-lib.sh"
    install -m 0644 "$root/scripts/skill-update.py" "$package/scripts/skill-update.py"
    install -m 0755 "$root/bin/lystar-skill-update" "$package/bin/lystar-skill-update"
}

make_db_package() {
    package="$work/lystar-db-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" "$package/skills/lystar-db-ops"
    install -m 0755 "$root/install-lystar-db-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-db-ops.txt" "$package/requirements/lystar-db-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/result_store.py" \
        "$root/runtime/agent-ops/scripts/db_core.py" \
        "$root/runtime/agent-ops/scripts/db_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/dbx" "$package/bin/dbx"
    install -m 0644 "$root/skills/lystar-db-ops/SKILL.md" "$package/skills/lystar-db-ops/SKILL.md"
    install -m 0644 "$root/docs/packages/lystar-db-ops.md" "$package/README.md"
}

make_ssh_package() {
    package="$work/lystar-ssh-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" \
        "$package/skills/lystar-ssh-ops" "$package/templates/server-list/_templates"
    install -m 0755 "$root/install-lystar-ssh-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-ssh-ops.txt" "$package/requirements/lystar-ssh-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/result_store.py" \
        "$root/runtime/agent-ops/scripts/ssh_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/sshx" "$package/bin/sshx"
    install -m 0644 "$root/skills/lystar-ssh-ops/SKILL.md" "$package/skills/lystar-ssh-ops/SKILL.md"
    install -m 0644 "$root/templates/server-list/README.md" "$package/templates/server-list/README.md"
    install -m 0644 "$root/templates/server-list/_templates/server.md" "$package/templates/server-list/_templates/server.md"
    install -m 0644 "$root/templates/server-list/_templates/topic.md" "$package/templates/server-list/_templates/topic.md"
    install -m 0644 "$root/docs/packages/lystar-ssh-ops.md" "$package/README.md"
}

rm -rf "$output"
mkdir -p "$output"
make_db_package
make_ssh_package

for name in lystar-db-ops lystar-ssh-ops; do
    (cd "$work/$name" && zip -qr "$output/$name.zip" .)
done

(cd "$root" && zip -qr "$output/lystar-devops-toolkit-skills.zip" \
    VERSION LICENSE README.md CONTRIBUTING.md \
    bin docs requirements runtime scripts skills templates tests \
    install.sh install-lystar-db-ops.sh install-lystar-ssh-ops.sh \
    -x '*/__pycache__/*' '*.pyc' 'dist/*')

(
    cd "$output"
    sha256sum lystar-db-ops.zip lystar-ssh-ops.zip lystar-devops-toolkit-skills.zip >SHA256SUMS
)

echo "安装包已生成：$output"
