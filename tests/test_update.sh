#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
temp=$(mktemp -d)
server_pid=
trap 'test -z "$server_pid" || kill "$server_pid" 2>/dev/null || true; rm -rf "$temp"' EXIT HUP INT TERM

# 先安装 0.1.0 到两个自定义 Harness 目录。
home="$temp/home"
state="$home/.lystar/state"
data="$home/.lystar/runtime"
bin="$home/.lystar/bin"
skills_a="$temp/skills-a"
skills_b="$temp/skills-b"
server_home="$temp/server-list"
initial_source="$temp/initial-source"
cp -R "$root" "$initial_source"
rm -rf "$initial_source/.git" "$initial_source/dist"
printf '%s\n' '0.1.0' >"$initial_source/VERSION"
HOME="$home" XDG_STATE_HOME="$state" XDG_DATA_HOME="$data" XDG_BIN_HOME="$bin" \
    sh "$initial_source/install-lystar-ssh-ops.sh" \
        --skills-home "$skills_a" --skills-home "$skills_b" --server-home "$server_home" >/dev/null

test "$(cat "$skills_a/lystar-ssh-ops/VERSION")" = "0.1.0"
printf '%s\n' '用户资料不可覆盖' >"$server_home/README.md"

# 生成本地 0.2.0 Release 资产。
release_root="$temp/release"
source_copy="$temp/source"
cp -R "$root" "$source_copy"
rm -rf "$source_copy/.git" "$source_copy/dist"
printf '%s\n' '0.2.0' >"$source_copy/VERSION"
printf '%s\n' '' '更新测试标记：0.2.0' >>"$source_copy/skills/lystar-ssh-ops/SKILL.md"
sh "$source_copy/scripts/build-packages.sh" >/dev/null
mkdir -p "$release_root/assets"
cp "$source_copy/dist/lystar-ssh-ops.zip" "$release_root/assets/lystar-ssh-ops.zip"
cp "$source_copy/dist/SHA256SUMS" "$release_root/assets/SHA256SUMS"

port_file="$temp/port"
python3 - "$release_root" "$port_file" <<'PY' &
import http.server
import json
import pathlib
import socketserver
import sys

root = pathlib.Path(sys.argv[1])
port_file = pathlib.Path(sys.argv[2])

class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass
    def do_GET(self):
        if self.path == "/release.json":
            port = self.server.server_address[1]
            payload = {
                "tag_name": "v0.2.0",
                "assets": [
                    {"name": "lystar-ssh-ops.zip", "browser_download_url": f"http://127.0.0.1:{port}/assets/lystar-ssh-ops.zip"},
                    {"name": "SHA256SUMS", "browser_download_url": f"http://127.0.0.1:{port}/assets/SHA256SUMS"},
                ],
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

with socketserver.TCPServer(("127.0.0.1", 0), Handler) as server:
    port_file.write_text(str(server.server_address[1]))
    import os
    os.chdir(root)
    server.serve_forever()
PY
server_pid=$!
for _ in $(seq 1 50); do
    test -s "$port_file" && break
    sleep 0.1
done
port=$(cat "$port_file")

HOME="$home" XDG_STATE_HOME="$state" XDG_DATA_HOME="$data" XDG_BIN_HOME="$bin" \
LYSTAR_SKILL_RELEASE_API="http://127.0.0.1:$port/release.json" \
    "$bin/lystar-skill-update" check lystar-ssh-ops >"$temp/check.out"
grep -q '0.1.0 -> 0.2.0 (可更新)' "$temp/check.out"

# 把检查时间调早，由 sshx 启动时的 auto 路径触发更新。
python3 - "$state/update/installed.json" <<'PY'
import json
import sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["skills"]["lystar-ssh-ops"]["last_checked"] = "2000-01-01T00:00:00Z"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY
HOME="$home" XDG_STATE_HOME="$state" XDG_DATA_HOME="$data" XDG_BIN_HOME="$bin" \
LYSTAR_SKILL_RELEASE_API="http://127.0.0.1:$port/release.json" \
    "$bin/sshx" --help >/dev/null

test "$(cat "$skills_a/lystar-ssh-ops/VERSION")" = "0.2.0"
test "$(cat "$skills_b/lystar-ssh-ops/VERSION")" = "0.2.0"
grep -q '更新测试标记：0.2.0' "$skills_a/lystar-ssh-ops/SKILL.md"
grep -q '^用户资料不可覆盖$' "$server_home/README.md"

python3 - "$state/update/installed.json" <<'PY'
import json
import sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
entry = state["skills"]["lystar-ssh-ops"]
assert entry["version"] == "0.2.0"
assert len(entry["skill_homes"]) == 2
assert entry["lystar_home"].endswith("/.lystar")
assert entry["server_home"].endswith("server-list")
PY

# 哈希错误必须拒绝安装。先把本地记录降到 0.1.0，确保会重新下载。
python3 - "$state/update/installed.json" <<'PY'
import json
import sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["skills"]["lystar-ssh-ops"]["version"] = "0.1.0"
with open(path, "w", encoding="utf-8") as handle:
    json.dump(state, handle)
PY
printf '%s\n' 'bad checksum  lystar-ssh-ops.zip' >"$release_root/assets/SHA256SUMS"
set +e
HOME="$home" XDG_STATE_HOME="$state" XDG_DATA_HOME="$data" XDG_BIN_HOME="$bin" \
LYSTAR_SKILL_RELEASE_API="http://127.0.0.1:$port/release.json" \
    "$bin/lystar-skill-update" update lystar-ssh-ops >"$temp/bad.out" 2>"$temp/bad.err"
status=$?
set -e
test "$status" -ne 0
grep -q 'SHA-256 校验失败' "$temp/bad.err"

echo "update tests passed"
