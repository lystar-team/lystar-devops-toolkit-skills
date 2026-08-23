#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-deploy-ops.txt"
install_runtime_scripts config_store.py registry_store.py deploy_ops.py
install_command deployx
install_update_tool
install_regular_skill lystar-deploy-ops
record_skill_installation lystar-deploy-ops
verify_command deployx
verify_command lystar-skill-update

echo "lystar-deploy-ops 安装完成："
echo "  命令：$bin_home/deployx、$bin_home/lystar-skill-update"
print_skill_locations lystar-deploy-ops
print_path_notice
