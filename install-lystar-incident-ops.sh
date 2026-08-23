#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-incident-ops.txt"
install_runtime_scripts config_store.py registry_store.py incident_ops.py
install_command incidentx
install_update_tool
install_regular_skill lystar-incident-ops
record_skill_installation lystar-incident-ops
verify_command incidentx
verify_command lystar-skill-update

echo "lystar-incident-ops 安装完成："
echo "  命令：$bin_home/incidentx、$bin_home/lystar-skill-update"
print_skill_locations lystar-incident-ops
print_path_notice
