#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-web-restore.txt"
install_update_tool
install_regular_skill lystar-web-restore
record_skill_installation lystar-web-restore
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-web-restore 安装完成："
print_skill_locations lystar-web-restore
