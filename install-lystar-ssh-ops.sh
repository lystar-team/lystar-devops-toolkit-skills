#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-ssh-ops.txt"
install_runtime_scripts config_store.py registry_store.py result_store.py ssh_ops.py
install_command sshx
install_update_tool
install_regular_skill lystar-ssh-ops
install_server_data
remove_legacy_skill ssh-ops
remove_legacy_skill lystar-server-ops
record_skill_installation lystar-ssh-ops server-home
verify_command sshx
verify_command lystar-skill-update

echo "lystar-ssh-ops 安装完成："
echo "  命令：$bin_home/sshx、$bin_home/lystar-skill-update"
print_skill_locations lystar-ssh-ops
echo "  服务器资料：$server_ops_home"
print_path_notice
