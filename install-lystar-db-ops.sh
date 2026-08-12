#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-db-ops.txt"
install_runtime_scripts config_store.py result_store.py db_core.py db_ops.py
install_command dbx
install_update_tool
install_regular_skill lystar-db-ops
remove_legacy_skill sql-multi-db-ops
record_skill_installation lystar-db-ops
verify_command dbx
verify_command lystar-skill-update

echo "lystar-db-ops 安装完成："
echo "  命令：$bin_home/dbx、$bin_home/lystar-skill-update"
print_skill_locations lystar-db-ops
print_path_notice
