#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-ui-restore.txt"
install_update_tool
install_regular_skill lystar-ui-restore
record_skill_installation lystar-ui-restore
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-ui-restore 安装完成："
print_skill_locations lystar-ui-restore
