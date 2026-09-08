#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/all.txt"
install_runtime_scripts config_store.py registry_store.py result_store.py db_core.py db_ops.py ssh_ops.py host_ops.py deploy_ops.py backup_ops.py incident_ops.py codeup_devops.py redis_ops.py magicapi_ops.py
install_runtime_tests
install_command dbx
install_command sshx
install_command hostx
install_command deployx
install_command backupx
install_command incidentx
install_command codeupx
install_command redisx
install_command magicx
install_codeup_cli_plugin
install_update_tool
install_regular_skill lystar-db-ops
install_regular_skill lystar-ssh-ops
install_regular_skill lystar-host-ops
install_regular_skill lystar-deploy-ops
install_regular_skill lystar-backup-ops
install_regular_skill lystar-incident-ops
install_regular_skill lystar-codeup-devops
install_regular_skill lystar-redis-ops
install_regular_skill lystar-magicapi-ops
install_server_data
remove_legacy_server_links
remove_legacy_skill sql-multi-db-ops
remove_legacy_skill ssh-ops
remove_legacy_skill lystar-server-ops
record_skill_installation lystar-db-ops
record_skill_installation lystar-ssh-ops server-home
record_skill_installation lystar-host-ops
record_skill_installation lystar-deploy-ops
record_skill_installation lystar-backup-ops
record_skill_installation lystar-incident-ops
record_skill_installation lystar-codeup-devops
record_skill_installation lystar-redis-ops
record_skill_installation lystar-magicapi-ops
verify_command dbx
verify_command sshx
verify_command hostx
verify_command deployx
verify_command backupx
verify_command incidentx
verify_command codeupx
verify_command redisx
verify_command magicx
verify_command lystar-skill-update
verify_command lystar-migrate

echo "完整安装完成："
echo "  命令：$bin_home/dbx、$bin_home/sshx、$bin_home/hostx、$bin_home/deployx、$bin_home/backupx、$bin_home/incidentx、$bin_home/codeupx、$bin_home/redisx、$bin_home/magicx、$bin_home/lystar-skill-update、$bin_home/lystar-migrate"
print_skill_locations lystar-db-ops lystar-ssh-ops lystar-host-ops lystar-deploy-ops lystar-backup-ops lystar-incident-ops lystar-codeup-devops lystar-redis-ops lystar-magicapi-ops
echo "  服务器资料：$server_ops_home"
print_path_notice
