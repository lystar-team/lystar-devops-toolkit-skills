#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-backup-ops.txt"
install_runtime_scripts config_store.py registry_store.py backup_ops.py
install_command backupx
install_update_tool
install_regular_skill lystar-backup-ops
record_skill_installation lystar-backup-ops
verify_command backupx
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-backup-ops 安装完成："
echo "  命令：$bin_home/backupx、$bin_home/lystar-skill-update、$bin_home/lystar-migrate"
print_skill_locations lystar-backup-ops
print_path_notice
