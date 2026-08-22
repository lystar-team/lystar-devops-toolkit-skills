# LYStar DevOps Toolkit Skills

给 AI coding agent 使用的运维 Skill 集合。目前包含两个可独立安装的 Skill：

| Skill | 命令 | 能力 |
| --- | --- | --- |
| `lystar-ssh-ops` | `sshx` | SSH/SFTP、远程命令、长任务、服务器资料维护 |
| `lystar-db-ops` | `dbx` | MySQL、MariaDB、PostgreSQL 数据源发现、查询、执行、导入导出 |

支持 OpenCode、OpenAI Codex、Claude Code、Pi，以及其它兼容 Agent Skills `SKILL.md` 结构的 Harness。两个命令共用本地运行时，但 Skill 可以单独安装、单独更新。

## 环境要求

- Linux、macOS 或其它兼容 Unix 的系统
- Python 3.11 或更高版本
- `pip`
- 安装依赖和检查更新时可访问 GitHub、PyPI
- 构建发布包时需要 `zip`、`unzip`、`sha256sum`

Windows 建议在 WSL 中使用。

## 快速安装

### 安装全部 Skill

```bash
git clone https://github.com/lystar-team/lystar-devops-toolkit-skills.git
cd lystar-devops-toolkit-skills
./install.sh
```

### 单独安装

```bash
./install-lystar-ssh-ops.sh
./install-lystar-db-ops.sh
```

也可以从 GitHub Release 下载独立包：

```bash
curl -fLO https://github.com/lystar-team/lystar-devops-toolkit-skills/releases/latest/download/lystar-ssh-ops.zip
unzip lystar-ssh-ops.zip -d lystar-ssh-ops
cd lystar-ssh-ops
./install.sh
```

数据库包将文件名替换为 `lystar-db-ops.zip`。

## Harness 探测与选择

不传参数时，安装器会检查命令或配置目录，只安装到当前用户已经使用的 Harness：

| Harness | 探测依据 | 默认 Skill 目录 |
| --- | --- | --- |
| OpenCode | `opencode` 或 `~/.config/opencode` | `~/.config/opencode/skills` |
| OpenAI Codex | `codex` 或 `${CODEX_HOME:-~/.codex}` | `${CODEX_HOME:-~/.codex}/skills` |
| Claude Code | `claude` 或 `${CLAUDE_CONFIG_DIR:-~/.claude}` | `${CLAUDE_CONFIG_DIR:-~/.claude}/skills` |
| Pi | `pi` 或 `${PI_CODING_AGENT_DIR:-~/.pi/agent}` | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills` |

只安装到一个 Harness：

```bash
./install.sh --harness codex
```

安装到多个 Harness：

```bash
./install.sh --harness codex,pi
./install.sh --harness opencode,claude,pi
```

忽略探测并安装到全部支持的 Harness：

```bash
./install.sh --harness all
```

安装到任意兼容目录，可重复传入：

```bash
./install.sh \
  --skills-home /path/to/agent-a/skills \
  --skills-home /path/to/agent-b/skills
```

旧环境变量 `SKILLS_HOME` 仍可用，但命令行参数更适合多目录安装。

## 安装位置

默认位置：

- 命令：`${XDG_BIN_HOME:-~/.local/bin}/dbx`、`sshx`、`lystar-skill-update`
- 运行时：`${XDG_DATA_HOME:-~/.local/share}/agent-ops`
- 配置：`${XDG_CONFIG_HOME:-~/.config}/agent-ops`
- 状态与上次结果：`${XDG_STATE_HOME:-~/.local/state}/agent-ops`
- 更新记录：`${XDG_STATE_HOME:-~/.local/state}/lystar-devops-toolkit-skills/installed.json`
- 服务器资料：`${LYSTAR_SERVER_OPS_HOME:-~/lystar-server-list}`

修改服务器资料目录：

```bash
./install-lystar-ssh-ops.sh --server-home /private/server-list
```

安装器会在每个已选 Harness 的 `lystar-ssh-ops/servers` 创建软链接。重复安装只更新 Skill 和程序，不覆盖已有服务器索引、模板或用户资料。

如果命令找不到，把 `~/.local/bin` 加入 `PATH`。

## 自动更新

`dbx` 和 `sshx` 启动时会调用各自 Skill 的更新检查：

1. 每个 Skill 最多每 24 小时请求一次 GitHub Latest Release。
2. 发现更高的语义版本后，下载对应独立 ZIP。
3. 下载并校验 Release 中的 `SHA256SUMS`。
4. 使用首次安装时记录的 Skill 目录、XDG 目录和服务器资料目录重新安装。
5. 检查或更新失败不会阻断当前 `dbx`、`sshx` 命令。

手工检查：

```bash
lystar-skill-update check
lystar-skill-update check lystar-ssh-ops
```

手工更新：

```bash
lystar-skill-update update
lystar-skill-update update lystar-db-ops
```

临时关闭命令启动时的自动更新：

```bash
LYSTAR_SKILL_AUTO_UPDATE=0 sshx status
LYSTAR_SKILL_AUTO_UPDATE=0 dbx sources
```

自动更新依赖 GitHub Release。源码分支上的未发布提交不会自动进入用户环境。

## SSH 使用

```bash
sshx open prod root@example.com:22 'password'
sshx prod "uname -a"
sshx exec --json prod "systemctl status nginx --no-pager"
sshx put prod ./local.conf /etc/example/local.conf
sshx get prod /var/log/example.log ./example.log
sshx run prod "long-running-command"
sshx wait prod <job_id>
```

多级跳板机通过重复 `--jump` 配置。跳板 profile 可以继续引用更前一级跳板：

```bash
sshx open server-1 ops@bastion.example
sshx open server-2 ops@10.0.0.2 --jump server-1
sshx open server-3 root@10.0.0.3 --jump server-2
sshx server-3 "hostname"
```

SSH 建连、认证、守护进程启动、同步远程操作和普通远程命令默认超时为 120 秒：

```bash
sshx open prod root@example.com --timeout 180
sshx exec prod --timeout 300 "slow-command"
sshx run prod --timeout 300 "long-running-command"
```

服务器资料由同一个 `lystar-ssh-ops` Skill 管理。资料入口位于 Skill 的 `servers/`，模板位于 `servers/_templates/`。

## 数据库使用

```bash
dbx sources
dbx use <source>
dbx query "SELECT 1"
dbx query --json "SELECT id, name FROM users LIMIT 20"
dbx exec "CREATE INDEX ..."
dbx import backup.sql
dbx export backup.sql
```

数据库写操作应由用户明确授权，执行后再用只读查询回查。

## 从旧名称迁移

本仓库开源后的正式名称为：

- `ssh-ops` -> `lystar-ssh-ops`
- `sql-multi-db-ops` -> `lystar-db-ops`
- `lystar-server-ops` -> 合并到 `lystar-ssh-ops`

安装新版时，如果旧 Skill 目录只是一个旧 `SKILL.md` 或旧软链接，安装器会自动删除。目录中有其它自定义文件时不会删除，会输出提示供用户人工确认。

原有 `dbx`、`sshx` 配置、连接状态和结果目录保持不变，升级不会迁移或清空用户数据。

## 仓库结构

```text
.
├── skills/                    # 两个 Skill 源文件
├── runtime/agent-ops/         # dbx、sshx Python 运行时与测试
├── bin/                       # 用户命令入口
├── templates/server-list/     # 空服务器资料模板
├── requirements/              # 全量和独立 Skill 依赖
├── scripts/                   # 安装公共库、更新器、打包脚本
├── docs/packages/             # 独立 ZIP 内的 README
├── tests/                     # 安装、打包、自动更新测试
├── .github/workflows/         # CI 与 Release 发布
├── install.sh                 # 全量安装
├── install-lystar-ssh-ops.sh
└── install-lystar-db-ops.sh
```

`dist/` 是构建产物，不提交 Git。运行：

```bash
./scripts/build-packages.sh
```

会生成：

- `dist/lystar-ssh-ops.zip`
- `dist/lystar-db-ops.zip`
- `dist/lystar-devops-toolkit-skills.zip`
- `dist/SHA256SUMS`

## 开发与验证

安装开发依赖：

```bash
python3 -m pip install -r requirements/all.txt
```

运行全部验证：

```bash
python3 -m unittest discover runtime/agent-ops/tests
sh tests/test_install.sh
sh tests/test_packages.sh
sh tests/test_update.sh
```

提交 tag 前，更新 `VERSION`，版本格式使用 `MAJOR.MINOR.PATCH`。推送 `v*` tag 后，GitHub Actions 会重新运行测试、构建 ZIP、生成 SHA-256，并创建 GitHub Release。

## 安全说明

- SSH 密码和数据库密码以明文保存在当前用户的本地配置目录，配置文件权限为 `0600`。
- 服务器资料可能包含明文凭据，只能放在本地受控目录或私有仓库。
- 公开仓库和 Release 不包含用户服务器、地址、密码、私钥、Token、Cookie 或历史结果。
- 自动更新只接受本仓库 GitHub Release 中的固定资产名，并在安装前校验 `SHA256SUMS`。

## License

MIT，见 `LICENSE`。
