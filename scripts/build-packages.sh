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
        "$root/runtime/agent-ops/scripts/registry_store.py" \
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
        "$root/runtime/agent-ops/scripts/registry_store.py" \
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

make_host_package() {
    package="$work/lystar-host-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" \
        "$package/skills/lystar-host-ops"
    install -m 0755 "$root/install-lystar-host-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-host-ops.txt" "$package/requirements/lystar-host-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/registry_store.py" \
        "$root/runtime/agent-ops/scripts/host_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/hostx" "$package/bin/hostx"
    install -m 0644 "$root/skills/lystar-host-ops/SKILL.md" "$package/skills/lystar-host-ops/SKILL.md"
    install -m 0644 "$root/docs/packages/lystar-host-ops.md" "$package/README.md"
}

make_deploy_package() {
    package="$work/lystar-deploy-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" \
        "$package/skills/lystar-deploy-ops"
    install -m 0755 "$root/install-lystar-deploy-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-deploy-ops.txt" "$package/requirements/lystar-deploy-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/registry_store.py" \
        "$root/runtime/agent-ops/scripts/deploy_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/deployx" "$package/bin/deployx"
    install -m 0644 "$root/skills/lystar-deploy-ops/SKILL.md" "$package/skills/lystar-deploy-ops/SKILL.md"
    install -m 0644 "$root/docs/packages/lystar-deploy-ops.md" "$package/README.md"
}

make_backup_package() {
    package="$work/lystar-backup-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" \
        "$package/skills/lystar-backup-ops"
    install -m 0755 "$root/install-lystar-backup-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-backup-ops.txt" "$package/requirements/lystar-backup-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/registry_store.py" \
        "$root/runtime/agent-ops/scripts/backup_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/backupx" "$package/bin/backupx"
    install -m 0644 "$root/skills/lystar-backup-ops/SKILL.md" "$package/skills/lystar-backup-ops/SKILL.md"
    install -m 0644 "$root/docs/packages/lystar-backup-ops.md" "$package/README.md"
}

make_incident_package() {
    package="$work/lystar-incident-ops"
    copy_common "$package"
    mkdir -p "$package/runtime/agent-ops/scripts" "$package/requirements" \
        "$package/skills/lystar-incident-ops"
    install -m 0755 "$root/install-lystar-incident-ops.sh" "$package/install.sh"
    install -m 0644 "$root/requirements/lystar-incident-ops.txt" "$package/requirements/lystar-incident-ops.txt"
    install -m 0644 "$root/runtime/agent-ops/scripts/config_store.py" \
        "$root/runtime/agent-ops/scripts/registry_store.py" \
        "$root/runtime/agent-ops/scripts/incident_ops.py" \
        "$package/runtime/agent-ops/scripts/"
    install -m 0755 "$root/bin/incidentx" "$package/bin/incidentx"
    install -m 0644 "$root/skills/lystar-incident-ops/SKILL.md" "$package/skills/lystar-incident-ops/SKILL.md"
    install -m 0644 "$root/docs/packages/lystar-incident-ops.md" "$package/README.md"
}

rm -rf "$output"
mkdir -p "$output"
make_db_package
make_ssh_package
make_host_package
make_deploy_package
make_backup_package
make_incident_package

for name in lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops; do
    (cd "$work/$name" && zip -qr "$output/$name.zip" .)
done

(cd "$root" && zip -qr "$output/lystar-devops-toolkit-skills.zip" \
    VERSION LICENSE README.md CONTRIBUTING.md \
    bin docs requirements runtime scripts skills templates tests \
    install.sh install-lystar-db-ops.sh install-lystar-ssh-ops.sh install-lystar-host-ops.sh install-lystar-deploy-ops.sh install-lystar-backup-ops.sh install-lystar-incident-ops.sh \
    -x '*/__pycache__/*' '*.pyc' 'dist/*' 'runtime/rtk' 'runtime/rtk/*')

(
    cd "$output"
    sha256sum lystar-db-ops.zip lystar-ssh-ops.zip lystar-host-ops.zip lystar-deploy-ops.zip lystar-backup-ops.zip lystar-incident-ops.zip \
        lystar-devops-toolkit-skills.zip >SHA256SUMS
)

echo "安装包已生成：$output"
