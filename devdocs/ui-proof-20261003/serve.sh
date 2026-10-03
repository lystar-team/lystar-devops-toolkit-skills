#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
port=${1:-8766}
printf '在浏览器打开：http://127.0.0.1:%s/\n' "$port"
exec python3 -m http.server "$port" --bind 127.0.0.1 --directory "$root"
