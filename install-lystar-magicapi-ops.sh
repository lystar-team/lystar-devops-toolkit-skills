#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-magicapi-ops.txt"
install_runtime_scripts config_store.py result_store.py magicapi_ops.py
install_command magicx
install_update_tool
install_regular_skill lystar-magicapi-ops
record_skill_installation lystar-magicapi-ops
verify_command magicx
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-magicapi-ops 安装完成："
echo "  命令：$bin_home/magicx"
echo "  配置：$config_home/magicapi.toml"
echo "  状态：$state_home/magicapi"
print_skill_locations lystar-magicapi-ops
print_path_notice
