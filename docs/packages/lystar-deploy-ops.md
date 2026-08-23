# lystar-deploy-ops

面向 OpenCode、Codex、Claude Code、Pi 等 Agent Harness 的版本化与历史目录兼容部署 Skill，附带 `deployx` 命令。

## 安装

`deployx` 依赖已经安装的 `sshx` 和 `hostx`，建议先安装 SSH 与 Host Skill：

```bash
./install-lystar-ssh-ops.sh --harness codex
./install-lystar-host-ops.sh --harness codex
./install-lystar-deploy-ops.sh --harness codex
```

本包安装器不会复制或替换 `sshx`、`hostx`。

## 使用

```bash
deployx plan prod --app web --artifact ./web.tar.gz \
  --release-root /srv/apps --service web.service
deployx status prod --app web --release-root /srv/apps --json
deployx history prod --app web --release-root /srv/apps --limit 10
deployx apply prod --app web --artifact ./web.tar.gz \
  --release-root /srv/apps --service web.service \
  --upload-chunk-size 4194304
deployx rollback prod --app web --release-root /srv/apps \
  --release web-old-111111111111
```

`plan`、`status`、`history` 只读目标主机的发布目录、版本 manifest、可用空间和服务状态。`apply` 默认通过 `sshx put --resume` 以 4 MiB 分片上传 `.tar.gz`，失败重试时从远端 staging 文件继续；暂存成功后清理该文件，上传或暂存失败则保留续传文件并在结果中返回 `resume`/`cleanup` 信息。之后会校验并落盘 release，原子切换 `current`，重启服务并执行 `hostx health`；`rollback` 切换到指定或上一可用 release。失败阶段、自动回退结果和最近一次部署结果都通过 JSON 返回并写入远端 state。不会执行数据库迁移。

默认策略是 `versioned-link`，兼容现有命令和 `<release-root>/<app>/current → releases/<release-id>` 结构。历史 Nginx 静态站点使用 `directory-swap`，线上路径保持不变，Nginx 无需改成指向软链接：

```bash
deployx plan prod --app mochu-admin \
  --strategy directory-swap --service-type nginx-static \
  --live-path /data/mochu_admin \
  --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example \
  --artifact ./mochu-admin.tar.gz \
  --health-check 'https://mochu.admin.example/|status=200|contains=<title>'

deployx apply prod --app mochu-admin \
  --strategy directory-swap --service-type nginx-static \
  --live-path /data/mochu_admin \
  --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example \
  --artifact ./mochu-admin.tar.gz \
  --health-check 'https://mochu.admin.example/|status=200|contains=<title>'

deployx rollback prod --app mochu-admin \
  --strategy directory-swap --service-type nginx-static \
  --live-path /data/mochu_admin \
  --state-root /data/.deployx/mochu_admin \
  --nginx-server-name mochu.admin.example
```

`directory-swap` 在执行前检查制品安全、磁盘空间、同文件系统、部署锁和 Nginx 绑定。发布时将旧目录保留为回退版本，再把新目录移动到相同的 `--live-path`；HTTP 健康检查失败会恢复原目录。状态、manifest 和回退版本保存在独立 `--state-root`。没有 deployx manifest 的历史或人工目录不会被自动清理。

## 注册与新服务 draft

全局注册表位于 `${XDG_CONFIG_HOME:-~/.config}/agent-ops/ops.toml`，独立安装包会携带注册表运行时；旧 `sshx`/`dbx` 配置和密码不变。

```bash
deployx project register mall-admin --name "商城后台" --local-path ./mall-admin
deployx environment register prod --name "生产"
deployx service register mall-admin api --name "API"
deployx recipe list --json
```

`service plan` 只读检查 SSH、systemd、端口、路径、磁盘和制品；`service create` 只在本地注册表登记 `draft` deployment 和创建阶段模型，不上传、安装或重启远端服务：

```bash
deployx service plan mall-admin api prod \
  --ssh-alias prod-api --artifact ./api.tar.gz \
  --release-root /srv/apps --unit mall-api.service \
  --path config=/etc/mall-api --path data=/var/lib/mall-api
deployx service create mall-admin api prod \
  --ssh-alias prod-api --artifact ./api.tar.gz \
  --release-root /srv/apps --unit mall-api.service
```

托管脚本 recipe 使用 `deployx recipe register <id> --stage name=/path/to/script`，文件由 Skill 保存并记录校验和。

既有服务先执行只读 inspect，检查 systemd、进程、端口、路径和注册表 drift：

```bash
deployx service inspect mall-admin api prod \
  --ssh-alias prod-api --release-root /srv/apps \
  --unit mall-api.service --json
```

传统单目录会标记为 `legacy_single_directory`，迁移只生成计划：

```bash
deployx service migrate-plan mall-admin api prod \
  --ssh-alias prod-api --release-root /srv/apps \
  --unit mall-api.service --json
```

用户确认后，`deployx service adopt ... --confirm` 才会在本地注册表保存 `adopted` 基线；它不会执行远端写操作，也不会直接进入 `managed`。

使用 `deployx doctor <project> <service> <environment> --json` 可检查已登记 deployment 的备份覆盖：数据库 source、data/config 路径、备份仓库可用性和最近绑定 manifest 的校验状态。该命令只读全局注册表和本地 manifest，不连接远端、不执行部署或恢复；缺失覆盖返回 `status=missing`。
