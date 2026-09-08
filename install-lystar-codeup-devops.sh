#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-codeup-devops.txt"
install_runtime_scripts config_store.py result_store.py codeup_devops.py
install_command codeupx
install_codeup_cli_plugin
install_update_tool
install_regular_skill lystar-codeup-devops
record_skill_installation lystar-codeup-devops
verify_command codeupx
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-codeup-devops 安装完成："
echo "  命令：$bin_home/codeupx"
echo "  配置：$config_home/codeup-devops.toml"
echo "  结果：$state_home/results/<project>/<session>/codeupx-last.json"
print_skill_locations lystar-codeup-devops
print_path_notice
