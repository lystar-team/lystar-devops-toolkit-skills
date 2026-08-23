---
name: lystar-backup-ops
description: 通过现有 dbx 和 sshx 管理本地数据库与文件备份、校验、恢复和保留清理。
---

# LYStar Backup Ops

统一使用 PATH 中的 `backupx`。它只管理本地备份仓库和备份生命周期，不复制 `dbx` 的数据库驱动、不复制 `sshx` 的连接管理，也不把备份成功等同于恢复成功。

## 依赖与仓库布局

- 数据库备份/恢复依赖已经安装并可用的 `dbx`。
- 文件备份/恢复依赖已经安装并可用的 `sshx`。
- `restore-verify --health-check` 依赖已经安装并可用的 `hostx`。
- 首版只使用本地目录，不实现对象存储、跨地域复制、增量块存储或凭据托管。

仓库中每个备份由同名数据文件和 `*.manifest.json` 组成：数据库为 `.sql`，文件为 `.tar.gz`。manifest 使用临时文件和原子替换写入；`path` 默认是相对仓库的文件名。

## 命令

```bash
backupx db create --source prod --repository ./backups
backupx file create prod /srv/web/uploads --repository ./backups

backupx list --repository ./backups --kind db --json
backupx inspect db-20260823T120000Z-ab12cd34 --repository ./backups
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups
backupx verify db-20260823T120000Z-ab12cd34 --repository ./backups --read-only

backupx restore db-20260823T120000Z-ab12cd34 \
  --repository ./backups --source staging
backupx restore file-20260823T120000Z-ab12cd34 \
  --repository ./backups --alias staging --target-dir /srv/web/uploads

backupx restore-verify db-20260823T120000Z-ab12cd34 \
  --repository ./backups --source staging --sql 'SELECT COUNT(*) FROM users'
backupx restore-verify file-20260823T120000Z-ab12cd34 \
  --repository ./backups --alias staging --target-dir /srv/web/uploads \
  --check-path avatar.png --health-check facts

backupx prune --repository ./backups --keep-last 5 --keep-days 30 --dry-run
backupx prune --repository ./backups --keep-last 5 --keep-days 30 --confirm
```

所有命令支持 `--json`。数据库恢复固定调用 `dbx import --transaction commit`，必须显式提供目标 `--source`；文件恢复必须显式提供 SSH profile 和绝对 `--target-dir`。不会从 manifest 自动选择生产目标。

`verify` 检查文件存在、大小、SHA-256；文件备份额外检查 tar.gz 可读性和文件数量。默认校验后更新 manifest 状态；`--read-only` 只返回校验结果，不修改 manifest，供 `incidentx` 等只读采集器使用。`restore-verify` 只运行用户明确提供的只读 SQL、文件路径检查或主机健康检查；没有提供检查时返回 `not_requested`。

`prune` 默认只列出待清理项。只有显式传入 `--confirm` 才会删除数据文件和对应 manifest；`--keep-last` 和 `--keep-days` 按备份类型分别计算保留结果。

## 仓库和备份资产登记

备份仓库和资产由全局注册表统一托管：`${XDG_CONFIG_HOME:-~/.config}/agent-ops/ops.toml`。仓库路径必须登记为绝对路径；显式传入 `--repository` 的旧命令仍然可用，省略时使用唯一登记的默认仓库：

```bash
backupx repository register local-prod \
  --path /var/lib/agent-ops/backups --default
backupx repository list --json
backupx repository set-default local-prod --json

backupx asset register mall-db --kind db \
  --project mall-admin --service api --environment prod \
  --repository local-prod --source prod-db
backupx asset register mall-files --kind file \
  --project mall-admin --service api --environment prod \
  --repository local-prod --alias prod-api --remote-path /srv/web/uploads
backupx asset list --project mall-admin --json
```

资产必须绑定已经登记的项目、服务、环境和仓库。数据库资产记录 `db_source`；文件资产记录 SSH alias 和绝对远端路径。使用 `--asset-id` 创建备份时，`backupx` 会补全来源和默认仓库，并把 project/service/environment、仓库和资产 ID 写入 manifest：

```bash
backupx db create --asset-id mall-db
backupx file create --asset-id mall-files
```

资产注册不会复制 `sshx`/`dbx` profile 或密码；旧 manifest 没有资产字段时仍可按原方式 list、inspect、verify 和 restore。

验证使用临时仓库和 fake `dbx`/`sshx`/`hostx`，不连接真实数据库或服务器。
