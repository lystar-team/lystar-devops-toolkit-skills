# LYStar DevOps Toolkit Skills

给 AI coding agent 使用的运维 Skill 集合。目前包含六个可独立安装的 Skill：

| Skill | 命令 | 能力 |
| --- | --- | --- |
| `lystar-ssh-ops` | `sshx` | SSH/SFTP、远程命令、长任务、任务取消/跟随、本地端口转发、服务器资料维护 |
| `lystar-db-ops` | `dbx` | MySQL、MariaDB、PostgreSQL 数据源发现、版本探测、事务控制、查询、执行、导入导出 |
| `lystar-host-ops` | `hostx` | 主机事实、服务、进程、日志、监听端口、磁盘和只读健康检查 |
| `lystar-deploy-ops` | `deployx` | 版本化部署、健康检查、状态、历史和可回滚发布 |
| `lystar-backup-ops` | `backupx` | 本地数据库/文件备份、校验、恢复、恢复验证和保留清理 |
| `lystar-incident-ops` | `incidentx` | 只读事件证据、可校验诊断包和离线时间线 |

支持 OpenCode、OpenAI Codex、Claude Code、Pi，以及其它兼容 Agent Skills `SKILL.md` 结构的 Harness。六个命令共用本地安装和更新机制，但 Skill 可以单独安装、单独更新。

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
./install-lystar-host-ops.sh
./install-lystar-deploy-ops.sh
./install-lystar-backup-ops.sh
./install-lystar-incident-ops.sh
```

也可以从 GitHub Release 下载独立包：

```bash
curl -fLO https://github.com/lystar-team/lystar-devops-toolkit-skills/releases/latest/download/lystar-ssh-ops.zip
unzip lystar-ssh-ops.zip -d lystar-ssh-ops
cd lystar-ssh-ops
./install.sh
```

数据库包、主机包、部署包、备份包和事件包分别将文件名替换为 `lystar-db-ops.zip`、`lystar-host-ops.zip`、`lystar-deploy-ops.zip`、`lystar-backup-ops.zip`、`lystar-incident-ops.zip`。

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

- 命令：`${XDG_BIN_HOME:-~/.local/bin}/dbx`、`sshx`、`hostx`、`deployx`、`backupx`、`incidentx`、`lystar-skill-update`
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

`dbx`、`sshx`、`hostx`、`deployx`、`backupx` 和 `incidentx` 启动时会调用各自 Skill 的更新检查：

1. 每个 Skill 最多每 24 小时请求一次 GitHub Latest Release。
2. 发现更高的语义版本后，下载对应独立 ZIP。
3. 下载并校验 Release 中的 `SHA256SUMS`。
4. 使用首次安装时记录的 Skill 目录、XDG 目录和服务器资料目录重新安装。
5. 检查或更新失败不会阻断当前 `dbx`、`sshx`、`hostx`、`deployx`、`backupx`、`incidentx` 命令。

手工检查：

```bash
lystar-skill-update check
lystar-skill-update check lystar-ssh-ops
```

手工更新：

```bash
lystar-skill-update update
lystar-skill-update update lystar-db-ops
lystar-skill-update check lystar-host-ops
```

临时关闭命令启动时的自动更新：

```bash
LYSTAR_SKILL_AUTO_UPDATE=0 sshx status
LYSTAR_SKILL_AUTO_UPDATE=0 dbx sources
LYSTAR_SKILL_AUTO_UPDATE=0 hostx facts prod
LYSTAR_SKILL_AUTO_UPDATE=0 deployx status prod --app web --release-root /srv/apps
LYSTAR_SKILL_AUTO_UPDATE=0 backupx list --repository ./backups
LYSTAR_SKILL_AUTO_UPDATE=0 incidentx show ./incident-bundles/case-20260823
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
sshx jobs prod
sshx cancel prod <job_id>
sshx wait prod <job_id>
sshx wait prod <job_id> --follow
sshx forward open prod 15432 db.internal 5432
sshx forward list prod
sshx forward close prod <forward_id>
sshx forget prod --json
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

`sshx forget` 会先检查全局注册表的 deployment、service 和 backup asset 影响。同一 profile 仍有其它 alias 时，引用会同步到存活 alias；删除最后一个 alias/profile 时默认阻断，只有 `sshx forget <alias> --confirm` 才会把 deployment/backup asset 标记为 `orphaned`。不会删除远端服务、目录、备份文件或 SSH 密码。

## 主机使用

`hostx` 依赖已安装的 `sshx`，只通过现有 SSH profile 和跳板链执行远端只读命令：

```bash
hostx facts prod
hostx service prod status nginx
hostx service prod inspect nginx.service
hostx process prod list --pattern python
hostx logs prod nginx --lines 100
hostx ports prod
hostx disk prod /
hostx health prod --check facts,disk:/,ports
hostx facts prod --json
```

`service inspect` 是只读的服务接管基线，会返回 systemd unit/drop-in、启动命令、依赖、主进程、按服务过滤的监听端口，以及从 unit、启动命令和 systemd 目录属性中观察到的 release/config/data/log 路径；无法确认的路径不会伪造。默认 `health` 只检查基础事实和根文件系统。`connection_status=unavailable` 表示 SSH/sshx 不可用，不等同于主机不健康；服务、进程、日志和端口能力缺失会保留明确的 `unknown` 状态。

## 部署使用

`deployx` 通过现有 `sshx` 和 `hostx` 编排版本化发布。`plan`、`status`、`history` 保持只读；明确执行 `apply` 或 `rollback` 才会上传制品、切换版本、重启服务和执行健康检查：

```bash
deployx plan prod --app web --artifact ./web.tar.gz \
  --release-root /srv/apps --service web.service
deployx status prod --app web --release-root /srv/apps --json
deployx history prod --app web --release-root /srv/apps --limit 10
deployx apply prod --app web --artifact ./web.tar.gz \
  --release-root /srv/apps --service web.service
deployx rollback prod --app web --release-root /srv/apps
```

`apply` 的制品上传默认使用 `sshx put --resume` 和 4 MiB 分片。上传或暂存中断后，下一次对同一制品执行 `apply` 会复用确定性的 staging 文件继续传输；暂存成功后自动清理，失败状态会把续传位置写入 `resume`、`cleanup` 和远端 `deployments/last.json`。

`--release-root` 必须是显式绝对远端目录，应用版本目录为 `<release-root>/<app>/releases/<release-id>`，首版制品格式固定为 `.tar.gz`。`apply` 和 `rollback` 的 JSON 结果记录 `staged`、`switched`、`restarted`、`healthy`、`failed` 或 `rolled_back` 阶段；健康失败只在有已验证上一版本时自动回退。

`deployx` 也统一托管项目、服务、环境和 recipe 注册信息。注册表位于 `${XDG_CONFIG_HOME:-~/.config}/agent-ops/ops.toml`，不改写旧 SSH/数据库配置：

```bash
deployx project register mall-admin --name "商城后台" --local-path ./mall-admin
deployx environment register prod --name "生产"
deployx service register mall-admin api --name "API"
deployx service plan mall-admin api prod --ssh-alias prod-api \
  --artifact ./api.tar.gz --release-root /srv/apps --unit mall-api.service
deployx service create mall-admin api prod --ssh-alias prod-api \
  --artifact ./api.tar.gz --release-root /srv/apps --unit mall-api.service
```

`service plan` 只读检查目标主机；`service create` 只登记 draft deployment 和后续创建阶段，不执行远端上传、目录创建、unit 安装、启动或重启。既有 `apply`/`rollback` 不受影响。

既有服务使用 `deployx service inspect` 读取 systemd、进程、端口、路径和注册表 drift。目标应用目录存在但没有 `current` 链接和 `releases` 目录时，会标记为 `legacy_single_directory`；`service migrate-plan` 只生成迁移计划，不移动或删除现场。用户明确提供 `service adopt --confirm` 后，Skill 才会把观察到的基线写入全局注册表并标记为 `adopted`，不会执行远端写操作或直接标记为 `managed`。

`deployx doctor` 只读检查 deployment 的备份覆盖，不连接远端：

```bash
deployx doctor mall-admin api prod --json
```

它会报告数据库 source、data/config 路径、备份仓库和最近绑定 manifest 的覆盖/校验状态；缺失覆盖返回 `status=missing`，备份资产存在但最近 manifest 尚未校验返回 `partial`。

## 备份使用

`backupx` 管理本地备份仓库，数据库路径调用 `dbx`，文件路径调用 `sshx`；它不会自动选择恢复目标：

```bash
backupx db create --source prod --repository ./backups
backupx file create prod /srv/web/uploads --repository ./backups
backupx list --repository ./backups --json
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups --read-only
backupx restore db-20260823T120000Z-ab12cd34 --repository ./backups --source staging
backupx restore file-20260823T120000Z-ab12cd34 --repository ./backups \
  --alias staging --target-dir /srv/web/uploads
backupx restore-verify db-20260823T120000Z-ab12cd34 --repository ./backups \
  --source staging --sql 'SELECT COUNT(*) FROM users'
backupx prune --repository ./backups --keep-last 5 --keep-days 30 --dry-run
```

数据库恢复固定使用 `dbx import --transaction commit`；文件恢复先上传 tar.gz，再解包到明确的绝对远端目录。`verify` 校验文件、大小、SHA-256 和文件归档可读性；`verify --read-only` 只校验、不更新 manifest 状态；`prune` 默认 dry-run，只有显式 `--confirm` 才删除。

备份仓库和资产由全局注册表统一托管。登记默认仓库后，备份命令可以省略 `--repository`；资产必须绑定项目、服务和环境，带资产创建会把绑定写入 manifest：

```bash
backupx repository register local-prod --path /var/lib/agent-ops/backups --default
backupx asset register mall-db --kind db --project mall-admin --service api \
  --environment prod --repository local-prod --source prod-db
backupx asset register mall-files --kind file --project mall-admin --service api \
  --environment prod --repository local-prod --alias prod-api --remote-path /srv/web/uploads
backupx db create --asset-id mall-db
backupx file create --asset-id mall-files
```

旧的显式 `--repository`、旧配置、旧 manifest 和密码链路继续兼容；注册表只保存对象引用，不复制凭据。

## 事件诊断使用

`incidentx` 按明确范围组合现有 Skill 的只读 JSON 结果，并可读取全局注册表对象，生成可脱离现场查看的诊断包：

```bash
incidentx collect case-20260823 \
  --out ./incident-bundles/case-20260823 \
  --host prod \
  --source prod-db \
  --app web \
  --release-root /srv/apps \
  --service web.service \
  --project-id mall-admin \
  --service-id api \
  --deployment-id mall-admin/api/prod \
  --backup-asset-id mall-db \
  --log-source nginx \
  --since '2 hours ago' \
  --require host,db,deploy,registry
incidentx show ./incident-bundles/case-20260823
incidentx timeline ./incident-bundles/case-20260823
incidentx verify ./incident-bundles/case-20260823
```

`collect` 不执行服务重启、发布切换、备份恢复、保留清理或 SQL 写操作。提供 project/service/deployment/backup asset ID 时，会只读采集全局注册表对象、关系和 revision，并把 revision 写入证据 manifest/timeline。时间线同时保存原始 `event_time`、采集 `observed_at` 和时间依据；缺少原始事件时间时不会把采集时间伪装成业务发生时间。

## 数据库使用

```bash
dbx sources
dbx use <source>
dbx query "SELECT 1"
dbx query --json "SELECT id, name FROM users LIMIT 20"
dbx exec "CREATE INDEX ..." --transaction commit
dbx import backup.sql --transaction commit
dbx export backup.sql
dbx source remove prod --json
```

`sshx` 的 `jobs`/`cancel`/`wait --follow` 复用远端长任务目录，`forward` 复用已有 SSH daemon 和多级跳板链。`dbx` 统一返回 `db_type`、版本与连接状态；`exec`、`import` 默认提交，使用 `--transaction rollback` 可在当前单次命令中执行后回滚，不建立跨命令持久事务会话。

`dbx source remove` 会先检查 deployment、service、backup asset 和项目默认 source/binding。profile 仍有其它 alias 时，数据库引用会同步到存活 alias；删除最后一个 alias/profile 时默认阻断，只有显式 `--confirm` 才会标记依赖为 `orphaned` 并清理失效项目绑定。旧数据库配置、密码和备份文件保持不变。

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
├── skills/                    # 六个 Skill 源文件
├── runtime/agent-ops/         # 六个命令的 Python 运行时与测试
├── bin/                       # 用户命令入口
├── templates/server-list/     # 空服务器资料模板
├── requirements/              # 全量和独立 Skill 依赖
├── scripts/                   # 安装公共库、更新器、打包脚本
├── docs/packages/             # 独立 ZIP 内的 README
├── tests/                     # 安装、打包、自动更新测试
├── .github/workflows/         # CI 与 Release 发布
├── install.sh                 # 全量安装
├── install-lystar-ssh-ops.sh
├── install-lystar-db-ops.sh
├── install-lystar-host-ops.sh
├── install-lystar-deploy-ops.sh
├── install-lystar-backup-ops.sh
└── install-lystar-incident-ops.sh
```

`dist/` 是构建产物，不提交 Git。运行：

```bash
./scripts/build-packages.sh
```

会生成：

- `dist/lystar-ssh-ops.zip`
- `dist/lystar-db-ops.zip`
- `dist/lystar-host-ops.zip`
- `dist/lystar-deploy-ops.zip`
- `dist/lystar-backup-ops.zip`
- `dist/lystar-incident-ops.zip`
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
