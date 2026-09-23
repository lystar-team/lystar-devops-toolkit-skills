#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_update_tool
install_regular_skill lystar-ui-design
record_skill_installation lystar-ui-design
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-ui-design 安装完成："
print_skill_locations lystar-ui-design
