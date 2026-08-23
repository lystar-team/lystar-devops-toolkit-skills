#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-host-ops.txt"
install_runtime_scripts config_store.py registry_store.py host_ops.py
install_command hostx
install_update_tool
install_regular_skill lystar-host-ops
record_skill_installation lystar-host-ops
verify_command hostx
verify_command lystar-skill-update

echo "lystar-host-ops 安装完成："
echo "  命令：$bin_home/hostx、$bin_home/lystar-skill-update"
print_skill_locations lystar-host-ops
print_path_notice
