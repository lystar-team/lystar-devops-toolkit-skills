#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/all.txt"
install_runtime_scripts config_store.py result_store.py db_core.py db_ops.py ssh_ops.py
install_runtime_tests
install_command dbx
install_command sshx
install_update_tool
install_regular_skill lystar-db-ops
install_regular_skill lystar-ssh-ops
install_server_data
remove_legacy_skill sql-multi-db-ops
remove_legacy_skill ssh-ops
remove_legacy_skill lystar-server-ops
record_skill_installation lystar-db-ops
record_skill_installation lystar-ssh-ops server-home
verify_command dbx
verify_command sshx
verify_command lystar-skill-update

echo "完整安装完成："
echo "  命令：$bin_home/dbx、$bin_home/sshx、$bin_home/lystar-skill-update"
print_skill_locations lystar-db-ops lystar-ssh-ops
echo "  服务器资料：$server_ops_home"
print_path_notice
