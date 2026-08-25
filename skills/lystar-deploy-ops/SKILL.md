---
name: lystar-deploy-ops
description: 通过现有 sshx 和 hostx 编排版本化或历史目录兼容部署、健康检查和可回滚发布。
---

# LYStar Deploy Ops

统一使用 PATH 中的 `deployx`。`plan`、`status`、`history` 是只读命令；`apply` 和 `rollback` 是明确的写操作，分别用于发布制品和切换已有版本。部署不会隐式执行数据库迁移。

`deployx` 依赖已经安装并可用的 `lystar-ssh-ops`（`sshx`）和 `lystar-host-ops`（`hostx`）。它不保存 SSH 凭据，不直接建立 SSH 连接，也不复制主机服务状态采集逻辑。

## 部署策略与目录约定

`deployx` 提供两种正式策略。默认 `versioned-link` 保持原有参数和目录行为，现有调用不需要增加 `--strategy`：

```text
<release-root>/<app>/
├── current -> releases/<release-id>
├── releases/<release-id>/manifest.json
├── deployments/last.json       # 最近一次 apply/rollback 结果
└── deployment.json             # 当前部署规格
```

`directory-swap` 用于 Nginx 静态站点等必须保持历史线上路径不变的项目。它不要求修改 Nginx，也不把线上目录改成软链接：

```text
<live-path>/                     # Nginx 继续使用的原路径
<state-root>/                    # 默认：<live-path 父目录>/.deployx/<live-path 名>
├── releases/<release-id>/
├── staging/
├── failed/
├── manifests/<release-id>.json
├── deployments/last.json
├── deployment.json
└── lock/
```

选择规则：已有 systemd 服务和新项目使用 `versioned-link`；历史 Nginx 静态目录使用 `directory-swap`。不要为了采用 deployx 改写既有 Nginx 路径或强制迁移目录结构。

制品首版固定为可读取的 `.tar.gz`。`release_id` 根据制品文件名和 SHA-256 前 12 位确定，同一个制品重复 apply 时复用已有 release 目录，不产生无法识别的重复版本。

## 命令

```bash
deployx plan prod \
  --app web \
  --artifact ./web.tar.gz \
  --release-root /srv/apps \
  --service web.service

deployx plan prod --app web --artifact ./web.tar.gz \
  --artifact-sha256 <sha256> --release-root /srv/apps \
  --service web.service --health-check facts --health-check disk:/

deployx status prod --app web --release-root /srv/apps
deployx status prod --app web --release-root /srv/apps --service web.service
deployx history prod --app web --release-root /srv/apps --limit 10

deployx apply prod \
  --app web \
  --artifact ./web.tar.gz \
  --release-root /srv/apps \
  --service web.service \
  --health-check facts \
  --health-check disk:/ \
  --keep-releases 5 \
  --upload-chunk-size 4194304

deployx rollback prod \
  --app web \
  --release-root /srv/apps \
  --release <release-id>
```

历史 Nginx 静态目录使用原路径部署：

```bash
deployx plan prod \
  --app mochu-admin \
  --strategy directory-swap \
  --service-type nginx-static \
  --live-path /data/mochu_admin \
  --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example \
  --artifact ./mochu-admin.tar.gz \
  --health-check 'https://mochu.admin.example/|status=200|contains=<title>'

deployx apply prod \
  --app mochu-admin \
  --strategy directory-swap \
  --service-type nginx-static \
  --live-path /data/mochu_admin \
  --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example \
  --artifact ./mochu-admin.tar.gz \
  --health-check 'https://mochu.admin.example/|status=200|contains=<title>'

deployx status prod --app mochu-admin \
  --strategy directory-swap --service-type nginx-static \
  --live-path /data/mochu_admin --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example

deployx rollback prod --app mochu-admin \
  --strategy directory-swap --service-type nginx-static \
  --live-path /data/mochu_admin --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example
```

所有命令支持 `--json`、`--timeout`。JSON 结果包含 `schema_version`、`kind`、`status`、`connection_status`、目标、应用和发布根目录。

`plan` 的状态为 `ready`、`blocked` 或 `unavailable`：制品不存在、格式不符、校验不匹配、目标目录不可用、无法确认可用空间或服务未安装时不会返回可执行的 ready 计划。`status`/`history` 会读取远端 manifest；无效 manifest 会保留 warning，不会伪造版本信息。

`versioned-link` 的 `apply` 阶段依次为上传、`staged`、`switched`、`restarted`、`healthy`；失败会记录 `failed_stage`。制品上传默认通过 `sshx put --resume` 按 4 MiB 分片写入确定性的 staging 路径，上传中断后再次对同一制品执行 `apply` 会从远端已有偏移继续；暂存成功后才清理 staging 文件。上传或暂存失败会保留可续传制品，并在 `resume`、`cleanup` 和 `deployments/last.json` 中记录其位置和状态。切换后服务重启或健康检查失败时，只有存在可验证的上一版本才自动回退，并将结果标为 `rolled_back`；没有回退目标时保留现场并返回失败。`rollback` 未指定 `--release` 时选择按 manifest 时间排序的上一可用版本。

`directory-swap` 在执行前检查 tar 路径安全、磁盘空间、同文件系统、部署锁、`nginx -t`、`server_name` 和 `root`/`alias` 绑定，并要求至少一个 HTTP 健康检查。发布时先把当前线上目录保留为回退版本，再把新目录移动到完全相同的 `--live-path`；验证失败立即恢复原目录。只有带合法 deployx manifest 的 release 才参与保留清理，没有 manifest 的历史或人工目录只报告 warning，绝不自动删除。

`apply`/`rollback` 会通过 `sshx` 写入最小 manifest、`deployment.json` 和 `deployments/last.json`，并按 `--keep-releases` 清理旧版本；当前版本和保护性回退版本始终保留。systemd 主机检查使用 `hostx health`；`directory-swap` 的应用可用性通过显式 HTTP 检查确认。

验证使用 fake `sshx`/`hostx` 和本地临时制品，不连接真实服务器。

## 项目、服务、环境和 recipe 注册

注册表由 Skill 统一保存到 `${LYSTAR_HOME:-$HOME/.lystar}/config/ops.toml`，不改写旧的 `sshx`/`dbx` 配置，也不保存密码。注册命令只写本地注册表：

```bash
deployx project register mall-admin --name "商城后台" --local-path ./mall-admin
deployx environment register prod --name "生产"
deployx service register mall-admin api --name "API" --service-type systemd
deployx project list --json
deployx service list --project-id mall-admin --json
deployx environment list --json
```

内置 `tar.gz-systemd` recipe 直接可用；托管脚本 recipe 会复制到 `${LYSTAR_HOME:-$HOME/.lystar}/data/recipes/` 并保存每个阶段的 SHA-256：

```bash
deployx recipe register mall-script --name "商城脚本" \
  --stage prepare=./ops/prepare.sh --stage verify=./ops/verify.sh
deployx recipe list --json
```

新服务先生成只读前置检查，再登记 draft deployment。`service plan` 会检查制品、SSH、systemd unit、端口、路径和磁盘；`service create` 只把 draft 和 `prepare/install/configure/activate/verify/rollback/managed` 阶段模型写入全局注册表，不上传制品、不写远端目录、不安装或重启服务：

```bash
deployx service plan mall-admin api prod \
  --ssh-alias prod-api --artifact ./api.tar.gz \
  --release-root /srv/apps --unit mall-api.service \
  --path config=/etc/mall-api --path data=/var/lib/mall-api \
  --port tcp:0.0.0.0:18080:http:true

deployx service create mall-admin api prod \
  --ssh-alias prod-api --artifact ./api.tar.gz \
  --release-root /srv/apps --unit mall-api.service
```

## 既有服务接管与 drift

既有服务先通过 `service inspect` 读取注册表和 `hostx service inspect` 的 systemd、进程、端口、路径事实。它会检查已登记的 unit、服务管理器、systemd 基线、路径和端口；不写注册表、不执行远端写操作：

```bash
deployx service inspect mall-admin api prod \
  --ssh-alias prod-api --release-root /srv/apps \
  --unit mall-api.service --json
```

没有 `current` 链接和 `releases` 目录、但目标应用目录已存在时，结果会标记为 `legacy_single_directory`。迁移只生成计划，保留现场，不移动或删除目录：

```bash
deployx service migrate-plan mall-admin api prod \
  --ssh-alias prod-api --release-root /srv/apps \
  --unit mall-api.service --json
```

确认事实后，`service adopt --confirm` 只把 observed systemd、路径、端口和 legacy 状态写入全局注册表，状态为 `adopted`；不会上传制品、写远端目录、重启服务或直接标记为 `managed`。未提供 `--confirm` 时不会写入注册表。既有 `apply` 和 `rollback` 行为保持不变。

## 备份覆盖检查

`deployx doctor <project> <service> <environment>` 读取全局注册表中的 deployment、backup repository 和 backup asset，只检查本地登记事实，不连接远端、不执行部署或恢复：

```bash
deployx doctor mall-admin api prod --json
```

它会检查 deployment 声明的数据库 source 是否有匹配的数据库资产，`data`/`config`/环境文件路径是否有文件资产覆盖，仓库是否可用，以及最近绑定 manifest 是否为 `verified`。没有覆盖返回 `status=missing`，有资产但最近备份尚未校验返回 `partial`；缺失的 deployment 或无法读取仓库事实会明确保留在结果中。`backupx` 仍是备份生成和校验的事实源。
