#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$root/scripts/install-lib.sh"
parse_install_args "$@"

require_python
install_python_dependencies "$root/requirements/lystar-redis-ops.txt"
install_runtime_scripts config_store.py result_store.py redis_ops.py
install_command redisx
install_update_tool
install_regular_skill lystar-redis-ops
record_skill_installation lystar-redis-ops
verify_command redisx
verify_command lystar-skill-update
verify_command lystar-migrate

echo "lystar-redis-ops 安装完成："
echo "  命令：$bin_home/redisx"
echo "  配置：$config_home/redis-ops.toml"
echo "  预留：$config_home/redis-reservations.toml"
print_skill_locations lystar-redis-ops
print_path_notice
